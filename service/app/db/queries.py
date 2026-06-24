# DB 读路径(查询;与写侧 orm_persister.py 分文件)。载入一次 / 不做实时判定的语义见 storage.md / db.md。

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from app.db.models import User


async def load_user_by_nick(
    sessionmaker: async_sessionmaker[AsyncSession], nick: str
) -> tuple[int, int] | None:
    # 按昵称读账号:返回 (uid, points) 供 JoinRoom 富化;无此行返回 None(调用方判内部不一致)。
    async with sessionmaker() as session:
        user = (await session.execute(select(User).where(User.nickname == nick))).scalar_one_or_none()
        return None if user is None else (user.id, user.points)
