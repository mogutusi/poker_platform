# core 游戏状态机:reduce(work, cmd) -> (events, err)。
# 纯同步、不 await、不碰 IO/DB、不读墙钟(不变量 1);改的是 GameLoop 给的工作副本,
# 成功 commit、失败/异常丢弃(见 core.md / storage.md)。命令簇逐次落地(见 refactor/changes/),
# 顶层 match 已实现的臂见 reduce();未实现命令落 case _ 的 INTERNAL 占位。

from app.core.cards import Card
from app.core.commands import (
    BuyIn,
    Cleanup,
    Command,
    Connect,
    Disconnect,
    JoinRoom,
    LeaveRoom,
    OpenFreeEntryVote,
    PlayerAction,
    RoomChat,
    SetUserStatus,
    SitDown,
    StartHand,
    Timeout,
    VoteFreeEntry,
)
from app.core.deck import BOARD_CARDS, evaluate, shuffled_deck
from app.core.domain import EntryVote, Hand, Player, Room, Seat, UserState, Work
from app.core.enums import (
    HandStatus,
    PlayerActionType,
    PlayerStatus,
    RoomStatus,
    UserStatus,
)
from app.core.errors import Err, ErrorCode
from app.core.events import Broadcast, ClearAction, Event, Personal, Persist, TurnChanged
from app.core.records import HandRecordWrite, ParticipantWrite, PointsWrite
from app.wire.server import (  # core 投影直接产 wire DTO(models.md);Broadcast/Personal 的 msg
    ChatMessage,
    FreeEntryVoteClosed,
    FreeEntryVoteUpdated,
    HandEnded,
    HandShowDown,
    HandStarted,
    HandStatusChanged,
    HoleCards,
    NickAmount,
    PlayerActed,
    PlayerBoughtIn,
    PlayerView,
    SeatView,
    ShowdownReveal,
    StateSnapshot,
    UserJoined,
    UserLeft,
    UserStatusChanged,
)
from app.core.rules import betting, blinds, sidepot

# SetUserStatus 玩家可主动发起的目标状态:就座内 ready/sit-out 切换 + 起身离座(→WATCHING)。
# 入座(WATCHING→SITTING_IN)走 SitDown,不在此列。
_SELF_STATUS_TARGETS = frozenset(
    {UserStatus.READY_TO_PLAY, UserStatus.SITTING_IN, UserStatus.SITTING_OUT, UserStatus.WATCHING}
)

ReduceResult = tuple[list[Event], Err | None]


def reduce(work: Work, cmd: Command) -> ReduceResult:
    # 顶层按命令类型分发;每个 handler:先校验(返回 Err)→ 改工作副本 → 产出 events。
    match cmd:
        case JoinRoom():
            return _join_room(work, cmd)
        case StartHand():
            return _start_hand(work, cmd)
        case PlayerAction():
            return _player_action(work, cmd)
        case Timeout():
            return _timeout(work, cmd)
        case LeaveRoom():
            return _leave_room(work, cmd)
        case Connect():
            return _connect(work, cmd)
        case Disconnect():
            return _disconnect(work, cmd)
        case Cleanup():
            return _cleanup(work, cmd)
        case SetUserStatus():
            return _set_user_status(work, cmd)
        case SitDown():
            return _sit_down(work, cmd)
        case BuyIn():
            return _buy_in(work, cmd)
        case OpenFreeEntryVote():
            return _open_free_entry_vote(work, cmd)
        case VoteFreeEntry():
            return _vote_free_entry(work, cmd)
        case RoomChat():
            return _room_chat(work, cmd)
        case _:
            # 其余命令的 handler 随后续变更逐个落地(见 refactor/TODO P1);未实现期间
            # 按内部错误归一(工作副本被丢弃、world 不动),全部落地后此分支应不可达。
            return [], Err(ErrorCode.INTERNAL, f"reduce 暂未实现命令 {type(cmd).__name__}")


