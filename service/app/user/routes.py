from fastapi import APIRouter


from app.database.core import DBsession
from app.user.models import UserLogin, UserLoginResponse, UserChangePassword,  UserChangePasswordResponse
from app.user.services import user_login, user_change_password

user_route = APIRouter(prefix="/user")

@user_route.post("/login")
def login(user: UserLogin, db: DBsession) -> UserLoginResponse:
    token = user_login(user, db)
    return UserLoginResponse(token=token)

@user_route.post("/change_password")
def change_password(user: UserLogin, db: DBsession) -> UserLoginResponse:
    token = user_change_password(user, db)
    return UserChangePasswordResponse(token=token)