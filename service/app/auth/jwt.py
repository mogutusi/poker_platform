import jwt
from typing import Annotated
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlmodel import select

from app.config import settings
from app.database.core import DBsession
from app.user.models import User

PasswordBearer = HTTPBearer(auto_error=False)

async def get_current_user_name(token: HTTPAuthorizationCredentials = Depends(PasswordBearer)):
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        payload = jwt.decode(token.credentials, settings.JWT_SECRET, algorithms=["HS256"])
        user_name = payload['sub']
        if not user_name:
            raise HTTPException(status_code=401, detail="Invalid token")
        return user_name
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except Exception as e:
        raise HTTPException(status_code=401, detail="Unauthorized")

CurrentUserName = Annotated[str, Depends(get_current_user_name)]

async def get_current_user_by_token(user_name: CurrentUserName, db: DBsession):
    db_user = await db.exec(select(User).where(User.name == user_name))
    db_user = db_user.first()
    if not db_user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return db_user

CurrentUser = Annotated[User, Depends(get_current_user_by_token)]