# ── 开局(StartHand)── rules.md ① 开局那半 + core.md §1
def _start_hand(work: Work, cmd: StartHand) -> ReduceResult:
    room = work.room
    if room is None:
        return [], Err(ErrorCode.NOT_IN_ROOM, "StartHand:发起人不在任何房间")
    if room.status is not RoomStatus.PENDING_START or room.hand is not None:
        return [], Err(ErrorCode.HAND_IN_PROGRESS, "已有手牌进行中")

    # 发起人须在其声明的座位、且 READY_TO_PLAY
    if not (0 <= cmd.seat < len(room.seats)):
        return [], Err(ErrorCode.NOT_YOUR_SEAT, f"座位号 {cmd.seat} 越界")
    seat = room.seats[cmd.seat]
    if seat is None or seat.nickname != cmd.origin:
        return [], Err(ErrorCode.NOT_YOUR_SEAT, f"座位 {cmd.seat} 不属于 {cmd.origin}")
    if room.users_in_room.get(cmd.origin) is not UserStatus.READY_TO_PLAY:
        return [], Err(ErrorCode.NOT_READY, f"{cmd.origin} 未 READY_TO_PLAY,不能开局")

    # 入局资格分类(rules.md ①):core_dealt = 不依赖 BB 位就发牌的座位(established/付盲即玩/bootstrap/waive);
    # paying_entrants ⊆ core_dealt 需付入局 BB;waiters = 选「等大盲免费」、本手是否入局取决于 BB 是否扫到其座。
    core_dealt, paying_entrants, waiters = _eligible_seats(room)
    if len(core_dealt) < 1:
        # 无已入局/付盲座位锚定庄位(FIX-1:advance_button 空集会 raise,破 helper 契约);等大盲者单独不能
        # bootstrap(无「免费空降好位」不变量,rules.md ①)→ 判人数不足等待。
        return [], Err(ErrorCode.NOT_ENOUGH_PLAYERS, "无已入局(established/付盲)玩家锚定庄位")

    # 定位(复用 blinds):庄**只在 core_dealt 上推进**(waiter 永不持庄/小盲),再据庄判等大盲者是否被 BB 扫入。
    button = blinds.advance_button(room.button_position, core_dealt)
    entrant = blinds.sweep_entrant(button, core_dealt, waiters)  # BB 扫到的等大盲者(免费入局,下结构大盲)
    dealt = core_dealt | ({entrant} if entrant is not None else set())
    if len(dealt) < 2:
        return [], Err(ErrorCode.NOT_ENOUGH_PLAYERS, f"在局 ready 玩家不足 2(={len(dealt)})")

    order = blinds.seat_order(button, dealt)  # entrant(若有)即 order[1]=大盲,由 post_blinds 下结构大盲
    n = len(order)
    small_blind = room.small_blind
    big_blind = blinds.BIG_BLIND_MULTIPLE * small_blind

    # 取牌堆并校验够发 2N 张:生产洗牌恒 52;注入牌堆(重放/测试)若过短先返 Err,守 helper
    # 「绝不 raise」契约(core.md)。先校验后改工作副本——此 return 在任何 mutation 之前。
    deck = list(cmd.deck) if cmd.deck is not None else shuffled_deck()
    if len(deck) < 2 * n + BOARD_CARDS:
        # 一手最多需 2N 张底牌 + 5 张公共牌;一次校验到底,使后续发公共牌的 helper 永不缺牌(绝不 raise)。
        # 生产洗牌恒 52 张不受影响;注入过短牌堆(测试/重放)返 Err、丢工作副本。
        return [], Err(ErrorCode.INTERNAL, f"牌堆 {len(deck)} 张不足一手({n} 人)所需")

    # 锁筹:Seat.points → Player.points,存 in_game_points 快照,Seat.points 清零
    players: list[Player] = []
    for idx in order:
        s = room.seats[idx]
        assert s is not None  # order ⊆ dealt ⊆ 已占座位
        s.in_game_points = s.points
        players.append(Player(nickname=s.nickname, seat_position=idx, points=s.points))
        s.points = 0

    room.hand_seq += 1
    hand = Hand(
        status=HandStatus.PRE_FLOP,
        players=players,
        seq=room.hand_seq,
        start_time=cmd.started_at,
    )

    # 下盲(结构盲注)+ 入局 post(付盲即玩,live:投一个 BB 进 bet_amount)
    blinds.post_blinds(hand, small_blind)
    blind_seats = {players[0].seat_position, players[1].seat_position}
    for p in players:
        if p.seat_position in paying_entrants and p.seat_position not in blind_seats:
            _post_entry(p, big_blind)  # 占盲位的入局者以结构盲注充作入局付费,不重复 post

    # 发底牌(不烧牌):轮转取前 2N 张,余下存 hand.deck 供后续街(deck 已在上方校验长度)
    for j, p in enumerate(players):
        p.hole_cards = (deck[j], deck[n + j])
    hand.deck = deck[2 * n:]

    # 置 PLAYING / HAND_STARTED;入局者清 new_here / wait_for_big_blind;消费 waive 快照
    dealt_seats = {p.seat_position for p in players}
    for p in players:
        room.users_in_room[p.nickname] = UserStatus.PLAYING
        dealt_seat = room.seats[p.seat_position]
        assert dealt_seat is not None
        dealt_seat.new_here = False
        dealt_seat.wait_for_big_blind = False
    # 防躲盲(rules.md ① 行 50「判据是上一手是否参与」):本手未被发牌的在座者一律重标 new_here——
    # 涵盖坐出 / 没 ready 干等 / 断线跨手,回来都要付盲或等大盲(等大盲 waiter 本就 new_here,此处幂等)。
    # 必在上面发牌者清 new_here 之后跑,且 eligibility/bootstrap 已在本函数顶读 pre-hand new_here,故不自扰本手。
    for i, s in enumerate(room.seats):
        if s is not None and i not in dealt_seats:
            s.new_here = True
    room.waive_entry_for = set()
    room.entry_vote = None  # 阵容已定:未通过的免盲投票随开局作废(rules.md ① 行 75「没投到不算免」),不跨手悬挂
    room.button_position = button
    room.status = RoomStatus.HAND_STARTED
    room.hand = hand

    # preflop 首行动 = BB 下一位(heads-up 自然回到 players[0]=button/SB)
    hand.acting_position = betting.next_active_position(hand, 1)

    events = _start_hand_events(work.room_name, room, hand, small_blind, big_blind)
    if hand.acting_position is None:
        # born-all-in:全员投盲即 all-in、无人可行动 → 不等动作,立即结算本街、跑公共牌摊牌
        # (完成 0010 §6 待办:街推进入口须接住此手,否则手卡死、无人察觉)。
        events += _close_street(work, hand, big_blind)
    return events, None


def _eligible_seats(room: Room) -> tuple[set[int], set[int], set[int]]:
    # 入局资格分类(rules.md ①)。返回 (core_dealt, paying_entrants, waiters):
    #   core_dealt    = 不依赖庄/BB 位即发牌的座位(established/付盲即玩/bootstrap/waive);庄位据它定。
    #   paying_entrants ⊆ core_dealt:需付一个入局 BB(付盲即玩的 new_here)。
    #   waiters       = 选「等大盲免费」者:本手是否入局取决于 BB 是否扫到其座(由 blinds.sweep_entrant 判)。
    # 资格细则:READY_TO_PLAY 且——非 new_here(上一手在局)直接发;bootstrap(整桌无已入局玩家)全员免付发;
    # new_here 在 waive 快照里免付发;new_here 付盲即玩(默认)发并 post 一个 BB;new_here 选等大盲进 waiters。
    ready = [
        (i, s)
        for i, s in enumerate(room.seats)
        if s is not None and room.users_in_room.get(s.nickname) is UserStatus.READY_TO_PLAY
    ]
    # bootstrap 看**整桌已占座位**(rules.md ① 行 60「桌上还没有任何已入局玩家」),不只 ready 子集:
    # 坐出/未 ready 的已入局(new_here=False)玩家仍堵掉新人的免费入局,守防躲盲不变量(行 46/50)。
    bootstrap = not any(s is not None and not s.new_here for s in room.seats)

    core_dealt: set[int] = set()
    paying_entrants: set[int] = set()
    waiters: set[int] = set()
    for i, s in ready:
        if not s.new_here or bootstrap or s.nickname in room.waive_entry_for:
            core_dealt.add(i)  # 已入局 / bootstrap / 免盲快照:免付入局(waive 优先于等大盲)
        elif not s.wait_for_big_blind:
            core_dealt.add(i)  # 付盲即玩(默认)
            paying_entrants.add(i)
        else:
            waiters.add(i)  # 等大盲:本手是否入局待 sweep_entrant 据庄/BB 判
    return core_dealt, paying_entrants, waiters


def _post_entry(player: Player, big_blind: int) -> None:
    # 入局 post(live):从 points 投一个 BB 进 bet_amount;短码即 all-in;不置 has_acted。
    amount = min(big_blind, player.points)
    player.points -= amount
    player.bet_amount += amount
    if player.points == 0:
        player.status = PlayerStatus.ALLIN


