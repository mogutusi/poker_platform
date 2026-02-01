from fastapi import HTTPException
from sqlmodel import select
from datetime import datetime, timedelta, timezone
import jwt
import secrets
import asyncio
from typing import Dict, Tuple, Optional
from collections import defaultdict


from app.database.core import DBsession
from app.user.models import User
from app.config import settings


_refresh_token_pool: Dict[str, Tuple[str, str]] = {}
_user_locks: Optional[Dict[str, asyncio.Lock]] = defaultdict(asyncio.Lock)

async def add_refresh_token_to_pool(user_name: str,refresh_token: str,new_refresh_token: str):
    _refresh_token_pool[user_name] = (refresh_token, new_refresh_token)
    asyncio.create_task(remove_refresh_token_from_pool(user_name))

async def remove_refresh_token_from_pool(user_name: str):
    await asyncio.sleep(settings.REFRESH_TOKEN_POOL_EXPIRY)
    _refresh_token_pool.pop(user_name, None)

def create_access_token(user_name: str) -> str:
    return jwt.encode(
        payload={
            "sub": user_name,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRY)
        },
        key=settings.JWT_SECRET,
        algorithm="HS256",
    )

async def user_refresh(user_name: str, refresh_token: str, db: DBsession):
    temp_refresh_token = _refresh_token_pool.get(user_name)
    if temp_refresh_token:
        if temp_refresh_token[0] == refresh_token:
            access_token = create_access_token(user_name)
            return access_token, temp_refresh_token[1]
    
    async with _user_locks[user_name]:
        temp_refresh_token = _refresh_token_pool.get(user_name)
        if temp_refresh_token:
            if temp_refresh_token[0] == refresh_token:
                access_token = create_access_token(user_name)
                return access_token, temp_refresh_token[1]

        statement = (
            select(User)
            .where(User.name == user_name)
            .where(User.refresh_token == refresh_token)
            .where(User.refresh_token_expiry > datetime.now(timezone.utc))
            .with_for_update()
        )
        db_user = await db.exec(statement)
        db_user = db_user.first()
        if not db_user:
            raise HTTPException(status_code=401, detail="Refresh token is invalid")
        new_refresh_token = secrets.token_urlsafe(32)
        db_user.refresh_token = new_refresh_token
        db_user.refresh_token_expiry = datetime.now(timezone.utc) + timedelta(minutes=settings.REFRESH_TOKEN_EXPIRY)
        await db.commit()
        await add_refresh_token_to_pool(user_name, refresh_token, new_refresh_token)
        access_token = create_access_token(db_user.name)
        return access_token, new_refresh_token

