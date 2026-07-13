#!/usr/bin/env python3
# K_user 管理员 CLI(P5 双钥轮换,见 docs/auth.md §K_user 每周轮换 / changes/0066)。
#
# 轮换任务落在这里(管理员侧 cron,不做进程内调度):新钥必须**带外下发**——本工具把新钥/新口令打到
# 管理员终端 stdout(这是密钥唯一的导出点,由管理员私发给用户),服务器进程与日志全程不见密钥。
# 直连 DB(经 app.config settings 的 DATABASE_URL,与 alembic 同源),与运行中的服务并存安全:
# 单行短事务、与 PersistWriter 列不相交,轮换不影响已建会话(会话密钥派生自 session_token)。
#
# 用法(工作目录 service/,venv 内):
#   .venv/bin/python scripts/kuser_admin.py list                   # 密钥记账视图(版本/排程;不含密钥)
#   .venv/bin/python scripts/kuser_admin.py rotate                 # 轮换全部到期账号(cron 周期跑,幂等)
#   .venv/bin/python scripts/kuser_admin.py rotate --name alice    # 强制轮换指定账号(疑似泄露等,无视排程)
#   .venv/bin/python scripts/kuser_admin.py issue --name alice [--nickname Alice] [--points 0] [--reset]
#                                                                  # 首发/补发:生成口令 + K_user v1(补发须 --reset)
# 生产连 Postgres:DATABASE_URL=postgresql+psycopg://… 前缀环境变量(同 alembic,见 docs/db-migrations.md)。
# ⚠ stdout 即带外通道起点:只在私密终端跑,勿重定向进会入 git/日志采集的文件(dev.md 秘密零容忍)。

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # service/ 根,使 `app` 可导入

from sqlalchemy.exc import IntegrityError

from app import gameconfig
from app.auth.kuser import (
    SECONDS_PER_DAY,
    RotatedKey,
    generate_kuser,
    generate_password,
    rotate_due,
    rotate_one,
)
from app.auth.passwords import hash_password
from app.db.engine import make_engine, make_sessionmaker
from app.db.queries import list_login_users, load_identity_by_name
from app.db.user_writes import issue_login


def _fmt_ts(epoch: float | None) -> str:
    # epoch 秒 → 可读 UTC 时刻(记账展示;None = 未排程/无宽限)。
    if epoch is None:
        return "-"
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


async def _cmd_list() -> int:
    engine = make_engine()
    try:
        rows = await list_login_users(make_sessionmaker(engine))
    finally:
        await engine.dispose()
    now = time.time()
    print(f"{'name':<16} {'nickname':<20} {'ver':>3} {'rotate due':<20} {'prev grace until':<20}")
    for u in rows:
        due_mark = " (due)" if u.k_cur_until is not None and u.k_cur_until <= now else ""
        print(
            f"{u.name:<16} {u.nickname:<20} {u.k_cur_ver if u.k_cur_ver is not None else '-':>3} "
            f"{_fmt_ts(u.k_cur_until) + due_mark:<20} {_fmt_ts(u.k_prev_until) if u.has_prev else '-':<20}"
        )
    print(f"({len(rows)} login-enabled account(s))")
    return 0


def _print_rotated(r: RotatedKey) -> None:
    # 密钥唯一导出点(仅管理员终端);flush 立即落终端——commit 后一毫秒都不多留在进程内存里。
    print(f"  {r.name}: K_user v{r.version} = {r.new_key_hex}", flush=True)