def _start_hand_events(
    room_name: str | None,
    room: Room,
    hand: Hand,
    small_blind: int,
    big_blind: int,
) -> list[Event]:
    # 投影为出站载荷(快照值,无活引用);顺序按 core.md §事件:HandStarted → HoleCards* → HandStatusChanged → TurnChanged
    assert room_name is not None  # 开局必有目标房
    views = tuple(
        PlayerView(
            seat_position=p.seat_position,
            nickname=p.nickname,
            points=p.points,
            bet_amount=p.bet_amount,
            status=p.status,
        )
        for p in hand.players
    )
    started = HandStarted(
        hand_seq=hand.seq,
        button_position=room.button_position,
        small_blind=small_blind,
        big_blind=big_blind,
        players=views,
        acting_position=hand.acting_position,
    )
    events: list[Event] = [Broadcast(room=room_name, msg=started)]
    for p in hand.players:
        assert p.hole_cards is not None  # 开局已发
        events.append(Personal(nick=p.nickname, msg=HoleCards(cards=p.hole_cards)))
    events.append(Broadcast(room=room_name, msg=HandStatusChanged(status=hand.status, board=())))
    if hand.acting_position is not None:
        acting = hand.players[hand.acting_position]
        events.append(TurnChanged(room=room_name, acting_nick=acting.nickname, epoch=hand.epoch))
    return events


# ── 玩家动作(PlayerAction)── rules.md ② 行动 + ③ 街推进/摊牌/结算 + core.md §2-4
def _player_action(work: Work, cmd: PlayerAction) -> ReduceResult:
    room = work.room
    if room is None:
        return [], Err(ErrorCode.NOT_IN_ROOM, "PlayerAction:发起人不在任何房间")
    hand = room.hand
    if hand is None:
        return [], Err(ErrorCode.NO_HAND, "无进行中手牌")
    pos = hand.acting_position
    if pos is None or hand.players[pos].nickname != cmd.origin:
        return [], Err(ErrorCode.NOT_YOUR_TURN, f"非 {cmd.origin} 的行动回合")
    actor = hand.players[pos]

    big_blind = blinds.BIG_BLIND_MULTIPLE * room.small_blind
    err = betting.apply_action(hand, actor, cmd.action, cmd.bet_amount, big_blind)
    if err is not None:
        return [], err  # 违规动作:丢工作副本、world 不动(失败安全)
    return _acted_events(work, hand, actor, cmd.action, big_blind), None


def _acted_events(
    work: Work, hand: Hand, actor: Player, action: PlayerActionType, big_blind: int
) -> list[Event]:
    # 行动者动作(自愿 PlayerAction / 超时默认 / 离桌 auto-fold)已落 hand 后的标准产出:
    # 先快照行动结果(街若关闭 settle_street 会清零 bet_amount),再推进,产 Broadcast(PlayerActed) + 推进事件。
    snapshot = (actor.bet_amount, actor.points, actor.status)
    follow = _advance(work, hand, big_blind)
    return [_acted_broadcast(work, hand, actor, action, snapshot), *follow]


def _acted_broadcast(
    work: Work,
    hand: Hand,
    actor: Player,
    action: PlayerActionType,
    snapshot: tuple[int, int, PlayerStatus],
) -> Broadcast:
    # 把行动结果(快照于推进前)+ 推进后底池/行动者投影为 Broadcast(PlayerActed)。
    bet_amount, points, status = snapshot
    return Broadcast(
        room=work.room_name,
        msg=PlayerActed(
            seat_position=actor.seat_position,
            nickname=actor.nickname,
            action=action,
            bet_amount=bet_amount,
            points=points,
            status=status,
            last_bet=hand.last_bet,
            pot=_pot(hand),
            acting_position=hand.acting_position,
        ),
    )


def _advance(work: Work, hand: Hand, big_blind: int) -> list[Event]:
    # 一次行动后的推进:只剩一人未弃 / 本街关闭 → 结算分支;否则换下一个 ACTIVE 行动者(epoch+1 + TurnChanged)。
    live = [p for p in hand.players if p.status is not PlayerStatus.FOLDED]
    if len(live) == 1 or betting.street_closed(hand):
        # len(live)==1:其余皆弃 → 无摊牌结束。显式判而非只靠 street_closed:残存者可能 has_acted=False、
        # bet 未跟平 → street_closed 为假却已该结束。两条触发路径:① 离桌 auto-fold 在「本可 check」时也弃
        # (rules.md ④);② 普通弃牌使存活者尚未行动(如 heads-up preflop SB 直接弃,BB 还没用选择权)。
        return _close_street(work, hand, big_blind)
    assert hand.acting_position is not None  # 未关 ⇒ 当前行动者存在(已在 _player_action 校验)
    hand.acting_position = betting.next_active_position(hand, hand.acting_position)
    hand.epoch += 1
    assert hand.acting_position is not None  # 未关 ⇒ 必有另一个 ACTIVE(见 rules.md ② street_closed)
    nxt = hand.players[hand.acting_position]
    return [TurnChanged(room=work.room_name, acting_nick=nxt.nickname, epoch=hand.epoch)]


def _close_street(work: Work, hand: Hand, big_blind: int) -> list[Event]:
    # 本街关闭:并入 contributed、重置本街计数,然后按 rules.md ③ 分支。
    betting.settle_street(hand, big_blind)
    live = [p for p in hand.players if p.status is not PlayerStatus.FOLDED]
    if len(live) == 1:
        return _settle_and_end(work, hand, reveal=False)  # 只剩一人未弃 → 无摊牌直接结束
    can_act = [p for p in hand.players if p.status is PlayerStatus.ACTIVE]
    if len(can_act) <= 1 or hand.status is HandStatus.RIVER:
        # 其余 all-in(无人再行动)→ 跑完公共牌摊牌;或 RIVER 正常关闭 → 摊牌
        return _settle_and_end(work, hand, reveal=True)
    # 否则进下一街,继续下注
    nxt = hand.status.next_status
    assert nxt is not None and nxt is not HandStatus.SHOWDOWN  # RIVER 已在上面分流
    _deal_board(hand, nxt)
    hand.status = nxt
    hand.acting_position = _postflop_first(hand, room=work.room)
    hand.epoch += 1
    assert hand.acting_position is not None  # ≥2 可行动 ⇒ postflop 首行动者存在
    return [
        Broadcast(room=work.room_name, msg=HandStatusChanged(status=nxt, board=tuple(_board(hand)))),
        TurnChanged(
            room=work.room_name,
            acting_nick=hand.players[hand.acting_position].nickname,
            epoch=hand.epoch,
        ),
    ]


