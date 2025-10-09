from sqlmodel import Field, SQLModel
from typing import Optional

class UserBase(SQLModel):
    name: str = Field(max_length=15,unique=True,nullable=False)

class User(UserBase, table=True):
    id: int = Field(default=None, primary_key=True)
    nickname: str = Field(max_length=50,unique=True,default=None)
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

class UserChangeNickname(UserBase):
    nickname: str

class UserChangeNicknameResponse(SQLModel):
    nickname: str