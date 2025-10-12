from typing import List, Tuple

from app.pokertable.models import Player

def only_room_name(room_name: str):
    if room_name != "room1":
        raise ValueError("Invalid room name")

def get_blind(players: List[Player], last_blind: List[str]) -> Tuple[List[Player], List[str]]:
    last_small_blind = last_blind[0]
    temp_players = []
    for player in players:
        temp_players.append(player.nickname)
    now_players = [x for x in last_blind if x in temp_players]
    now_players.extend([x for x in temp_players if x not in last_blind])
    if last_small_blind in now_players:
        temp_player = now_players[0]
        now_players = now_players[1:]
        now_players.append(temp_player)
    sorted_players = sorted(
        players,
        key=lambda p: now_players.index(p.nickname) if p.nickname in now_players else len(now_players)
    )
    return sorted_players, now_players