def _settle_and_end(work: Work, hand: Hand, *, reveal: bool) -> list[Event]:
    # 摊牌(reveal)或无摊牌(单人)结算:边池(rules.md ③)→ 分配进 Player.points → 收尾。
    room = work.room
    assert room is not None
    live = [p for p in hand.players if p.status is not PlayerStatus.FOLDED]
    events: list[Event] = []
    if reveal:
        _run_out_board(hand)  # 补齐未发公共牌(已满则幂等);摊牌需完整 5 张
        board = _board(hand)
        strength: dict[str, int] = {}
        reveals: list[ShowdownReveal] = []
        for p in live:
            assert p.hole_cards is not None  # 在局者开局已发底牌
            strength[p.nickname] = evaluate(board, p.hole_cards)
            reveals.append(
                ShowdownReveal(seat_position=p.seat_position, nickname=p.nickname, hole_cards=p.hole_cards)
            )
        hand.status = HandStatus.SHOWDOWN
        events.append(
            Broadcast(room=work.room_name, msg=HandShowDown(board=tuple(board), reveals=tuple(reveals)))
        )
    else:
        strength = {live[0].nickname: 0}  # 仅一名未弃牌者,牌力无需比较

    payout = sidepot.settle(
        contributed=hand.contributed,
        live={p.nickname for p in live},
        strength=strength,
        seat_of={p.nickname: p.seat_position for p in hand.players},
        button_position=room.button_position,
        seat_size=len(room.seats),
    )
    for nick, amount in payout.total.items():
        _by_nick(hand, nick).points += amount  # 赢得 + 退还进本手剩余筹码,随后由 finalize 还回座位

    events += _finalize_hand(work, hand, payout)
    return events


def _finalize_hand(work: Work, hand: Hand, payout: sidepot.Payout) -> list[Event]:
    # 收尾:各 Player.points 还回 Seat、清锁筹、PLAYING→SITTING_IN/SITTING_OUT;产 HandEnded + Persist;
    # 最后驱逐本手 leaving(座位此时已含还回的剩余栈/赢得);ClearAction 停行动倒计时。
    assert work.room_name is not None
    room = work.room
    assert room is not None
    participants: list[ParticipantWrite] = []
    for p in hand.players:
        s = room.seats[p.seat_position]
        assert s is not None
        initial = s.in_game_points  # 开局锁入快照
        s.points += p.points  # p.points 已含赢得 / 退还
        s.in_game_points = 0
        # 手牌记录含全部参与者(离桌者也参与了本手)
        participants.append(
            ParticipantWrite(uid=work.users[p.nickname].uid, initial_points=initial, final_points=s.points)
        )
        # UserStatus 收尾:离桌者状态不动(随后 _evict 移除);局中请求坐出者转 SITTING_OUT;其余 PLAYING→SITTING_IN
        if p.nickname in room.leaving:
            continue
        if room.users_in_room.get(p.nickname) is UserStatus.PLAYING:
            room.users_in_room[p.nickname] = (
                UserStatus.SITTING_OUT if p.nickname in room.sitting_out_next else UserStatus.SITTING_IN
            )

    record = HandRecordWrite(
        dedupe_key=f"{work.room_name}:{hand.seq}",
        start_time=hand.start_time,
        final_pot=sum(pot.amount for pot in payout.pots),
        participants=tuple(participants),
    )
    ended = HandEnded(
        winnings=tuple(NickAmount(nickname=n, amount=a) for n, a in payout.winnings.items()),
        refunds=tuple(NickAmount(nickname=n, amount=a) for n, a in payout.refunds.items()),
    )

    hand.status = HandStatus.ENDING
    hand.acting_position = None
    room.hand = None
    room.status = RoomStatus.PENDING_START

    events: list[Event] = [Broadcast(room=work.room_name, msg=ended), Persist(payload=record)]
    # 驱逐本手离桌者:退座位剩余筹码回全局积分 + 释座 + 移出(sorted 使产出顺序确定,便于断言)
    for nick in sorted(room.leaving):
        events += _evict(work, room, nick)
    room.leaving = set()
    room.sitting_out_next = set()
    events.append(ClearAction(room=work.room_name))
    return events


# ── 纯计算 helper(发牌 / 位次 / 底池)──
def _deal_board(hand: Hand, status: HandStatus) -> None:
    # 发该街公共牌(不烧牌):从 hand.deck 顺取。_start_hand 已校验牌堆够 2N+5,故不缺牌。
    if status is HandStatus.FLOP:
        hand.flop = (hand.deck.pop(0), hand.deck.pop(0), hand.deck.pop(0))
    elif status is HandStatus.TURN:
        hand.turn = hand.deck.pop(0)
    elif status is HandStatus.RIVER:
        hand.river = hand.deck.pop(0)


def _run_out_board(hand: Hand) -> None:
    # 一次发齐所有未发公共牌(≤1 人可行动 / born-all-in 时);已发的不动(幂等)。
    if hand.flop is None:
        _deal_board(hand, HandStatus.FLOP)
    if hand.turn is None:
        _deal_board(hand, HandStatus.TURN)
    if hand.river is None:
        _deal_board(hand, HandStatus.RIVER)


def _board(hand: Hand) -> list[Card]:
    # 当前已发公共牌(flop → turn → river 顺序)。
    cards: list[Card] = list(hand.flop) if hand.flop is not None else []
    if hand.turn is not None:
        cards.append(hand.turn)
    if hand.river is not None:
        cards.append(hand.river)
    return cards


def _postflop_first(hand: Hand, *, room: Room | None) -> int | None:
    # postflop 首行动 = 庄家下一位起第一个 ACTIVE(≥3 人为 SB 位;heads-up 为 BB);见 rules.md ①。
    assert room is not None
    button_idx = next(i for i, p in enumerate(hand.players) if p.seat_position == room.button_position)
    return betting.next_active_position(hand, button_idx)


def _pot(hand: Hand) -> int:
    # 当前总底池:已并入的 contributed + 各人本街尚未并入的 bet_amount。
    return sum(hand.contributed.values()) + sum(p.bet_amount for p in hand.players)


def _by_nick(hand: Hand, nick: str) -> Player:
    return next(p for p in hand.players if p.nickname == nick)


