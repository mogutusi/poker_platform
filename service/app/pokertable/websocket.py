from fastapi import WebSocket
from typing import Dict
import json
from sqlmodel import select
from datetime import datetime

from app.pokertable.models import Round, UserInRoom, Player, RoomRecord
from app.pokertable.services import only_room_name, get_blind
from app.pokertable.enum import RoundStatus, UserStatus
from app.user.models import User
from app.database.core import DBsession
from app.gamerecord.models import record_players, GameRecord

class GameRoom:
    def __init__(self):
        # room_name -> round
        self.round_status: Dict[str, Round] = {}
        # room_name -> user_nickname -> users_in_room
        self.user_in_room: Dict[str, Dict[str, UserInRoom]] = {}
        # room_name -> room_record
        self.room_record: Dict[str, RoomRecord] = {}

    async def connect(self, websocket: WebSocket, user_name: str,room_name: str):
        only_room_name(room_name)
        await websocket.accept()
        if room_name not in self.user_in_room:
            self.user_in_room[room_name] = {}
            self.room_record[room_name] = RoomRecord()
            self.round_status[room_name] = Round(status=RoundStatus.PENDING_START, last_blind=[])
        self.user_in_room[room_name][user_name] = UserInRoom(websocket=websocket)

    async def disconnect(self, room_name: str, user_name: str,db: DBsession):
        if room_name in self.user_in_room:
            for nickname, user in self.user_in_room[room_name].items():
                if nickname == user_name:
                    if user.user_status == UserStatus.PLAYING:
                        user.user_status = UserStatus.OFFLINE
                    elif user.user_status == UserStatus.WATCHING:
                        user.user_status = UserStatus.OFFLINE
                    else:
                        del self.user_in_room[room_name][nickname]
                    break
            if len(self.user_in_room[room_name]) == 0:
                if self.room_record[room_name].game_start_time is not None:
                    self.room_record[room_name].game_end_time = datetime.now()
                game_record = GameRecord.model_validate(self.room_record[room_name])
                db.add(game_record)
                await db.commit()
                users_db = db.exec(select(User).where(User.nickname.in_(list(self.room_record[room_name].players_set)))).all()
                for user in users_db:
                    for player in self.room_record[room_name].game_players:
                        if player.nickname == user.nickname:
                            user.points = player.final_points
                            break
                await db.commit()
                del self.user_in_room[room_name]
                del self.room_record[room_name]

    async def start_round(self, room_name: str,db: DBsession):
        if not self.round_status[room_name].status == RoundStatus.PENDING_START:
            raise ValueError("Invalid status change")
        ready_players = []
        for nickname, user in self.user_in_room[room_name].items():
            if user.user_status == UserStatus.READY_TO_PLAY :
                user.user_status = UserStatus.PLAYING
                ready_players.append(nickname)
            elif user.user_status == UserStatus.READY_TO_WATCH:
                user.user_status = UserStatus.WATCHING
        if len(ready_players) < 2:
            raise ValueError("Not enough players to start the round")
        if len(ready_players) > 9:
            raise ValueError("Who is u????不是群友就滚!!!!")
        players = []
        result = await db.exec(select(User).where(User.nickname.in_(ready_players)))
        for user in result:
            players.append(Player(nickname=user.nickname, points=user.points))
        if not len(self.round_status[room_name].last_blind) == 0:
            players, last_blind = get_blind(players=players,last_blind=self.round_status[room_name].last_blind)
        
        self.round_status[room_name].status = RoundStatus.ROUND_STARTED
        self.round_status[room_name].players = players
        self.round_status[room_name].round_start_time = datetime.now()
        self.round_status[room_name].last_blind = last_blind
        if self.room_record[room_name].rounds_number == 0:
            self.room_record[room_name].game_start_time = datetime.now()
        self.room_record[room_name].rounds_number += 1
        for player in players:
            if player.nickname not in self.room_record[room_name].players_set:
                self.room_record[room_name].players_set.add(player.nickname)
                self.room_record[room_name].game_players.append(record_players(nickname=player.nickname, initial_points=player.points))
        return
        

    async def room_broadcast(self, message: dict, room_name: str):
        if room_name in self.user_in_room:
            for user in self.user_in_room[room_name].values():
                await user.websocket.send_text(json.dumps(message))

# room_name -> GameRoom
game_room = GameRoom()