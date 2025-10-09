from fastapi import APIRouter
from app.user.routes import user_route

app_route = APIRouter(prefix="/Texas/service")

app_route.include_router(user_route)