# ── 局中生命周期(Timeout / Disconnect / LeaveRoom / Cleanup / SetUserStatus)── rules.md ④ + timer.md
def _timeout(work: Work, cmd: Timeout) -> ReduceResult:
    # 行动超时:staleness(无手 / epoch 不符 / 行动者非 cmd.nick)→ 忽略(系统命令 origin=None,过期不报错);
    # 仍是该回合该玩家 → 默认动作:能 check 则 check,否则 fold(timer.md「能 check 则 check,否则 fold」)。
    room = work.room
    if room is None or room.hand is None:
        return [], None  # 不在房 / 无手牌 → 过期忽略
    hand = room.hand
    if hand.epoch != cmd.epoch:
        return [], None  # 回合早已推进(epoch 不符)→ 过期忽略
    pos = hand.acting_position
    if pos is None or hand.players[pos].nickname != cmd.nick:
        return [], None  # 行动者已变 → 过期忽略
    actor = hand.players[pos]

    big_blind = blinds.BIG_BLIND_MULTIPLE * room.small_blind
    action = PlayerActionType.CHECK if actor.bet_amount == hand.last_bet else PlayerActionType.FOLD
    err = betting.apply_action(hand, actor, action, None, big_blind)
    assert err is None  # 默认动作必合法(check 当且仅当已跟平,否则 fold);非法即 bug
    return _acted_events(work, hand, actor, action, big_blind), None


def _connect(work: Work, cmd: Connect) -> ReduceResult:
    # 接入大厅(连接绑 nick,不绑房间)。纯大厅用户(不在 world.users)→ core 无事;
    # 已在某房且 OFFLINE → 重连:恢复在线 + 私发 StateSnapshot 对齐;在线(预置 WATCHING / 重复 Connect)→ 幂等忽略。
    room = work.room
    nick = cmd.nick
    if room is None or nick not in room.users_in_room:
        return [], None  # 纯大厅接入:进房 + 载入积分走 JoinRoom(lobby.md / user.md),core 此处无事
    if room.users_in_room[nick] is not UserStatus.OFFLINE:
        return [], None  # 已在线(非重连)→ 不重发快照
    # 重连:OFFLINE → 按 world 真相恢复状态(座位/筹码在 OFFLINE 期间保留),私发整桌快照
    restored = _reconnect_status(room, nick)
    room.users_in_room[nick] = restored
    snap = _state_snapshot(room, work.room_name, for_nick=nick)
    changed = UserStatusChanged(nickname=nick, status=restored, seat_position=_seat_of(room, nick))
    return [Broadcast(room=work.room_name, msg=changed), Personal(nick=nick, msg=snap)], None


def _reconnect_status(room: Room, nick: str) -> UserStatus:
    # 重连恢复目标状态(不存断线前状态,从 world 推断):在进行中手牌(是其 Player)→ PLAYING;
    # 有座但不在手 → SITTING_IN(需重新 ready);无座 → WATCHING。皆是合法 OFFLINE→* 转移(见 enums)。
    if room.hand is not None and any(p.nickname == nick for p in room.hand.players):
        return UserStatus.PLAYING
    if _seat_of(room, nick) is not None:
        return UserStatus.SITTING_IN
    return UserStatus.WATCHING


# ── 进房(JoinRoom)── lobby.md / user.md:大厅 → 房间,装入 world.users 为 WATCHING + 私发快照
def _join_room(work: Work, cmd: JoinRoom) -> ReduceResult:
    # uid/loaded 由 shell 读 DB 带入(user.md);校验房存在 + 单房间约束;装 UserState + WATCHING;
    # 产 Broadcast(UserJoined) 给全房 + Personal(StateSnapshot) 给进房者(观战 → 无自有底牌)。
    room = work.room
    nick = cmd.origin
    room_name = work.room_name
    if nick is None:
        return [], Err(ErrorCode.INTERNAL, "JoinRoom 缺 origin")
    if room is None or room_name is None:
        return [], Err(ErrorCode.NO_SUCH_ROOM, f"无此房间 {cmd.room}")
    if nick in work.users:
        return [], Err(ErrorCode.ALREADY_IN_ROOM, f"{nick} 已在房间 {work.users[nick].room},先 LeaveRoom")
    # ROOM_FULL 暂不强制(v1:不限观战;座位可用性由 SitDown 的 SEAT_TAKEN 兜,见 lobby.md / changes/0022)
    work.users[nick] = UserState(uid=cmd.uid, nickname=nick, points=cmd.loaded, room=room_name)
    room.users_in_room[nick] = UserStatus.WATCHING
    snap = _state_snapshot(room, room_name, for_nick=nick)  # 进房者观战 → your_hole_cards=None
    return [
        Broadcast(room=room_name, msg=UserJoined(nickname=nick)),
        Personal(nick=nick, msg=snap),
    ], None


def _state_snapshot(room: Room, room_name: str | None, *, for_nick: str) -> StateSnapshot:
    # 整桌当前态投影(逐收件人):seats 仅已占座(各带 chips/status);watchers 无座者;进行中手牌投影 board/pot/players;
    # your_hole_cards 仅收件人自己的底牌(他人底牌结构性缺位,见 wire.md 隐私)。快照值,不持域活引用(不变量 7)。
    assert room_name is not None  # 进房/重连必有目标房
    hand = room.hand
    in_hand: dict[str, Player] = {p.nickname: p for p in hand.players} if hand is not None else {}
    seats = tuple(
        SeatView(
            seat_position=i,
            nickname=s.nickname,
            status=room.users_in_room.get(s.nickname, UserStatus.SITTING_IN),
            points=in_hand[s.nickname].points if s.nickname in in_hand else s.points,
            new_here=s.new_here,
        )
        for i, s in enumerate(room.seats)
        if s is not None
    )
    seated = {s.nickname for s in room.seats if s is not None}
    watchers = tuple(sorted(n for n in room.users_in_room if n not in seated))
    big_blind = blinds.BIG_BLIND_MULTIPLE * room.small_blind
    if hand is None:
        return StateSnapshot(
            room=room_name, max_seats=len(room.seats), button_position=room.button_position,
            small_blind=room.small_blind, big_blind=big_blind, room_status=room.status,
            seats=seats, watchers=watchers, hand_status=None, board=(), pot=0,
            acting_position=None, players=(), your_hole_cards=None,
        )
    players = tuple(
        PlayerView(seat_position=p.seat_position, nickname=p.nickname, points=p.points,
                   bet_amount=p.bet_amount, status=p.status)
        for p in hand.players
    )
    own = in_hand[for_nick].hole_cards if for_nick in in_hand else None
    return StateSnapshot(
        room=room_name, max_seats=len(room.seats), button_position=room.button_position,
        small_blind=room.small_blind, big_blind=big_blind, room_status=room.status,
        seats=seats, watchers=watchers, hand_status=hand.status, board=tuple(_board(hand)),
        pot=_pot(hand), acting_position=hand.acting_position, players=players, your_hole_cards=own,
    )


