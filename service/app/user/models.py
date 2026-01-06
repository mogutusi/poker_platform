from sqlmodel import Field, SQLModel
from typing import Optional
from pydantic import BaseModel
from sqlalchemy.orm import deferred, mapped_column
from sqlalchemy import Column, String

class UserBase(SQLModel):
    name: str = Field(max_length=15,unique=True,nullable=False)

class User(UserBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    nickname: Optional[str] = Field(max_length=50,unique=True,default=None)
    hash_password: Optional[str] = mapped_column(
        String,
        deferred=True
    )
    points: int = Field(default=0)

class UserLogin(UserBase):
    password: str

class UserLoginResponse(BaseModel):
    token: str

class UserChangePassword(UserBase):
    old_password: str
    password: str

class UserChangePasswordResponse(BaseModel):
    token: str

class UserRead(UserBase):
    points: int

class UserReadPagination(BaseModel):
    users: list[UserRead]
    total: int

class UserChangeNickname(UserBase):
    nickname: str

class UserChangeNicknameResponse(BaseModel):
    nickname: str