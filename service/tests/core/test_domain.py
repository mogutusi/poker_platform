"""P0:域模型可构造 + UserStatus 合法转移表(对齐 core.md / enums.py)。"""

from app.core.cards import Card, CardRank, CardSuit
from app.core.domain import EntryVote, Hand, Player, Seat, UserState, World
from app.core.enums import (
    USER_STATUS_TRANSITIONS,
    HandStatus,
    PlayerStatus,
    RoomStatus,
    UserStatus,
)
from tests.builders import T0, make_world, room_with, seat


def test_world_defaults_empty():
    world = World()
    assert world.rooms == {}
    assert world.users == {}


def test_room_seats_fixed_length():
    room = room_with(seats=[seat("A", 100)], max_seats=6)
    assert len(room.seats) == 6
    assert room.seats[0].nickname == "A"
    assert room.seats[1] is None
    assert room.status is RoomStatus.PENDING_START


def test_userstate_carries_uid():
    user = UserState(uid=7, nickname="A", points=500, room="r1")
    assert user.uid == 7
    assert user.room == "r1"


def test_hand_construction_with_new_fields():
    players = [Player(nickname="A", seat_position=0, points=100)]
    hand = Hand(status=HandStatus.PRE_FLOP, players=players, seq=1, start_time=T0)
    assert hand.epoch == 0
    assert hand.last_raise_size == 0
    assert hand.contributed == {}
    assert hand.deck == []


def test_card_to_treys():
    assert Card(CardRank.ACE, CardSuit.SPADES).to_treys() == "As"
    assert Card(CardRank.TEN, CardSuit.HEARTS).to_treys() == "Th"


def test_card_is_hashable_and_value_equal():
    a = Card(CardRank.KING, CardSuit.CLUBS)
    b = Card(CardRank.KING, CardSuit.CLUBS)
    assert a == b
    assert len({a, b}) == 1


def test_entry_vote_defaults():
    vote = EntryVote()
    assert vote.approvals == set()
    assert vote.rejected is False


def test_hand_status_next_status_chain():
    assert HandStatus.PRE_FLOP.next_status is HandStatus.FLOP
    assert HandStatus.FLOP.next_status is HandStatus.TURN
    assert HandStatus.TURN.next_status is HandStatus.RIVER
    assert HandStatus.RIVER.next_status is HandStatus.SHOWDOWN
    assert HandStatus.SHOWDOWN.next_status is None


def test_user_status_legal_transition():
    assert UserStatus.WATCHING.can_change_to(UserStatus.SITTING_IN)
    assert UserStatus.READY_TO_PLAY.can_change_to(UserStatus.PLAYING)
    assert UserStatus.PLAYING.can_change_to(UserStatus.SITTING_IN)


def test_user_status_illegal_transition_rejected():
    # WATCHING 不能直接 PLAYING(必须先 sit in → ready)
    assert not UserStatus.WATCHING.can_change_to(UserStatus.PLAYING)
    # 在玩时不能直接回观战
    assert not UserStatus.PLAYING.can_change_to(UserStatus.WATCHING)


def test_self_transitions_are_subset_of_all():
    from app.core.enums import USER_STATUS_SELF_TRANSITIONS

    assert USER_STATUS_SELF_TRANSITIONS <= USER_STATUS_TRANSITIONS


def test_offline_reconnect_round_trip_legal():
    assert UserStatus.PLAYING.can_change_to(UserStatus.OFFLINE)
    assert UserStatus.OFFLINE.can_change_to(UserStatus.PLAYING)


def test_player_privacy_fields_default_none():
    player = Player(nickname="A", seat_position=0, points=100)
    assert player.hole_cards is None
    assert player.status is PlayerStatus.ACTIVE
    assert player.has_acted is False


def test_make_world_with_room():
    world = make_world(rooms={"r1": room_with(seats=[seat("A", 100)])})
    assert "r1" in world.rooms
    assert isinstance(world.rooms["r1"].seats[0], Seat)
