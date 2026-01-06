from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.database.core import DBsession
from app.pokertable.websocket import handle_websocket

pokertable_route = APIRouter(prefix="/pokertable",tags=["pokertable"])

@pokertable_route.websocket("/room")
async def websocket_endpoint(websocket: WebSocket, room_id: str, user_nickname: str, db: DBsession):
    await handle_websocket(websocket=websocket, user_nickname=user_nickname, room_name=room_id, db=db)
    