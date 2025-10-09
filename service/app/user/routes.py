from fastapi import APIRouter


from app.database.core import DBsession
from app.user.models import UserLogin, UserLoginResponse, UserChangePassword,  UserChangePasswordResponse, UserReadPagination
from app.user.services import user_login, user_change_password, user_read

user_route = APIRouter(prefix="/user",tags=["user"])

@user_route.post("/login")
async def login(user: UserLogin, db: DBsession) -> UserLoginResponse:
    token = await user_login(user, db)
    return UserLoginResponse(token=token)

@user_route.post("/change_password")
async def change_password(user: UserChangePassword, db: DBsession) -> UserLoginResponse:
    token = await user_change_password(user, db)
    return UserChangePasswordResponse(token=token)

@user_route.get("/read")
async def read(db: DBsession) -> UserReadPagination:
    users = await user_read(db)
    return UserReadPagination(users=users, total=len(users))