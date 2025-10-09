from sqlmodel import Field, SQLModel
from datetime import datetime
from typing import Optional

class UserBase(SQLModel):
    name: str = Field(max_length=50,unique=True)

class User(UserBase, table=True):
    id: int = Field(default=None, primary_key=True)
    hash_password: Optional[str] = Field(default=None)
    points: int = Field(default=0)

class UserLogin(UserBase):
    password: str

class UserLoginResponse(SQLModel):
    token: str

class UserChangePassword(UserBase):
    old_password: str
    password: str

class UserChangePasswordResponse(SQLModel):
    token: str

class UserRead(UserBase):
    points: int

class UserReadPagination(SQLModel):
    users: list[UserRead]
    total: int

