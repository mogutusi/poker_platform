from fastapi import WebSocket, WebSocketDisconnect
from typing import Dict, Optional


from app.pokertable.models import Player, Room, Hand, PlayerAction
from app.pokertable.services import process_action, only_room_name
from app.pokertable.enums import UserStatus, HandStatus, RoomStatus
from app.user.models import User
from app.database.core import DBsession
from app.pokertable.wsm_schemas import parse_client_message, BroadcastTarget, PersonalTarget, serialize_server_message, UserOnlineMessage, UserOfflineMessage, RoomStateMessage

class GameRoom:
    def __init__(self):
        # room_name -> room
        self.rooms: Dict[str, Room] = {}
        # room_name -> user_nickname -> websocket
        self.connections: Dict[str, Dict[str, WebSocket]] = {}

    async def connect(self, websocket: WebSocket, user_nickname: str,room_name: str):
        # validation
        only_room_name(room_name)
        await websocket.accept()
        if room_name not in self.rooms.keys():
            self.rooms[room_name] = Room(
                users_in_room={}, 
            )
            self.connections[room_name] = {}
        # if user_nickname in self.rooms[room_name].users_in_room.keys():
        #     if self.rooms[room_name].users_in_room[user_nickname].user_status != UserStatus.OFFLINE:
        #         raise GameLogicError(message="User is already in the room")
        #     if self.rooms[room_name].hand is not None:
        #         for player in self.rooms[room_name].hand.players:
        #             if player.nickname == user_nickname:
        #                 self.rooms[room_name].users_in_room[user_nickname].user_status = UserStatus.WATCHING
        #                 break
        self.connections[room_name][user_nickname] = websocket
        self.rooms[room_name].users_in_room[user_nickname] = UserStatus.READY_TO_WATCH
        message = UserOnlineMessage(nickname=user_nickname, user_status=UserStatus.READY_TO_WATCH)
        await self.room_broadcast(message=serialize_server_message(message), room_name=room_name)
        message = RoomStateMessage(room=self.rooms[room_name])
        await self.room_broadcast(message=serialize_server_message(message), room_name=room_name)

    async def disconnect(self, room_name: str, user_nickname: str,db: DBsession):
        if room_name in self.room.keys():
            if user_nickname in self.rooms[room_name].users_in_room.keys():
                player = self.rooms[room_name].users_in_room[user_nickname]
                if player.user_status == UserStatus.PLAYING or player.user_status == UserStatus.WATCHING:
                    player.user_status = UserStatus.OFFLINE
                    message = UserOfflineMessage(nickname=user_nickname)
                    await self.room_broadcast(message=serialize_server_message(message), room_name=room_name)
                else:
                    del self.rooms[room_name].users_in_room[user_nickname]
                    del self.connections[room_name][user_nickname]
                    message = UserOfflineMessage(nickname=user_nickname)
                    await self.room_broadcast(message=serialize_server_message(message), room_name=room_name)

            if len(self.rooms[room_name].users_in_room) == 0:
                # settle up
                
                del self.rooms[room_name]
                del self.connections[room_name]

    async def room_broadcast(self, message: str, room_name: str):
        # Args: 
        # message: JSON string
        if room_name in self.connections:
            for user_nickname, websocket in self.connections[room_name].items():
                await websocket.send_text(message)

    async def send_personal_message(self, message: str, user_nickname: str, room_name: str):
        # Args: 
        # message: JSON string
        if room_name in self.connections and user_nickname in self.connections[room_name]:
            await self.connections[room_name][user_nickname].send_text(message)


game_room = GameRoom()


async def handle_websocket(websocket: WebSocket, user_nickname: str, room_name: str, db: DBsession):
    await game_room.connect(websocket=websocket, user_nickname=user_nickname, room_name=room_name)

    try:
        while True:
            data = await websocket.receive_text()
            client_message = parse_client_message(data)
            async for message in process_action(
                room = game_room.rooms[room_name],
                message = client_message, 
                user_nickname=user_nickname, 
                room_name=room_name, 
                db=db
            ):
                match message:
                    case BroadcastTarget(message=message):
                        await game_room.room_broadcast(message=serialize_server_message(message), room_name=room_name)
                    case PersonalTarget(nickname=nickname, message=message):
                        await game_room.send_personal_message(message=serialize_server_message(message), user_nickname=nickname, room_name=room_name)
    except GameLogicError as e:
        await websocket.send_text(serialize_server_message(ErrorMessage(error_code="GAME_LOGIC_ERROR", message=str(e))))
    except WebSocketDisconnect:
        await game_room.disconnect(room_name=room_name, user_nickname=user_nickname)