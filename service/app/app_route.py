from fastapi import APIRouter

from app.user.routes import user_route
from app.handrecord.routes import handrecord_route
from app.pokertable.routes import pokertable_route

app_route = APIRouter(prefix="/Texas/service")

app_route.include_router(user_route)
app_route.include_router(handrecord_route)
app_route.include_router(pokertable_route)