def _disconnect(work: Work, cmd: Disconnect) -> ReduceResult:
    # ws 断开:在房则标 OFFLINE 保座(timer.md「断开 ≠ 离场」,清理等 Cleanup);在大厅则无 world 变化。
    # 在局者仍是 Player,轮到他时由行动倒计时 _timeout 自动 fold(牌局不卡)。
    room = work.room
    if room is None or cmd.nick not in room.users_in_room:
        return [], None  # 大厅断开:无 world 状态可改
    current = room.users_in_room[cmd.nick]
    if current is UserStatus.OFFLINE:
        return [], None  # 已离线(顶替/重复 Disconnect)→ 幂等忽略
    if not current.can_change_to(UserStatus.OFFLINE):
        return [], Err(ErrorCode.INVALID_STATUS_TRANSITION, f"{cmd.nick} {current}→OFFLINE 非法")
    room.users_in_room[cmd.nick] = UserStatus.OFFLINE
    msg = UserStatusChanged(
        nickname=cmd.nick, status=UserStatus.OFFLINE, seat_position=_seat_of(room, cmd.nick)
    )
    return [Broadcast(room=work.room_name, msg=msg)], None


def _leave_room(work: Work, cmd: LeaveRoom) -> ReduceResult:
    # 退房回大厅:在当前手内 → 标 leaving + 仍 ACTIVE 则即时 auto-fold,手尾结算后驱逐;
    # 不在当前手(观战/坐出/两手之间)→ 立即驱逐。
    room = work.room
    nick = cmd.origin
    if room is None or nick is None or nick not in room.users_in_room:
        return [], Err(ErrorCode.NOT_IN_ROOM, f"{nick} 不在任何房间,无法 LeaveRoom")
    return _begin_leave(work, room, nick), None


def _cleanup(work: Work, cmd: Cleanup) -> ReduceResult:
    # 占座到期清理:staleness——仅当仍 OFFLINE 才退筹释座(timer.md;已重连则忽略)。
    room = work.room
    if room is None or cmd.nick not in room.users_in_room:
        return [], None  # 不在房 → 过期忽略
    if room.users_in_room[cmd.nick] is not UserStatus.OFFLINE:
        return [], None  # 已重连(非 OFFLINE)→ 忽略
    return _begin_leave(work, room, cmd.nick), None


def _begin_leave(work: Work, room: Room, nick: str) -> list[Event]:
    # 离房(LeaveRoom 主动 / Cleanup 占座到期)统一处理:在当前手内则标 leaving、仍 ACTIVE 则即时 auto-fold
    # (rules.md ④「即便能 check 也按弃」),手尾 _finalize_hand 结算后 _evict;不在当前手则立即 _evict。
    hand = room.hand
    player = _player_in_hand(hand, nick) if hand is not None else None
    if player is None or hand is None:
        # 观战/坐出/两手之间:座位筹码未锁入,直接驱逐;离场者若是投票人则重算免盲投票(rules.md ①.15)
        return _evict(work, room, nick) + _maybe_resolve_entry_vote(work, room)

    room.leaving.add(nick)  # 在手内:延到手尾驱逐(已投池中筹码不能抽走,守恒)
    if player.status is not PlayerStatus.ACTIVE:
        return []  # FOLDED 已出局 / ALLIN 已全押(不能再 fold,仍可赢);手尾结算后 _evict

    big_blind = blinds.BIG_BLIND_MULTIPLE * room.small_blind
    is_acting = hand.acting_position is not None and hand.players[hand.acting_position] is player
    player.status = PlayerStatus.FOLDED  # auto-fold:即便能 check 也按弃(他要走)
    if is_acting:
        return _acted_events(work, hand, player, PlayerActionType.FOLD, big_blind)  # 行动者:正常推进(含 fold-to-one 结束)

    # 非行动者离桌:当前行动者继续、不推进 turn;仅当因此只剩 1 名未弃者才结束本手。
    # 快照行动结果于结算前(_close_street 的 settle_street 会清零 bet_amount),与 _acted_events 同。
    snapshot = (player.bet_amount, player.points, player.status)
    live = [p for p in hand.players if p.status is not PlayerStatus.FOLDED]
    if len(live) == 1:
        follow = _close_street(work, hand, big_blind)
        return [_acted_broadcast(work, hand, player, PlayerActionType.FOLD, snapshot), *follow]
    return [_acted_broadcast(work, hand, player, PlayerActionType.FOLD, snapshot)]


def _release_seat(work: Work, room: Room, nick: str) -> Persist | None:
    # 腾座:退座位筹码回全局积分 + 释座,产 Persist(PointsWrite);无座位(观战者)→ None。
    # _evict(离房驱逐)与起身(→WATCHING)共用——任何「腾座」都把座位筹码还回全局(user.md)。
    seat_idx = _seat_of(room, nick)
    if seat_idx is None:
        return None
    seat = room.seats[seat_idx]
    assert seat is not None
    user = work.users[nick]
    user.points += seat.points  # 座位筹码退回全局积分(对局内流转此前已结算回 Seat.points)
    seat.points = 0
    room.seats[seat_idx] = None  # 释座
    return Persist(payload=PointsWrite(uid=user.uid, points=user.points))


def _evict(work: Work, room: Room, nick: str) -> list[Event]:
    # 驱逐离房者:腾座(_release_seat,退筹 Persist 先于 del)→ 移出 users_in_room →
    # del work.users[nick] → UserLeft(Broadcast 给留下者 + Personal 回执本人)。
    seat_idx = _seat_of(room, nick)  # 释座前取座位号供 UserLeft
    events: list[Event] = []
    pw = _release_seat(work, room, nick)
    if pw is not None:
        events.append(pw)
    room.users_in_room.pop(nick, None)
    del work.users[nick]  # 彻底离场回大厅;单房间约束 ⇒ 驱逐无歧义(user.md)
    left = UserLeft(nickname=nick, seat_position=seat_idx)
    # 离开者已不在 users_in_room ⇒ Broadcast 只到留下者;回执本人走 Personal(connection.md)
    events.append(Broadcast(room=work.room_name, msg=left))
    events.append(Personal(nick=nick, msg=left))
    return events


