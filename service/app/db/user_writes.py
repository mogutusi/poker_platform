# 鉴权列同步直写(P5/P7,见 docs/db.md「鉴权列写路径」/ changes/0064)。
#
# 这是 **delayDB 之外**的另一条 DB 写路径:`hash_password`/`name`/`k_cur`/`k_prev` 等鉴权列**不进内存**
# (user.md:不放 UserState)、**DB 即权威、无内存副本**,故不套用「内存权威 + delayDB 异步追平」——
# 改了要同步落库、commit 后才回成功(所见即所得;delayDB 的崩溃丢窗口对密码不可接受)。
#
# 不破「PersistWriter 是 delayDB 唯一写者」:本路径与 delayDB 正交。无锁前提 = **列不相交**:
# PersistWriter 的状态写是定向列 UPDATE(SET points,0028),本路径是 SET hash_password / nickname / k_*,
# 永不碰 points 列 ⇒ 无 lost-update、无需 FOR UPDATE(例外:issue_login 建**新行**带 points——该用户
# 必不在内存,PersistWriter 无从写它;详见 storage.md「鉴权列写路径」)。这些写全部极低频(人工/每周 cron)。
# 脱敏红线(log.md):hash_password/k_cur/k_prev 明文/摘要不进日志。

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import User


async def update_password_hash(
    sessionmaker: async_sessionmaker[AsyncSession], uid: int, new_hash: str
) -> None:
    # 同步改一个用户的密码哈希:请求级 session、定向列 UPDATE(只盖 hash_password,保其它列)、commit。
    # 按不可变 uid 定位(db.md:落库按 uid 不按可变 nickname)。行必存在(调用方先 load 过);0 命中无害。
    async with sessionmaker() as session:
        async with session.begin():
            await session.execute(update(User).where(User.id == uid).values(hash_password=new_hash))


async def rotate_kuser(
    sessionmaker: async_sessionmaker[AsyncSession],
    uid: int,
    new_key_hex: str,
    now: float,
    rotation_seconds: float,
    grace_seconds: float,
) -> int | None:
    # K_user 轮换(changes/0066 决策 6):单条 UPDATE 列到列搬移——SET 右值取**旧行值**(SQL 标准语义,
    # sqlite/pg 一致),故「k_cur 降为 k_prev + 新钥上位 + 版本 +1 + 重排到期」原子完成,无读-改-写窗口;
    # 与并发登录读互不半见,与 PersistWriter 列不相交(SET k_* vs SET points)⇒ 沿无锁前提。
    # WHERE 钉 k_cur IS NOT NULL:未发钥的行不可轮换(轮换语义是「换」不是「发」,首发走 issue_login)→ None。
    # 版本 +1 用 coalesce 兜 NULL ver(SQL 里 NULL+1 = NULL 会**静默**把版本抹掉,不会报错;正常路径
    # issue/迁移回填都成对盖 ver,coalesce 只兜「k_cur 有、ver 丢」的脏行 → 视作 0 起重新计数)。
    # 返回新版本号,经 RETURNING 与 UPDATE 同语句取回(sqlite ≥3.35 / pg 均支持)——**不做 commit 后
    # 二次回读**:密钥唯一导出点是调用方 stdout,commit 后再多一次可失败的往返 = 密钥已换却没导出的窗口
    # (0066 自 review 抓修)。
    async with sessionmaker() as session:
        async with session.begin():
            result = await session.execute(
                update(User)
                .where(User.id == uid, User.k_cur.is_not(None))
                .values(
                    k_prev=User.k_cur,
                    k_prev_ver=User.k_cur_ver,
                    k_prev_until=now + grace_seconds,
                    k_cur=new_key_hex,
                    k_cur_ver=func.coalesce(User.k_cur_ver, 0) + 1,
                    k_cur_until=now + rotation_seconds,
                )
                .returning(User.k_cur_ver)
            )
            new_ver = result.scalar_one_or_none()
            return None if new_ver is None else int(new_ver)


async def issue_login(
    sessionmaker: async_sessionmaker[AsyncSession],
    name: str,
    nickname: str,
    password_hash: str,
    key_hex: str,
    now: float,
    rotation_seconds: float,
    points: int,
    reset: bool,
) -> tuple[int | None, str | None]:
    # 首发/补发登录凭证(changes/0066 决策 5;auth.md「首发/强制轮换」):按 name 定位或新建 User 行,
    # 盖 hash_password + k_cur + 排程 k_cur_until,并清空 k_prev(补发即强制换代,旧钥不留宽限——
    # 补发多因疑似泄露/丢失,留旧钥反而留洞)。Go 风格返回 (新版本号, None)=成功 /(None, 拒绝原因)——
    # 版本必须如实回给 CLI 打印(首发=1、--reset 补发=旧 ver+1,硬报 v1 会误导管理员对账)。
    # 已启用(k_cur 非 NULL)须 reset=True 才覆盖(防手滑把在用密钥重置掉);name 不存在则建新行
    # (nickname 撞唯一约束由调用方兜 IntegrityError)。单事务、同步直写(鉴权列 DB 权威,storage.md)。
    async with sessionmaker() as session:
        async with session.begin():
            user = (await session.execute(select(User).where(User.name == name))).scalar_one_or_none()
            if user is None:
                # name 未占:优先复用同 nickname 的既有行(pre-P5 行 login-enable),否则建新行。
                user = (
                    await session.execute(select(User).where(User.nickname == nickname))
                ).scalar_one_or_none()
                if user is not None and user.name is not None:
                    return None, f"nickname {nickname!r} belongs to account {user.name!r}"
                if user is None:
                    user = User(nickname=nickname, points=points)
                    session.add(user)
                user.name = name
            elif user.k_cur is not None and not reset:
                return None, f"account {name!r} already issued (ver={user.k_cur_ver}); use --reset to overwrite"
            user.hash_password = password_hash
            user.k_cur = key_hex
            user.k_cur_ver = 1 if user.k_cur_ver is None else user.k_cur_ver + 1  # 补发也换代,版本单调
            user.k_cur_until = now + rotation_seconds
            user.k_prev = None
            user.k_prev_ver = None
            user.k_prev_until = None
            return user.k_cur_ver, None


async def update_nickname(
    sessionmaker: async_sessionmaker[AsyncSession], uid: int, old_nick: str, new_nick: str
) -> bool:
    # 同步改一个用户的昵称(仅大厅可调,见 rest.md/changes/0065):**CAS** 定向 UPDATE、commit。
    # WHERE 同时钉 uid 与 old_nick(compare-and-swap):同账号并发双改名时输者 0 命中 → False,
    # 调用方回 409 且**跳过内存联动**——否则 DB/会话表/连接键三处会各随一个赢家、永久发散(0065 自 review 抓修)。
    # 撞名(唯一约束)在 commit 抛 IntegrityError,由调用方兜 409(预查 + 约束双保险)。
    async with sessionmaker() as session:
        async with session.begin():
            result = await session.execute(
                update(User).where(User.id == uid, User.nickname == old_nick).values(nickname=new_nick)
            )
            return (result.rowcount or 0) > 0
