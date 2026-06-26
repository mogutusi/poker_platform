# wire 协议单测:序列化边界隐私 + client 报文 parse/to_command + ErrorMessage.from_err + 可辨识联合。
# 与 core 单测互补——core 测结构性隐私(hasattr),这里测**序列化产物**(真正发往客户端的 JSON)无底牌/牌堆,
# 并锁住 client→Command 映射(身份盖 origin、墙钟盖 now)。纯同步、无 IO(testing.md)。

from datetime import datetime, timezone

from app.core import commands
from app.core.cards import Card, CardRank, CardSuit
from app.core.enums import HandStatus, PlayerActionType, PlayerStatus, RoomStatus, UserStatus
from app.core.errors import Err, ErrorCode
from app.wire import client as C
from app.wire import server as S

_NOW = datetime(2026, 6, 23, 12, 0, tzinfo=timezone.utc)
_A = Card(CardRank.ACE, CardSuit.SPADES)
_K = Card(CardRank.KING, CardSuit.HEARTS)


def _all_strings(obj):
    # 递归收集嵌套 dict/list 里的全部 key,用于「序列化后无某字段」的兜底断言。
    keys: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(k)
            keys |= _all_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            keys |= _all_strings(v)
    return keys


def _broadcast_samples() -> list[S.ServerMessage]:
    view = S.PlayerView(seat_position=0, nickname="A", points=98, bet_amount=2, status=PlayerStatus.ACTIVE)
    return [
        S.HandStarted(hand_seq=1, button_position=0, small_blind=1, big_blind=2, players=(view,), acting_position=0),
        S.HandStatusChanged(status=HandStatus.FLOP, board=(_A,)),
        S.PlayerActed(seat_position=0, nickname="A", action=PlayerActionType.BET, bet_amount=2, points=98,
                      status=PlayerStatus.ACTIVE, last_bet=2, pot=3, acting_position=1),
        S.HandEnded(winnings=(S.NickAmount(nickname="A", amount=3),), refunds=()),
        S.UserStatusChanged(nickname="A", status=UserStatus.SITTING_IN, seat_position=0),
        S.UserJoined(nickname="C"),
        S.UserLeft(nickname="A", seat_position=0),
        S.PlayerBoughtIn(nickname="A", seat_position=0, amount=64, seat_points=64),
        S.FreeEntryVoteUpdated(candidates=("D",), voters=("A", "B"), approvals=("A",)),
        S.FreeEntryVoteClosed(passed=True, waived=("D",)),
        S.ChatMessage(from_nick="A", text="nh"),
        S.RoomChatHistory(room="r1", messages=(S.ChatMessage(from_nick="A", text="nh"),)),
        S.DMDelivered(msg_id="m1", from_nick="A", text="hey", created_at=_NOW),
        S.DMUndelivered(to_nick="ghost"),
        S.ErrorMessage.from_err(Err(ErrorCode.NOT_YOUR_TURN, "non-A turn")),
    ]


def test_broadcast_serialization_has_no_hole_cards_or_deck():
    # 隐私红线(wire.md #5):非揭示 DTO 的序列化产物里不得出现 hole_cards / deck / cards。
    for msg in _broadcast_samples():
        keys = _all_strings(msg.model_dump(mode="json"))
        assert "hole_cards" not in keys, msg
        assert "deck" not in keys, msg
        assert "cards" not in keys, msg  # 'cards' 仅属 HoleCards(揭示点),广播 DTO 无之


def test_reveal_dtos_carry_cards():
    # 两个揭示点显式携带底牌:HoleCards(私发本人)、HandShowDown.reveals(摊牌)。
    hc = S.HoleCards(cards=(_A, _K))
    assert _all_strings(hc.model_dump(mode="json")) >= {"cards", "rank", "suit"}
    sd = S.HandShowDown(
        board=(_A, _K, _A, _K, _A),
        reveals=(S.ShowdownReveal(seat_position=0, nickname="A", hole_cards=(_A, _K)),),
    )
    assert "hole_cards" in _all_strings(sd.model_dump(mode="json"))


def test_state_snapshot_carries_only_own_cards_not_others_or_deck():
    # StateSnapshot 私发收件人:含 your_hole_cards(自己的),但 players 结构上无 hole_cards、无 deck。
    snap = S.StateSnapshot(
        room="r1", max_seats=6, button_position=0, small_blind=1, big_blind=2,
        room_status=RoomStatus.HAND_STARTED,
        seats=(S.SeatView(seat_position=0, nickname="A", status=UserStatus.PLAYING, points=50, new_here=False),),
        watchers=("C",),
        hand_status=HandStatus.FLOP, board=(_A,), pot=20, acting_position=0,
        players=(S.PlayerView(seat_position=0, nickname="A", points=50, bet_amount=0, status=PlayerStatus.ACTIVE),),
        your_hole_cards=(_A, _K),
    )
    keys = _all_strings(snap.model_dump(mode="json"))
    assert "your_hole_cards" in keys  # 自己的底牌显式携带
    assert "hole_cards" not in keys  # 无他人底牌字段(players 结构上无)
    assert "deck" not in keys  # 牌堆不入快照
    assert snap.model_copy(update={"your_hole_cards": None}).model_dump(mode="json")["your_hole_cards"] is None


