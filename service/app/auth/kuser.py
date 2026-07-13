# K_user 双钥轮换编排(P5,见 docs/auth.md §K_user 每周轮换 / changes/0066)。生成密钥/口令 + 把「查 due →
# 逐个生成新钥 → 写库」串起来,供管理员 CLI(scripts/kuser_admin.py)调用;服务器进程不跑轮换——新钥必须
# 带外下发,只在管理员终端 stdout 出现一次(K_user 任何级别不进日志,log.md 红线)。
# 天数换算秒的常量在此(配置以「天」计,DB 排程以 epoch 秒计,见 models.py *_until 注)。

import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.queries import users_due_for_rotation
from app.db.user_writes import rotate_kuser

SECONDS_PER_DAY = 86400  # 天 → 秒(KUSER_ROTATION_DAYS/GRACE_DAYS 是「天」旋钮,DB 排程存 epoch 秒)

_KUSER_BYTES = 16  # SM4 128-bit 密钥长度(与 credentials._KEY_BYTES 同源约束)
_PASSWORD_BYTES = 12  # 首发随机口令熵源字节数(urlsafe 后 16 字符,高熵、可手输)


def generate_kuser() -> str:
    # 全新随机 K_user(hex)。轮换的意义系于「新钥不可由旧钥推出」——必须 CSPRNG 全新生成,
    # 绝不从旧钥派生/经信道下发(auth.md:链式派生会让拿到旧钥者顺藤摸到新钥)。
    return secrets.token_bytes(_KUSER_BYTES).hex()


def generate_password() -> str:
    # 首发高熵随机口令(auth.md:初始密码由管理员生成、私下发给用户;用户可自行改密)。
    return secrets.token_urlsafe(_PASSWORD_BYTES)


@dataclass(frozen=True)
class RotatedKey:
    # 一次轮换的产出:交给 CLI 打到管理员终端、由管理员带外发给用户(这是新钥唯一的导出点)。
    name: str  # 登录账号
    version: int  # 新钥版本号(= 旧 ver + 1;管理员对账用)
    new_key_hex: str  # 全新 K_user(hex)——秘密,只出现在 CLI stdout,不进日志/不落文件

    def __repr__(self) -> str:  # 防 dataclass 默认 repr 把密钥带进异常栈/调试输出(脱敏红线)
        return f"RotatedKey(name={self.name!r}, version={self.version}, new_key_hex=<redacted>)"


@dataclass(frozen=True)
class RotationFailure:
    # 一个账号轮换失败的通报(交 CLI 打 stderr;不带密钥——失败即未 commit,无钥可泄)。
    name: str  # 登录账号
    error: str  # 异常摘要(repr;rotate_kuser 入参无秘密,异常文本不触脱敏红线)


async def rotate_one(
    sessionmaker: async_sessionmaker[AsyncSession],
    uid: int,
    name: str,
    now: float,
    rotation_days: int,
    grace_days: int,
) -> RotatedKey | None:
    # 轮换单个账号:生成全新钥 → 原子搬移 + 同语句 RETURNING 取回新版本(rotate_kuser)→ 返回新钥供带外下发。
    # commit 即返回、无 commit 后二次往返——「密钥已换但没导出」的窗口收敛为零(0066 自 review 抓修)。
    # None = 该行不可轮换(k_cur 为 NULL,未发钥——首发走 issue,不走轮换)。
    new_key = generate_kuser()
    new_ver = await rotate_kuser(
        sessionmaker, uid, new_key, now, rotation_days * SECONDS_PER_DAY, grace_days * SECONDS_PER_DAY
    )
    if new_ver is None:
        return None
    return RotatedKey(name=name, version=new_ver, new_key_hex=new_key)


async def rotate_due(
    sessionmaker: async_sessionmaker[AsyncSession], now: float, rotation_days: int, grace_days: int
) -> AsyncIterator[RotatedKey | RotationFailure]:
    # 轮换任务本体(cron 周期跑):挑出 k_cur_until <= now 的账号逐个轮换,**逐个产出**——
    # 调用方(CLI)每收到一把 RotatedKey 须立即打印:密钥已 commit 进 DB 而 stdout 是唯一导出点,
    # 攒到批尾才打会让「后续账号失败/进程被杀」吞掉已换未导的密钥(用户宽限期后锁死,重跑不补——
    # 该账号已不再 due;0066 自 review 抓修)。单账号失败 try/except 兜住、产出 RotationFailure 继续,
    # 真正做到「一个失败不拖累其余」;失败者未 commit、下次 due 仍在,重跑即补。
    for uid, name in await users_due_for_rotation(sessionmaker, now):
        try:
            result = await rotate_one(sessionmaker, uid, name, now, rotation_days, grace_days)
        except Exception as exc:  # 单账号事务失败(DB 瞬断等):通报并继续,不拖累其余账号
            yield RotationFailure(name=name, error=repr(exc))
            continue
        if result is not None:  # None = due 查询后、轮换前 k_cur 被并发清掉(理论窗),视同跳过
            yield result
