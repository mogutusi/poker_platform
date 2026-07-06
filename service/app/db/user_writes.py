# 鉴权列同步直写(P5/P7,见 docs/db.md「鉴权列写路径」/ changes/0064)。
#
# 这是 **delayDB 之外**的另一条 DB 写路径:`hash_password`/`name`/`k_user` 等鉴权列**不进内存**
# (user.md:不放 UserState)、**DB 即权威、无内存副本**,故不套用「内存权威 + delayDB 异步追平」——
# 改了要同步落库、commit 后才回成功(所见即所得;delayDB 的崩溃丢窗口对密码不可接受)。
#
# 不破「PersistWriter 是 delayDB 唯一写者」:本路径与 delayDB 正交。无锁前提 = **列不相交**:
# PersistWriter 的状态写是定向列 UPDATE(SET points,0028),本路径是 SET hash_password,两写永不碰同一列
# ⇒ 无 lost-update、无需 FOR UPDATE。改密码极低频,dev sqlite 库级串行 / 生产 pg 行级 MVCC 足矣。
# 脱敏红线(log.md):hash_password 明文/摘要不进日志。

from sqlalchemy import update
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
