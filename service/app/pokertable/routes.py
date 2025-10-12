from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.database.core import DBsession
from app.pokertable.websocket import game_room

pokertable_route = APIRouter(prefix="/pokertable",tags=["pokertable"])

@pokertable_route.websocket("/room")
async def websocket_endpoint(websocket: WebSocket, room_id: str, db: DBsession):
    await game_room.connect(websocket, room_id, db)
    try:
        while True:
            data = await websocket.receive_text()
            await game_room.broadcast(data, room_id)
    except WebSocketDisconnect:
        await game_room.disconnect(websocket, room_id, db)