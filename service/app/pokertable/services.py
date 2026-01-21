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
    BroadcastTarget, PersonalTarget, ServerResponse, SitdownMessage, UserSitdownMessage, BuyInMessage, PlayerBuyInMessage, LeaveRoomMessage , UserLeaveRoomMessage
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
        case StartHandMessage(seat_number=seat_number):
            async for message in start_hand(room=room, user_nickname=user_nickname, seat_number=seat_number):
                yield message
        case SetSmallBlindMessage(small_blind=blind):
            message = await set_small_blind(room=room, user_nickname=user_nickname, small_blind=blind)
            yield message
        case SetBuyInMessage(buy_in = buy_in):
            message = await set_buy_in(room=room, user_nickname=user_nickname, buy_in=buy_in)
            yield message
        case SetUserStatusMessage(user_status=status, seat_number=seat_number):
            message = await set_user_status(room=room, user_nickname=user_nickname, user_status=status, seat_number=seat_number, db=db)
            yield message
        case LeaveRoomMessage():
            message = await leave_room(room=room, user_nickname=user_nickname, db=db)
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
    room.users_in_room[user_nickname].user_status = UserStatus.SITTING_IN
    message = UserSitdownMessage(seat_number=seat_number, user_nickname=user_nickname)
    return BroadcastTarget(message=message)

async def player_buy_in(room: Room, user_nickname: str, buy_in: int, seat_number: int, db: DBsession) -> ServerResponse:
    if user_nickname not in room.users_in_room.keys():
        raise GameLogicError(message="Only players in the room can buy in")
    if seat_number < 0 or seat_number >= len(room.seats):
        raise GameLogicError(message="Invalid seat number")
    if room.seats[seat_number] is None or room.seats[seat_number].nickname != user_nickname:
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

async def start_hand(room: Room, user_nickname: str, seat_number: int) -> AsyncGenerator[ServerResponse, None]:
    # Validation
    if room.hand is not None:
        raise GameLogicError(message="Hand already exists")
    if room.seats[seat_number] is None or room.seats[seat_number].nickname != user_nickname:
        raise GameLogicError(message="Only the player in the seat can start the hand")
    if room.users_in_room[user_nickname].user_status != UserStatus.READY_TO_PLAY:
        raise GameLogicError(message="Only ready players can start the hand")
    if not room.status == RoomStatus.PENDING_START:
            raise GameLogicError(message="Invalid status change")
    
    # lock!!!
    # async with lock:
    ready_players_nicknames = []

    for i in range(1,len(room.seats)+1):
        seat = room.seats[(room.button_position + i) % len(room.seats)]
        if seat is not None and seat.nickname in room.users_in_room.keys():
            if room.users_in_room[seat.nickname].user_status == UserStatus.SITTING_IN:
                raise GameLogicError(message="Some players are not ready")
            if room.users_in_room[seat.nickname].user_status == UserStatus.READY_TO_PLAY:
                ready_players_nicknames.append(((room.button_position + i) % len(room.seats),seat.nickname))

    if len(ready_players_nicknames) < 2:
        raise GameLogicError(message="Not enough players to start the hand")
    if len(ready_players_nicknames) > 9:
        raise GameLogicError(message="Who is u????不是群友就滚!!!!")
    room.button_position = ready_players_nicknames[0][0]
    
    # get blind 
    dead_blind_seat = -1
    if room.new_player_seat_list != [] and len(ready_players_nicknames) > 2:
        if ready_players_nicknames[2][0] in room.new_player_seat_list:
            dead_blind_seat = ready_players_nicknames[2][0]
        else:   
            room.new_player_seat_list.append(ready_players_nicknames[2][0])
            temp = sorted(room.new_player_seat_list)
            dead_blind_seat = temp[(temp.index(ready_players_nicknames[2][0]) - 1) % len(temp)]
    room.new_player_seat_list = []

    players = []
    dead_blind = -1
    for i,(position, nickname) in enumerate(ready_players_nicknames[1:]+ready_players_nicknames[:1]):
        players.append(Player(
            nickname=nickname, 
            points=room.seats[position].points, 
            seat_position=position, 
        ))
        room.seats[position].in_game_points = room.seats[position].points
        room.seats[position].points = 0
        if position == dead_blind_seat:
            dead_blind = i
    
    # Update room status
    room.status = RoomStatus.HAND_STARTED
    for player in players:
        room.users_in_room[player.nickname].user_status = UserStatus.PLAYING
    
    room.hand = Hand(
        status=HandStatus.READY_TO_START,
        players=players,
        start_time=datetime.now(),
        last_bet=2 * room.small_blind,
    )
    message = RoomStatusChangedMessage(room_status=room.status, changed_by=user_nickname)
    yield BroadcastTarget(message=message)
    # hand started
    room.hand.status = HandStatus.PRE_FLOP
    if len(players) > 2:
        room.hand.acting_player_position = 2 
        room.hand.players[0].points -= room.small_blind
        room.hand.players[0].bet_amount = room.small_blind
        if dead_blind == -1:
            room.hand.players[1].points -= 2 * room.small_blind
            room.hand.players[1].bet_amount = 2 * room.small_blind
        else:
            room.hand.players[dead_blind].points -= 2 * room.small_blind
            room.hand.players[dead_blind].bet_amount = 2 * room.small_blind
    else:
        room.hand.acting_player_position = 1
        room.hand.players[1].points -= room.small_blind
        room.hand.players[1].bet_amount = room.small_blind
        room.hand.players[0].points -= 2 * room.small_blind
        room.hand.players[0].bet_amount = 2 * room.small_blind
    room.hand.pot = 3 * room.small_blind
    room.hand.last_bet = 2 * room.small_blind
    message = HandStartedMessage(hand=room.hand, dead_blind=dead_blind)
    yield BroadcastTarget(message=message)
    deal_cards(players=room.hand.players)
    for player in room.hand.players:
        message = HoleCardsMessage(cards=player.hole_cards)
        yield PersonalTarget(nickname=player.nickname, message=message)
    message = BettingRoundStartedMessage(
        hand_status=room.hand.status, 
        acting_player=room.hand.players[room.hand.acting_player_position].nickname, 
        pot=room.hand.pot, 
        last_bet=room.hand.last_bet
    )
    yield BroadcastTarget(message=message)
        
