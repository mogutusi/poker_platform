from typing import Optional, Dict, Any, AsyncGenerator
from sqlmodel import select
from datetime import datetime

from app.database.core import DBsession
from app.pokertable.gamelogic import only_room_name, get_blind, deal_cards, do_action
from app.pokertable.exceptions import GameLogicError
from app.pokertable.models import Room, Hand, Player, PlayerAction, Seat
from app.pokertable.enums import UserStatus, RoomStatus, HandStatus, PlayerActionType
from app.user.models import User
from app.pokertable.wsm_schemas import (
    ClientMessage, ServerMessage,
    StartHandMessage, SetSmallBlindMessage, SetBuyInMessage, SetUserStatusMessage, PlayerActionMessage,
    HandStartedMessage, HoleCardsMessage, RoomStatusChangedMessage,BettingRoundStartedMessage, SmallBlindSetMessage, BuyInSetMessage, UserStatusChangedMessage,
    BroadcastTarget, PersonalTarget, ServerResponse, SitdownMessage, UserSitdownMessage, BuyInMessage, PlayerBuyInMessage
)

def only_room_name(room_name: str):
    if room_name != "room1":
        raise ValueError("Invalid room name")


async def process_action(room: Room, message: ClientMessage, user_nickname: str, room_name: str, db: DBsession) -> AsyncGenerator[ServerResponse, None]:
    match message:
        case SitdownMessage(seat_number=seat_number):
            message = await sit_down(room=room, user_nickname=user_nickname, seat_number=seat_number, db=db)
            yield message
        case BuyInMessage(buy_in=buy_in, seat_number=seat_number):
            message = await player_buy_in(room=room, user_nickname=user_nickname, buy_in=buy_in, seat_number=seat_number, db=db)
            yield message
        case StartHandMessage():
            async for message in start_hand(room=room, user_nickname=user_nickname, db=db):
                yield message
        case SetSmallBlindMessage(small_blind=blind):
            message = await set_small_blind(room=room, user_nickname=user_nickname, small_blind=blind)
            yield message
        case SetBuyInMessage(buy_in = buy_in):
            message = await set_buy_in(room=room, user_nickname=user_nickname, buy_in=buy_in)
            yield message
        case SetUserStatusMessage(user_status=status):
            message = await set_user_status(room=room, user_nickname=user_nickname, user_status=status)
            yield message
        case PlayerActionMessage():
            async for message in player_action(room=room, user_nickname=user_nickname, message=message, db=db):
                yield message
        case _:
            raise GameLogicError(message="Invalid action")

async def sit_down(room: Room, user_nickname: str, seat_number: int) -> ServerResponse:
    if user_nickname not in room.users_in_room.keys():
        raise GameLogicError(message="Only players in the room can sit down")
    if not room.status == RoomStatus.PENDING_START:
        raise GameLogicError(message="Invalid status change")
    if seat_number < 0 or seat_number >= len(room.seats):
        raise GameLogicError(message="Invalid seat number")
    if room.seats[seat_number] is not None:
        raise GameLogicError(message="Seat already taken")
    room.seats[seat_number] = Seat(nickname=user_nickname, points=0)
    message = UserSitdownMessage(seat_number=seat_number, user_nickname=user_nickname)
    return BroadcastTarget(message=message)

async def player_buy_in(room: Room, user_nickname: str, buy_in: int, seat_number: int, db: DBsession) -> ServerResponse:
    if user_nickname not in room.users_in_room.keys():
        raise GameLogicError(message="Only players in the room can buy in")
    if seat_number < 0 or seat_number >= len(room.seats):
        raise GameLogicError(message="Invalid seat number")
    if room.seats[seat_number].nickname != user_nickname:
        raise GameLogicError(message="Only the player in the seat can buy in")
    if room.buy_in != buy_in:
        raise GameLogicError(message="Invalid buy in amount")
    seat = room.seats[seat_number]
    if seat.points + seat.in_game_points >= buy_in:
        raise GameLogicError(message="Already bought in enough")
    statement = (
        select(User)
        .where(User.nickname == user_nickname)
        .with_for_update()
    )
    result = await db.exec(statement)
    user_db = result.one()
    user_db.points -= buy_in
    seat.points += buy_in
    await db.commit()
    message = PlayerBuyInMessage(seat_number=seat_number, user_nickname=user_nickname, buy_in=buy_in)
    return BroadcastTarget(message=message)