def _sit_down(work: Work, cmd: SitDown) -> ReduceResult:
    # 观战 → 就座:占一个空座,新建 Seat(new_here=True → 下一手付盲即玩/等大盲,防躲盲,见 rules.md ①)。
    room = work.room
    nick = cmd.origin
    if room is None or nick is None or nick not in room.users_in_room:
        return [], Err(ErrorCode.NOT_IN_ROOM, f"{nick} 不在任何房间")
    current = room.users_in_room[nick]
    if current is not UserStatus.WATCHING:
        return [], Err(ErrorCode.INVALID_STATUS_TRANSITION, f"{nick} 当前 {current},仅观战者可入座")
    if not (0 <= cmd.seat < len(room.seats)):
        return [], Err(ErrorCode.NOT_YOUR_SEAT, f"座位号 {cmd.seat} 越界")
    if room.seats[cmd.seat] is not None:
        return [], Err(ErrorCode.SEAT_TAKEN, f"座位 {cmd.seat} 已被占用")
    if not current.can_change_to(UserStatus.SITTING_IN):
        return [], Err(ErrorCode.INVALID_STATUS_TRANSITION, f"{nick} {current}→SITTING_IN 非法")
    # new_here=True(防躲盲);wait_for_big_blind 由玩家在 SitDown 声明入局方式(rules.md ①);买入后再 ready
    room.seats[cmd.seat] = Seat(nickname=nick, points=0, wait_for_big_blind=cmd.wait_for_big_blind)
    room.users_in_room[nick] = UserStatus.SITTING_IN
    msg = UserStatusChanged(nickname=nick, status=UserStatus.SITTING_IN, seat_position=cmd.seat)
    return [Broadcast(room=work.room_name, msg=msg)], None


def _buy_in(work: Work, cmd: BuyIn) -> ReduceResult:
    # 全局积分 → 座位筹码(纯内存转账,user.md);失败丢工作副本即回滚(无需 BuyInFailed)。
    # 校验:自己的座位 + 非局中(手内筹码已锁) + 正额 + 余额够。上下限随 gameconfig 收编后补(P8)。
    room = work.room
    nick = cmd.origin
    if room is None or nick is None or nick not in room.users_in_room:
        return [], Err(ErrorCode.NOT_IN_ROOM, f"{nick} 不在任何房间")
    if not (0 <= cmd.seat < len(room.seats)):
        return [], Err(ErrorCode.NOT_YOUR_SEAT, f"座位号 {cmd.seat} 越界")
    seat = room.seats[cmd.seat]
    if seat is None or seat.nickname != nick:
        return [], Err(ErrorCode.NOT_YOUR_SEAT, f"座位 {cmd.seat} 不属于 {nick}")
    if room.users_in_room[nick] is UserStatus.PLAYING:
        return [], Err(ErrorCode.HAND_IN_PROGRESS, "手牌进行中不能买入(筹码已锁入本手)")
    if cmd.amount <= 0:
        return [], Err(ErrorCode.INVALID_BUY_IN, f"买入额须为正(amount={cmd.amount})")
    user = work.users[nick]
    if cmd.amount > user.points:
        return [], Err(ErrorCode.INSUFFICIENT_POINTS, f"have={user.points} need={cmd.amount}")
    user.points -= cmd.amount
    seat.points += cmd.amount
    persist = Persist(payload=PointsWrite(uid=user.uid, points=user.points))
    msg = PlayerBoughtIn(nickname=nick, seat_position=cmd.seat, amount=cmd.amount, seat_points=seat.points)
    return [Broadcast(room=work.room_name, msg=msg), persist], None


# ── 免盲投票(OpenFreeEntryVote / VoteFreeEntry)── rules.md ① 行 65-96(①.12-15)
def _open_free_entry_vote(work: Work, cmd: OpenFreeEntryVote) -> ReduceResult:
    # 为当前 new_here 玩家开一次免盲投票。门槛:在房 + 有候选 + 有合格投票人(后者兼挡真空通过,见决策 3/5);
    # 已有进行中投票则幂等 no-op(不重置已有 approvals,防反复开票刷票)。
    room = work.room
    nick = cmd.origin
    if room is None or nick is None or nick not in room.users_in_room:
        return [], Err(ErrorCode.NOT_IN_ROOM, f"{nick} 不在任何房间,无法开免盲投票")
    if room.entry_vote is not None:
        return [], None  # 已有投票进行中 → 幂等忽略,保留现有 approvals
    candidates = _free_entry_candidates(room)
    voters = _voters(room)
    if not candidates or not voters:
        return [], Err(
            ErrorCode.CANNOT_OPEN_VOTE, f"无可免盲候选或无合格投票人(candidates={len(candidates)} voters={len(voters)})"
        )
    # 冻结候选:approver 的同意只针对开票这批 new_here。后来就座者不在 vote.candidates → 不蹭车;
    # 原候选离场 → 票失对象(见 _finish_entry_vote)。否则旧 approvals 会被复用去免一个无人投过的新候选(防躲盲)。
    room.entry_vote = EntryVote(candidates=frozenset(candidates))
    msg = FreeEntryVoteUpdated(candidates=tuple(sorted(candidates)), voters=tuple(sorted(voters)), approvals=())
    return [Broadcast(room=work.room_name, msg=msg)], None


def _vote_free_entry(work: Work, cmd: VoteFreeEntry) -> ReduceResult:
    # 合格投票人表态:reject 即失败清空;approve 累加后若全票(非空)则通过快照,否则广播进度。
    room = work.room
    nick = cmd.origin
    if room is None or nick is None or nick not in room.users_in_room:
        return [], Err(ErrorCode.NOT_IN_ROOM, f"{nick} 不在任何房间")
    vote = room.entry_vote
    if vote is None:
        return [], Err(ErrorCode.NO_VOTE_IN_PROGRESS, f"{nick} 投票但当前无进行中免盲投票")
    if nick not in _voters(room):
        return [], Err(ErrorCode.NOT_A_VOTER, f"{nick} 非合格投票人(须已入局且 READY_TO_PLAY)")
    if cmd.approve:
        vote.approvals.add(nick)
    else:
        vote.rejected = True  # 一票否决(rules.md ① 行 73)
    closed = _finish_entry_vote(work, room)
    if closed is not None:
        return closed, None
    # 未终结 → 广播当前进度(候选取「开票冻结集 ∩ 仍在的 new_here」;赞成只算仍合格投票人,剔除已离场的陈旧 approval)
    voters = _voters(room)
    msg = FreeEntryVoteUpdated(
        candidates=tuple(sorted(vote.candidates & _free_entry_candidates(room))),
        voters=tuple(sorted(voters)),
        approvals=tuple(sorted(vote.approvals & voters)),
    )
    return [Broadcast(room=work.room_name, msg=msg)], None


