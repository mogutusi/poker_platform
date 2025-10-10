from fastapi import APIRouter

from app.user.routes import user_route
from app.gamerecord.routes import gamerecord_route
from app.pokertable.routes import pokertable_route

app_route = APIRouter(prefix="/Texas/service")

app_route.include_router(user_route)
app_route.include_router(gamerecord_route)
app_route.include_router(pokertable_route)