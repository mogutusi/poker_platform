from typing import Annotated, AsyncGenerator

from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import SQLModel,Field, select
from sqlalchemy.orm import sessionmaker

from fastapi import Depends, Request, HTTPException

from app.config import settings
from app.user.models import User


engine = create_async_engine(settings.DATABASE_URL, echo = True)

AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def get_session()-> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session

DBsession = Annotated[AsyncSession, Depends(get_session)]

# async def get_current_user(request: Request, db: DBsession):
#     user_id = request.state.user_id
#     if not user_id:
#         return None
#     user = await db.get(User, user_id)
#     if not user:
#         raise HTTPException(status_code=401, detail="Unauthorized")
#     return user

# CurrentUser = Annotated[User, Depends(get_current_user)]

# def db_init():
#     SQLModel.metadata.create_all(engine)
