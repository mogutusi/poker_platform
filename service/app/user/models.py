from sqlmodel import Field, SQLModel
from typing import Optional
from pydantic import BaseModel
from sqlalchemy.orm import  mapped_column
from sqlalchemy import Column, String, DateTime
from datetime import datetime

from app.config import settings

class UserBase(SQLModel):
    name: str = Field(max_length=15,unique=True,nullable=False,index=True)

class User(UserBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    nickname: Optional[str] = Field(max_length=50,unique=True,default=None)
    hash_password: Optional[str] = Field(
        default=None,
        sa_column_kwargs = {
            "index": True,
            "deferred": True
        }
    )
    points: int = Field(default=0)
    refresh_token: Optional[str] = Field(
        default=None,
        sa_column_kwargs = {
            "deferred": True,
            "index": True
        }
    )
    refresh_token_expiry: Optional[datetime] = Field(
        default=None,
        sa_column_kwargs = {
            "deferred": True
        }
    )
    
class UserLogin(UserBase):
    password: str

class UserLoginResponse(BaseModel):
    access_token: str
    access_token_type: str = "Bearer"
    access_token_expiry: int = settings.ACCESS_TOKEN_EXPIRY
    refresh_token_expiry: int = settings.REFRESH_TOKEN_EXPIRY


class UserChangePassword(UserBase):
    old_password: str
    password: str

class UserChangePasswordResponse(BaseModel):
    token: str

class UserRead(UserBase):
    nickname: str
    points: int

class UserReadPagination(BaseModel):
    users: list[UserRead]
    total: int

class UserChangeNickname(UserBase):
    nickname: str

class UserChangeNicknameResponse(BaseModel):
    nickname: str