from fastapi import HTTPException
from datetime import datetime, timedelta
import jwt
from sqlmodel import select
from ttxsgm import sm3_hash

from app.database.core import DBsession
from app.user.models import User, UserLogin, UserChangePassword, UserChangeNickname
from app.config import settings


async def user_login(user: UserLogin, db: DBsession):
    db_user = await db.exec(select(User).where(User.name == user.name))
    db_user = db_user.first()
    if not db_user:
        raise HTTPException(status_code=401, detail="User not found")
    if not db_user.hash_password == sm3_hash(user.password):
        raise HTTPException(status_code=401, detail="Password is incorrect")
    token = jwt.encode({"sub": str(db_user.id)}, settings.JWT_SECRET, algorithm="HS256")
    return token

async def user_change_password(user: UserChangePassword, db: DBsession):
    db_user = await db.exec(select(User).where(User.name == user.name))
    db_user = db_user.first()
    if not db_user:
        raise HTTPException(status_code=401, detail="User not found")
    if not db_user.hash_password == sm3_hash(user.old_password):
        raise HTTPException(status_code=401, detail="Old password is incorrect")
    db_user.hash_password = sm3_hash(user.password)
    await db.commit()
    token = jwt.encode({"sub": str(db_user.id)}, settings.JWT_SECRET, algorithm="HS256")
    return token

async def user_read(db: DBsession):
    db_users = await db.exec(select(User).order_by(User.points.desc()))
    return db_users.all()

async def user_change_nickname(user: UserChangeNickname, db: DBsession):
    db_user = await db.exec(select(User).where(User.name == user.name))
    db_user = db_user.first()
    if not db_user:
        raise HTTPException(status_code=401, detail="User not found")
    nick_exist = await db.exec(select(User).where(User.nickname == user.nickname))
    nick_exist = nick_exist.first()
    if nick_exist:
        raise HTTPException(status_code=401, detail="Nickname already exists")
    db_user.nickname = user.nickname
    await db.commit()
    await db.refresh(db_user)
    return db_user.nickname