async def set_small_blind(room: Room, user_nickname: str, small_blind: int) -> ServerResponse:
    if room.seats[0] is None or room.seats[0].nickname != user_nickname:
        raise GameLogicError(message="Only the seat 0 can set small blind")
    if not room.status == RoomStatus.PENDING_START:
        raise GameLogicError(message="Invalid status change")
    room.small_blind = small_blind
    room.last_small_blind_position = 0
    message = SmallBlindSetMessage(small_blind=room.small_blind, set_by=user_nickname)    
    return BroadcastTarget(message=message)

async def set_buy_in(room: Room, user_nickname: str, buy_in: int) -> ServerResponse:
    if room.seats[0] is None or room.seats[0].nickname != user_nickname:
        raise GameLogicError(message="Only the seat 0 can set buy in")
    if not room.status == RoomStatus.PENDING_START:
        raise GameLogicError(message="Invalid status change")
    room.buy_in = buy_in
    message = BuyInSetMessage(buy_in=room.buy_in, set_by=user_nickname)
    return BroadcastTarget(message=message)

async def set_user_status(room: Room, user_nickname: str, user_status: UserStatus, seat_number: int, db: DBsession) -> ServerResponse:
    if user_nickname not in room.users_in_room.keys():
        raise GameLogicError(message="Only players in the room can set user status")
    if seat_number < 0 or seat_number >= len(room.seats):
        raise GameLogicError(message="Invalid seat number")
    if room.seats[seat_number] is None or room.seats[seat_number].nickname != user_nickname:
        raise GameLogicError(message="Only the player in the seat can set user status")
    now_status = room.users_in_room[user_nickname].user_status
    if not now_status.userself_can_change_to(user_status):
        raise GameLogicError(message="Invalid status transition")
    if user_status == UserStatus.READY_TO_PLAY:
        if room.seats[seat_number].points == 0:
            raise GameLogicError(message="Player has no points, cannot ready to play")
        room.new_player_seat_list.append(seat_number)
    if user_status == UserStatus.WATCHING:
        if room.seats[seat_number].in_game_points > 0:
            raise GameLogicError(message="Player has in game points, cannot leave the table")
        if room.seats[seat_number].points > 0:
            statement = (
                select(User)
                .where(User.nickname == user_nickname)
                .with_for_update()
            )
            result = await db.exec(statement)
            user_db = result.one()
            user_db.points += room.seats[seat_number].points
            await db.commit()
        room.seats[seat_number] = None
    room.users_in_room[user_nickname].user_status = user_status
    message = UserStatusChangedMessage(user_status=user_status, user_nickname=user_nickname, seat_number=seat_number)
    return BroadcastTarget(message=message)

async def leave_room(room: Room, user_nickname: str, db: DBsession) -> ServerResponse:
    if user_nickname not in room.users_in_room.keys():
        raise GameLogicError(message="Only players in the room can leave the room")
    if room.users_in_room[user_nickname] == UserStatus.PLAYING:
        raise GameLogicError(message="Cannot leave the room in the middle of the hand")
    if room.users_in_room[user_nickname] != UserStatus.WATCHING:
        seat_number = -1
        for i in range(len(room.seats)):
            if room.seats[i] is not None and room.seats[i].nickname == user_nickname:
                seat_number = i
                break
        if seat_number == -1:
            raise GameLogicError(message="Seat not found")
        statement = (
            select(User)
            .where(User.nickname == user_nickname)
            .with_for_update()
        )
        result = await db.exec(statement)
        user_db = result.one()
        user_db.points += room.seats[seat_number].points
        await db.commit()
        room.seats[seat_number] = None

    del room.users_in_room[user_nickname]
    message = UserLeaveRoomMessage(nickname=user_nickname, leave_type="leave_room")
    return BroadcastTarget(message=message)

async def player_action(room: Room, user_nickname: str, message: PlayerActionMessage, db: DBsession) -> AsyncGenerator[ServerResponse, None]:
    pass