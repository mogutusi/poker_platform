from fastapi import WebSocket, WebSocketDisconnect
from typing import Dict, Optional, List
import asyncio
from sqlmodel import select


from app.pokertable.models import Player, Room, Hand, PlayerAction
from app.pokertable.services import process_action, only_room_name
from app.pokertable.enums import UserStatus, HandStatus, RoomStatus
from app.user.models import User
from app.pokertable.gameconfig import gameconfig
from app.database.core import DBsession, AsyncSessionLocal 
from app.pokertable.wsm_schemas import (HoleCardsMessage, parse_client_message, BroadcastTarget, PersonalTarget, serialize_server_message, UserOnlineMessage, UserOfflineMessage, 
    RoomStateMessage, UserStatusChangedMessage, UserLeaveRoomMessage)


class GameRoom:
    def __init__(self):
        # room_name -> room
        self.rooms: Dict[str, Room] = {}
        # room_name -> user_nickname -> websocket
        self.connections: Dict[str, Dict[str, WebSocket]] = {}
        # room_name -> user_nickname -> disconnect_task
        self.disconnect_tasks: Dict[str, Dict[str, asyncio.Task]] = {}

    async def connect(self, websocket: WebSocket, user_nickname: str,room_name: str):
        # validation
        only_room_name(room_name)
        await websocket.accept()
        if room_name not in self.rooms.keys():
            self.rooms[room_name] = Room(
                users_in_room={}, 
            )
            self.connections[room_name] = {}
        if self.rooms[room_name].status == RoomStatus.HAND_ENDED:
            raise GameLogicError(message="Hand ended, cannot join the room")
        if user_nickname in self.rooms[room_name].users_in_room.keys():
            if self.rooms[room_name].users_in_room[user_nickname] != UserStatus.OFFLINE:
                raise GameLogicError(message="User is already in the room")
            if user_nickname in self.disconnect_tasks[room_name].keys():
                self.disconnect_tasks[room_name][user_nickname].cancel()
                del self.disconnect_tasks[room_name][user_nickname]
                if self.rooms[room_name].disconnect_snapshot[user_nickname] != UserStatus.PLAYING:
                    self.rooms[room_name].users_in_room[user_nickname] = self.rooms[room_name].disconnect_snapshot[user_nickname]
                else:
                    if self.rooms[room_name].status == RoomStatus.HAND_STARTED:
                        for player in self.rooms[room_name].hand.players:
                            if player.nickname == user_nickname:
                                card_message = HoleCardsMessage(cards=player.hole_cards)
                                break
                        self.rooms[room_name].users_in_room[user_nickname] = UserStatus.PLAYING
                    else:
                        self.rooms[room_name].users_in_room[user_nickname] = UserStatus.SITTING_OUT
                del self.rooms[room_name].disconnect_snapshot[user_nickname]
        self.connections[room_name][user_nickname] = websocket
        message = UserOnlineMessage(nickname=user_nickname, user_status=self.rooms[room_name].users_in_room[user_nickname])
        await self.room_broadcast(message=serialize_server_message(message), room_name=room_name)
        message = RoomStateMessage(room=self.rooms[room_name])
        await self.room_broadcast(message=serialize_server_message(message), room_name=room_name)
        if card_message is not None:
            await self.send_personal_message(message=serialize_server_message(card_message), user_nickname=user_nickname, room_name=room_name)


    async def disconnect(self, room_name: str, user_nickname: str,db: DBsession):
        if room_name in self.rooms.keys():
            if user_nickname in self.rooms[room_name].users_in_room.keys():
                user_status = self.rooms[room_name].users_in_room[user_nickname]
                self.rooms[room_name].disconnect_snapshot[user_nickname] = user_status
                if user_status == UserStatus.PLAYING:
                    user_status = UserStatus.OFFLINE
                    del self.connections[room_name][user_nickname]
                    message = UserOfflineMessage(nickname=user_nickname)
                    await self.room_broadcast(message=serialize_server_message(message), room_name=room_name)
                    task = asyncio.create_task(self.delayed_cleanup(999999,room_name=room_name, user_nickname=user_nickname))
                    self.disconnect_tasks[room_name][user_nickname] = task
                elif user_status == UserStatus.READY_TO_PLAY or user_status == UserStatus.SITTING_IN:
                    for i in range(len(self.rooms[room_name].seats)):
                        if self.rooms[room_name].seats[i] is not None and self.rooms[room_name].seats[i].nickname == user_nickname:
                            seat_number = i
                            break
                    del self.connections[room_name][user_nickname]
                    user_status = UserStatus.OFFLINE
                    message = UserOfflineMessage(nickname=user_nickname)
                    await self.room_broadcast(message=serialize_server_message(message), room_name=room_name)
                    task = asyncio.create_task(self.delayed_cleanup(gameconfig.OFFLINE_DELAY_TIME,room_name=room_name, user_nickname=user_nickname, seat_number=seat_number))
                    self.disconnect_tasks[room_name][user_nickname] = task
                else:
                    del self.connections[room_name][user_nickname]
                    user_status = UserStatus.OFFLINE
                    message = UserOfflineMessage(nickname=user_nickname)
                    await self.room_broadcast(message=serialize_server_message(message), room_name=room_name)
                    task = asyncio.create_task(self.delayed_cleanup(gameconfig.OFFLINE_DELAY_TIME,room_name=room_name, user_nickname=user_nickname))
                    self.disconnect_tasks[room_name][user_nickname] = task
                    

    async def delayed_cleanup(self, delay_time: int, room_name: str, user_nickname: str, seat_number: Optional[int] = None):
        await asyncio.sleep(delay_time)
        if seat_number is not None:
            if self.rooms[room_name].seats[seat_number].points != 0:
                async with AsyncSessionLocal() as db:
                    statement = (
                        select(User)
                        .where(User.nickname == user_nickname)
                        .with_for_update()
                    )
                    result = await db.exec(statement)
                    user_db = result.one()
                    user_db.points += self.rooms[room_name].seats[seat_number].points
                    await db.commit()
            self.rooms[room_name].seats[seat_number] = None
        del self.rooms[room_name].users_in_room[user_nickname]
        del self.disconnect_tasks[room_name][user_nickname]
        del self.rooms[room_name].disconnect_snapshot[user_nickname]

        message = UserLeaveRoomMessage(nickname=user_nickname, leave_type="offline")
        await self.room_broadcast(message=serialize_server_message(message), room_name=room_name)

        if len(self.rooms[room_name].users_in_room) == 0:
            del self.rooms[room_name]
            del self.connections[room_name]
            del self.disconnect_tasks[room_name]
        

    async def room_broadcast(self, message: str, room_name: str):
        # Args: 
        # message: JSON string
        if room_name in self.connections:
            send_task = []
            for user_nickname, websocket in self.connections[room_name].items():
                send_task.append(websocket.send_text(message))
            await asyncio.gather(*send_task, return_exceptions=True)
    
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
            if room_name in game_room.rooms:
                if user_nickname not in game_room.rooms[room_name].users_in_room:
                    if room_name in game_room.connections:
                        game_room.connections[room_name].pop(user_nickname, None)
                    if len(game_room.rooms[room_name].users_in_room) == 0:
                        game_room.rooms.pop(room_name, None)
                        game_room.connections.pop(room_name, None)
                        game_room.disconnect_tasks.pop(room_name, None)
                    break
    except GameLogicError as e:
        if e.message == "Hand ended, clear offline players":
            for user_nickname, user_status in game_room.rooms[room_name].users_in_room.items():
                if user_status == UserStatus.OFFLINE and game_room.rooms[room_name].disconnect_snapshot[user_nickname] == UserStatus.PLAYING:
                    game_room.disconnect_tasks[room_name][user_nickname].cancel()
                    game_room.disconnect_tasks[room_name][user_nickname] = UserStatus.SITTING_OUT
                    await game_room.delayed_cleanup(gameconfig.OFFLINE_DELAY_TIME,room_name=room_name, user_nickname=user_nickname)
            game_room.rooms[room_name].status = RoomStatus.PENDING_START
        else:
            await websocket.send_text(serialize_server_message(ErrorMessage(error_code="GAME_LOGIC_ERROR", message=str(e))))
    except WebSocketDisconnect:
        await game_room.disconnect(room_name=room_name, user_nickname=user_nickname, db=db)