async def _cmd_rotate(name: str | None) -> int:
    engine = make_engine()
    try:
        sm = make_sessionmaker(engine)
        now = time.time()
        rotation, grace = gameconfig.KUSER_ROTATION_DAYS, gameconfig.KUSER_GRACE_DAYS
        if name is None:
            # cron 路:只轮到期者(幂等)。**边轮边打**:每把密钥 commit 即打印——密钥唯一导出点是
            # 本 stdout,攒到批尾才打会让后续失败吞掉已换未导的密钥(0066 自 review 抓修);
            # 单账号失败打 stderr 并继续(未 commit,下次 due 仍在、重跑即补),退出码 1 提醒管理员。
            print(f"rotating due accounts; deliver new keys out-of-band (old keys valid "
                  f"{grace} more day(s)):", flush=True)
            rotated = failed = 0
            async for item in rotate_due(sm, now, rotation, grace):
                if isinstance(item, RotatedKey):
                    _print_rotated(item)
                    rotated += 1
                else:
                    print(f"error: rotate {item.name!r} failed: {item.error}; will retry next run",
                          file=sys.stderr)
                    failed += 1
            print(f"rotated {rotated} account(s), {failed} failed"
                  if rotated or failed else "nothing due for rotation")
            return 1 if failed else 0
        # 强制路:指定账号,无视排程(只需 uid,不载秘密列)。
        identity = await load_identity_by_name(sm, name)
        if identity is None:
            print(f"error: no such account {name!r}", file=sys.stderr)
            return 1
        result = await rotate_one(sm, identity[0], name, now, rotation, grace)
        if result is None:
            print(f"error: account {name!r} has no key yet; use `issue` first", file=sys.stderr)
            return 1
        print(f"rotated {name}; deliver new key out-of-band (old key valid {grace} more day(s)):")
        _print_rotated(result)
        return 0
    finally:
        await engine.dispose()


_NAME_MAX_LEN = 15  # 登录账号上限(对齐 db.models.User.name max_length)
_NICKNAME_MAX_LEN = 50  # 游戏昵称上限(对齐 db.models.User.nickname max_length)


async def _cmd_issue(name: str, nickname: str | None, points: int, reset: bool) -> int:
    nickname = nickname if nickname is not None else name
    # 入参形制在 CLI 就拒(sqlite 不强制 VARCHAR 长度,别让脏行进库):长度对齐列上限;
    # 首尾空白拒(" Bob" 与 "Bob" 视觉同名键不同 = 冒充面,承 0065)。
    if not name or name != name.strip() or len(name) > _NAME_MAX_LEN:
        print(f"error: name must be 1..{_NAME_MAX_LEN} chars, no leading/trailing spaces", file=sys.stderr)
        return 1
    if not nickname or nickname != nickname.strip() or len(nickname) > _NICKNAME_MAX_LEN:
        print(f"error: nickname must be 1..{_NICKNAME_MAX_LEN} chars, no leading/trailing spaces", file=sys.stderr)
        return 1
    new_password = generate_password()
    new_key = generate_kuser()
    engine = make_engine()
    try:
        sm = make_sessionmaker(engine)
        try:
            version, refusal = await issue_login(
                sm,
                name=name,
                nickname=nickname,
                password_hash=hash_password(new_password, gameconfig.PWD_HASH_ROUNDS),
                key_hex=new_key,
                now=time.time(),
                rotation_seconds=gameconfig.KUSER_ROTATION_DAYS * SECONDS_PER_DAY,
                points=points,
                reset=reset,
            )
        except IntegrityError:  # nickname 撞唯一约束(已属他人)
            print("error: nickname already taken (choose --nickname)", file=sys.stderr)
            return 1
    finally:
        await engine.dispose()
    if refusal is not None:
        print(f"error: {refusal}", file=sys.stderr)
        return 1
    print(f"issued {name}; deliver out-of-band:")  # 口令+密钥唯一导出点(仅管理员终端)
    print(f"  password = {new_password}")
    print(f"  K_user v{version} = {new_key}")  # 版本如实(首发=1、--reset 补发=旧+1),供管理员对账
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="K_user issuance & weekly rotation (docs/auth.md)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="show key versions & schedules (no key material)")
    p_rotate = sub.add_parser("rotate", help="rotate all due accounts, or --name to force one")
    p_rotate.add_argument("--name", help="force-rotate this account regardless of schedule")
    p_issue = sub.add_parser("issue", help="first-issue (or --reset re-issue) login credentials")
    p_issue.add_argument("--name", required=True, help="login account (immutable, <=15 chars)")
    p_issue.add_argument("--nickname", help="game nickname for a new row (default: same as name)")
    p_issue.add_argument("--points", type=int, default=0, help="starting points for a new row (default 0)")
    p_issue.add_argument("--reset", action="store_true", help="overwrite an already-issued account")
    args = parser.parse_args(argv)
    if args.command == "list":
        return asyncio.run(_cmd_list())
    if args.command == "rotate":
        return asyncio.run(_cmd_rotate(args.name))
    return asyncio.run(_cmd_issue(args.name, args.nickname, args.points, args.reset))


if __name__ == "__main__":
    sys.exit(main())
