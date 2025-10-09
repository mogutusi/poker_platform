from ttxsgm import sm3_hash
import asyncio
from typing import Annotated, AsyncGenerator
from sqlmodel.ext.asyncio.session import AsyncSession

from app.user.models import User
from app.database.core import get_session

async def qunyou_init():
    session_generator = get_session() 
    session = await session_generator.__anext__()

    name = ["wzx","wst","by","xr","lx","zzj","hfl","xzx","jsx"]
    if len(name) != 9:
        raise ValueError("群友一共9个！")
    for i,n in enumerate(name):
        user = User(name=n, hash_password=sm3_hash(n+sm3_hash(str(i))))
        print(n+sm3_hash(str(i)),n)
        session.add(user)
    await session.commit()

asyncio.run(qunyou_init())
