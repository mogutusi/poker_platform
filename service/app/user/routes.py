from fastapi import APIRouter, Response


from app.database.core import DBsession
from app.user.models import UserLogin, UserLoginResponse, UserChangePassword,  UserChangePasswordResponse, UserReadPagination, UserChangeNickname, UserChangeNicknameResponse 
from app.user.services import user_login, user_change_password, user_read, user_change_nickname
from app.auth.routes import auth_route

user_route = APIRouter(prefix="/user",tags=["user"])

@user_route.post("/login",response_model=UserLoginResponse)
async def login(user: UserLogin,response: Response, db: DBsession) -> UserLoginResponse:
    access_token, refresh_token = await user_login(user, db)
    response.set_cookie(
        key="refresh_token", 
        value=refresh_token,
        httponly=True, 
        # secure=True, 
        same_site="lax",
        path=f"{auth_route.prefix}/refresh"
    )

    return UserLoginResponse(access_token=access_token)

@user_route.post("/change_password",response_model=UserChangePasswordResponse)
async def change_password(user: UserChangePassword, db: DBsession) -> UserLoginResponse:
    token = await user_change_password(user, db)
    return UserChangePasswordResponse(token=token)

@user_route.get("/read",response_model=UserReadPagination)
async def read(db: DBsession) -> UserReadPagination:
    users = await user_read(db)
    return UserReadPagination(users=users, total=len(users))

@user_route.post("/change_nickname",response_model=UserChangeNicknameResponse)
async def change_nickname(user: UserChangeNickname, db: DBsession) -> UserChangeNicknameResponse:
    nickname = await user_change_nickname(user, db)
    return UserChangeNicknameResponse(nickname=nickname)