def test_card_serializes_to_rank_suit_shortcodes():
    assert S.HoleCards(cards=(_A, _K)).model_dump(mode="json")["cards"][0] == {"rank": "A", "suit": "s"}


def test_error_message_from_err_preserves_code_and_detail():
    em = S.ErrorMessage.from_err(Err(ErrorCode.SEAT_TAKEN, "seat 3"))
    assert em.code is ErrorCode.SEAT_TAKEN
    assert em.detail == "seat 3"
    assert em.model_dump(mode="json") == {"type": "error", "code": "SEAT_TAKEN", "detail": "seat 3"}


def test_parse_and_to_command_maps_every_client_message():
    # 身份不进报文:to_command 盖 origin;墙钟由 shell 盖 now(仅 StartHand 用);deck 生产恒 None。
    cases = [
        ('{"type":"sit_down","seat":3}', commands.SitDown(origin="A", seat=3)),
        ('{"type":"buy_in","seat":3,"amount":64}', commands.BuyIn(origin="A", seat=3, amount=64)),
        ('{"type":"set_user_status","status":"ready_to_play"}',
         commands.SetUserStatus(origin="A", status=UserStatus.READY_TO_PLAY, seat=None)),
        ('{"type":"set_user_status","status":"sitting_in","seat":2}',
         commands.SetUserStatus(origin="A", status=UserStatus.SITTING_IN, seat=2)),
        ('{"type":"leave_room"}', commands.LeaveRoom(origin="A")),
        ('{"type":"start_hand","seat":0}', commands.StartHand(origin="A", seat=0, started_at=_NOW, deck=None)),
        ('{"type":"player_action","action":"bet","bet_amount":10}',
         commands.PlayerAction(origin="A", action=PlayerActionType.BET, bet_amount=10)),
        ('{"type":"player_action","action":"check"}',
         commands.PlayerAction(origin="A", action=PlayerActionType.CHECK, bet_amount=None)),
        ('{"type":"room_chat","text":"nice hand"}', commands.RoomChat(origin="A", text="nice hand")),
        ('{"type":"open_free_entry_vote"}', commands.OpenFreeEntryVote(origin="A")),
        ('{"type":"vote_free_entry","approve":true}', commands.VoteFreeEntry(origin="A", approve=True)),
        ('{"type":"vote_free_entry","approve":false}', commands.VoteFreeEntry(origin="A", approve=False)),
    ]
    for raw, expected in cases:
        msg = C.parse(raw)
        assert C.to_command(msg, origin="A", now=_NOW) == expected, raw


def test_parse_rejects_unknown_and_missing_type():
    import pydantic

    for bad in ('{"type":"nope"}', '{"seat":1}', '{"type":"sit_down"}'):  # 未知 type / 缺判别量 / 缺必填字段
        try:
            C.parse(bad)
        except pydantic.ValidationError:
            continue
        raise AssertionError(f"should have rejected: {bad}")


def test_client_registry_covered_by_to_command():
    # CLIENT_MESSAGES 每个类型都登记;非 JoinRoom 经 to_command 映射,JoinRoom 是特例(需 DB 富化 uid/loaded,
    # 由 Receiver 异步构造,to_command 命中即 AssertionError,见 changes/0030)。
    import pytest

    samples = {
        C.SitDown: C.SitDown(seat=0),
        C.BuyIn: C.BuyIn(seat=0, amount=1),
        C.SetUserStatus: C.SetUserStatus(status=UserStatus.SITTING_IN),
        C.LeaveRoom: C.LeaveRoom(),
        C.StartHand: C.StartHand(seat=0),
        C.PlayerAction: C.PlayerAction(action=PlayerActionType.CHECK),
        C.RoomChat: C.RoomChat(text="hi"),
        C.OpenFreeEntryVote: C.OpenFreeEntryVote(),
        C.VoteFreeEntry: C.VoteFreeEntry(approve=True),
        C.JoinRoom: C.JoinRoom(room="dev"),
        C.FetchRoomChat: C.FetchRoomChat(room="dev"),
        C.DirectMessage: C.DirectMessage(to_nick="bob", text="hi"),
    }
    assert set(samples) == set(C.CLIENT_MESSAGES)  # 防新增报文漏测
    assert C.parse('{"type":"join_room","room":"dev"}') == C.JoinRoom(room="dev")  # parse 往返
    for cls, msg in samples.items():
        if cls in (C.JoinRoom, C.FetchRoomChat, C.DirectMessage):
            with pytest.raises(AssertionError):  # JoinRoom 走 DB 富化 / FetchRoomChat·DirectMessage 走 shell 路由,均不走 to_command(契约)
                C.to_command(msg, origin="A", now=_NOW)
            continue
        cmd = C.to_command(msg, origin="A", now=_NOW)
        assert isinstance(cmd, commands.Command)
        assert cmd.origin == "A"