async def start_hand(room: Room, user_nickname: str,db: DBsession) -> AsyncGenerator[ServerResponse, None]:
    # Validation
    if room.hand is not None:
        raise GameLogicError(message="Hand already exists")
    if user_nickname not in room.users_in_room.keys() or room.users_in_room[user_nickname].user_status != UserStatus.READY_TO_PLAY:
        raise GameLogicError(message="Only players in the room can start the hand")
    if not room.status == RoomStatus.PENDING_START:
            raise GameLogicError(message="Invalid status change")
    ready_players_nicknames = []
    # lock!!!
    # async with lock:
    for nickname, user in room.users_in_room.items():
        if user.user_status == UserStatus.READY_TO_PLAY :
            ready_players_nicknames.append(nickname)
        if user.user_status != UserStatus.READY_TO_WATCH:
            raise GameLogicError(message="Some players are not ready")
    if len(ready_players_nicknames) < 2:
        raise GameLogicError(message="Not enough players to start the round")
    if len(ready_players_nicknames) > 9:
        raise GameLogicError(message="Who is u????不是群友就滚!!!!")
    statement = (
        select(User)
        .where(User.nickname.in_(ready_players_nicknames))
        .with_for_update()
    )
    result = await db.exec(statement)
    users_db = result.all()
    if len(users_db) != len(ready_players_nicknames):
        raise GameLogicError(message="Some players are not found")
    user_db_map = {u.nickname: u for u in users_db}
    players = []
    
    for nickname in ready_players_nicknames:
        if user_db_map[nickname].points < room.buy_in:
            raise GameLogicError(message="Player has not enough points to cover buy in")
        user_db_map[nickname].points -= room.buy_in
        players.append(Player(nickname=nickname, points=room.buy_in))
    # Get blind
    players, last_blind = get_blind(players=players,last_blind=room.last_blind)
    # Update room status
    room.status = RoomStatus.HAND_STARTED
    for nickname, user in room.users_in_room.items():
        if nickname in last_blind:
            user.user_status = UserStatus.PLAYING
        elif user.user_status == UserStatus.READY_TO_WATCH:
            user.user_status = UserStatus.WATCHING
    room.hand = Hand(
        status=HandStatus.READY_TO_START,
        players=players,
        start_time=datetime.now(),
        last_bet=2 * room.small_blind,
    )
    # Update DB
    await db.commit()
    message = RoomStatusChangedMessage(room_status=room.status, changed_by=user_nickname)
    yield BroadcastTarget(message=message)
    # hand started
    room.hand.status = HandStatus.PRE_FLOP
    room.hand.acting_player_position = 2 % len(room.hand.players)
    room.hand.players[0].points -= room.small_blind
    room.hand.players[0].bet_amount = room.small_blind
    room.hand.players[1].points -= 2 * room.small_blind
    room.hand.players[1].bet_amount = 2 * room.small_blind
    room.hand.pot = 3 * room.small_blind
    room.hand.last_bet = 2 * room.small_blind
    message = HandStartedMessage(hand=room.hand)
    yield BroadcastTarget(message=message)
    deal_cards(room=room)
    for player in room.hand.players:
        message = HoleCardsMessage(cards=player.hole_cards)
        yield PersonalTarget(nickname=player.nickname, message=message)
    message = BettingRoundStartedMessage(hand_status=room.hand.status, acting_player=room.hand.players[room.hand.acting_player_position].nickname, pot=room.hand.pot, last_bet=room.hand.last_bet)
    yield BroadcastTarget(message=message)
        
async def set_small_blind(room: Room, user_nickname: str, small_blind: int) -> ServerResponse:
    if user_nickname not in room.users_in_room.keys():
        raise GameLogicError(message="Only players in the room can set small blind")
    if not room.status == RoomStatus.PENDING_START:
        raise GameLogicError(message="Invalid status change")
    room.small_blind = small_blind
    message = SmallBlindSetMessage(small_blind=room.small_blind, set_by=user_nickname)    
    return BroadcastTarget(message=message)

async def set_buy_in(room: Room, user_nickname: str, buy_in: int) -> ServerResponse:
    if user_nickname not in room.users_in_room.keys():
        raise GameLogicError(message="Only players in the room can set buy in")
    if not room.status == RoomStatus.PENDING_START:
        raise GameLogicError(message="Invalid status change")
    room.buy_in = buy_in
    message = BuyInSetMessage(buy_in=room.buy_in, set_by=user_nickname)
    return BroadcastTarget(message=message)

async def set_user_status(room: Room, user_nickname: str, user_status: UserStatus) -> ServerResponse:
    if user_nickname not in room.users_in_room.keys():
        raise GameLogicError(message="Only players in the room can set user status")
    if room.status != RoomStatus.PENDING_START:
        raise GameLogicError(message="Only pending start status can set user status")
    if not room.users_in_room[user_nickname].user_status.can_change_to(user_status):
        raise GameLogicError(message="Invalid status transition")
    room.users_in_room[user_nickname].user_status = user_status
    message = UserStatusChangedMessage(user_status=user_status, user_nickname=user_nickname)
    return BroadcastTarget(message=message)

async def player_action(room: Room, user_nickname: str, message: PlayerActionMessage, db: DBsession) -> AsyncGenerator[ServerResponse, None]:
    pass