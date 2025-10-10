from fastapi import WebSocket
from sqlmodel import SQLModel
from typing import List, Dict
import json

def check_room_name(room_id: str):
    room_name = ("room1")
    if room_id not in room_name:
        raise ValueError("Invalid room name")

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {} 
        
    async def connect(self, websocket: WebSocket, room_id: str):
        check_room_name(room_id)
        await websocket.accept()
        if room_id not in self.active_connections:
            self.active_connections[room_id] = []
        self.active_connections[room_id].append(websocket)

    def disconnect(self, websocket: WebSocket, room_id: str):
        if room_id in self.active_connections:
            self.active_connections[room_id].remove(websocket)
            if len(self.active_connections[room_id]) == 0:
                del self.active_connections[room_id]

    async def send_personal_message(self, message: dict, websocket: WebSocket):
        await websocket.send_text(json.dumps(message))

    async def broadcast(self, message: dict, room_id: str):
        if room_id in self.active_connections:
            for connection in self.active_connections[room_id]:
                await connection.send_text(json.dumps(message))


connection_manager = ConnectionManager()