def _voters(room: Room) -> set[str]:
    # 合格投票人(rules.md ① 行 69):已入局(非 new_here)且 READY_TO_PLAY 的座位——其 EV 受「放人免费进来」影响。
    return {
        s.nickname
        for s in room.seats
        if s is not None
        and not s.new_here
        and room.users_in_room.get(s.nickname) is UserStatus.READY_TO_PLAY
    }


def _free_entry_candidates(room: Room) -> set[str]:
    # 受这次入局盲影响的新玩家 = 当前 new_here 座位;通过时快照进 waive_entry_for(rules.md ① / ①.14 防蹭车)。
    return {s.nickname for s in room.seats if s is not None and s.new_here}


def _finish_entry_vote(work: Work, room: Room) -> list[Event] | None:
    # 投票终结判定:rejected → 失败;候选全离场(冻结集已无在场 new_here)→ 票失对象、失败;
    # voters 非空且全 approve → 通过(免掉仍在的原候选)。三者皆清空 entry_vote;未终结返 None(供静默重算)。
    vote = room.entry_vote
    assert vote is not None
    if vote.rejected:
        room.entry_vote = None
        return [Broadcast(room=work.room_name, msg=FreeEntryVoteClosed(passed=False, waived=()))]
    waived = vote.candidates & _free_entry_candidates(room)  # 冻结候选 ∩ 仍在的 new_here(离场者剔除、后来者不入)
    if not waived:
        # 原候选全离场 / 已被 StartHand 消费 → 投票失去对象,失败清空(与开票门槛对称,不产无意义 passed=True)
        room.entry_vote = None
        return [Broadcast(room=work.room_name, msg=FreeEntryVoteClosed(passed=False, waived=()))]
    voters = _voters(room)
    # 真空守门(决策 3):voters 非空才可能通过——否则 ∅⊆approvals 会瞬间「全票」误免;bootstrap 本就免费、无需投票。
    if voters and voters <= vote.approvals:
        room.waive_entry_for |= waived  # 免掉仍在的原候选(union,不覆盖既有快照);_start_hand 消费并清空
        room.entry_vote = None
        return [Broadcast(room=work.room_name, msg=FreeEntryVoteClosed(passed=True, waived=tuple(sorted(waived))))]
    return None


def _maybe_resolve_entry_vote(work: Work, room: Room) -> list[Event]:
    # 投票人集合缩小(离场/坐出/起身)后重算(rules.md ①.15):仅当因此达成全票才通过(产 Closed),否则不产事件。
    if room.entry_vote is None:
        return []
    return _finish_entry_vote(work, room) or []


# ── 房间聊天(RoomChat)── messaging.md §房间聊天:只读命令,产 Broadcast(ChatMessage)
def _room_chat(work: Work, cmd: RoomChat) -> ReduceResult:
    # 只读:不改任何游戏状态(messaging.md 契约 7),仅校验发送者在房 → 广播给全房(派发按 users_in_room,含观战者)。
    # 文本非空/长度/限速归 shell 文本防护(同限速,见 messaging.md),core 只认「在不在房」这一游戏判据。
    room = work.room
    nick = cmd.origin
    if room is None or nick is None or nick not in room.users_in_room:
        return [], Err(ErrorCode.NOT_IN_ROOM, f"{nick} 不在任何房间,无法房聊")
    return [Broadcast(room=work.room_name, msg=ChatMessage(from_nick=nick, text=cmd.text))], None


def _set_user_status(work: Work, cmd: SetUserStatus) -> ReduceResult:
    # 本簇仅处理「就座内」状态切换(0014):局中坐出延到手尾 + 就座内 ready/sit-out 切换。
    # 起身离座(→WATCHING)/ 入座 / 买入归后续座位簇(暂以 INTERNAL 占位,沿用 reduce case _ 约定)。
    room = work.room
    nick = cmd.origin
    if room is None or nick is None or nick not in room.users_in_room:
        return [], Err(ErrorCode.NOT_IN_ROOM, f"{nick} 不在任何房间")
    current = room.users_in_room[nick]
    new_status = cmd.status

    if current is UserStatus.PLAYING:
        # 局中:只接受「坐出」,延到手尾生效(rules.md ④);本手 PLAYING 不变、照常打完
        if new_status is UserStatus.SITTING_OUT:
            room.sitting_out_next.add(nick)
            return [], None  # 意图已记;手尾 _finalize_hand 转 SITTING_OUT(无即时 wire 回执)
        return [], Err(ErrorCode.INVALID_STATUS_TRANSITION, f"{nick} 局中仅可请求 SITTING_OUT(当前 {new_status})")

    if new_status not in _SELF_STATUS_TARGETS:
        return [], Err(ErrorCode.INTERNAL, f"SetUserStatus {current}→{new_status} 暂未实现(入座走 SitDown)")
    if not current.userself_can_change_to(new_status):
        return [], Err(ErrorCode.INVALID_STATUS_TRANSITION, f"{nick} {current}→{new_status} 非法")
    events: list[Event] = []
    if new_status is UserStatus.WATCHING:
        # 起身离座:腾座 + 退座位筹码回全局积分(user.md「腾座即退筹」第三出入口);非局中才到此(PLAYING 臂已拦)
        pw = _release_seat(work, room, nick)
        if pw is not None:
            events.append(pw)
    room.users_in_room[nick] = new_status
    seat_idx = _seat_of(room, nick)  # 起身后座位已腾空 → None
    events.append(Broadcast(room=work.room_name, msg=UserStatusChanged(nickname=nick, status=new_status, seat_position=seat_idx)))
    # 该转移可能改变投票人集合(如 voter 坐出/起身退出投票)→ 重算免盲投票(rules.md ①.15)
    events += _maybe_resolve_entry_vote(work, room)
    return events, None


def _seat_of(room: Room, nick: str) -> int | None:
    # 返回 nick 占用的座位下标;未就座(大厅/观战)为 None。
    for i, s in enumerate(room.seats):
        if s is not None and s.nickname == nick:
            return i
    return None


def _player_in_hand(hand: Hand | None, nick: str) -> Player | None:
    # 返回 nick 在当前手内的 Player;不在本手(观战/坐出/两手之间)为 None。
    if hand is None:
        return None
    for p in hand.players:
        if p.nickname == nick:
            return p
    return None
