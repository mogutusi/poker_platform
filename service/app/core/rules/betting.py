# rules.md ② 下注规则(三动作 / min-raise / 重开 / 街关闭判据)

from app.core.domain import Hand, Player
from app.core.enums import PlayerActionType, PlayerStatus
from app.core.errors import Err, ErrorCode


def apply_action(
    hand: Hand,
    player: Player,
    action: PlayerActionType,
    bet_amount: int | None,
    big_blind: int,
) -> Err | None:
    # 前置:玩家须仍可行动(ACTIVE);非 ACTIVE 不该轮到他
    if player.status is not PlayerStatus.ACTIVE:
        return Err(ErrorCode.ILLEGAL_ACTION, f"{player.nickname} 状态 {player.status} 不可行动")

    if action is PlayerActionType.FOLD:
        # 仅当有注要跟才允许弃(无注该 check)
        if player.bet_amount >= hand.last_bet:
            return Err(ErrorCode.ILLEGAL_ACTION, "无需跟注时应 check 而非 fold")
        player.status = PlayerStatus.FOLDED
        return None

    if action is PlayerActionType.CHECK:
        if player.bet_amount != hand.last_bet:
            return Err(ErrorCode.ILLEGAL_ACTION, f"需跟到 {hand.last_bet} 才能 check")
        player.has_acted = True
        return None

    if action is PlayerActionType.BET:
        return _apply_bet(hand, player, bet_amount, big_blind)

    return Err(ErrorCode.ILLEGAL_ACTION, f"未知动作 {action}")


def min_raise_target(hand: Hand, big_blind: int) -> int:
    # 自愿加注的合法下限(本街目标总额),rules.md ②:`last_bet + max(last_raise_size, BB)`。
    # **校验与上 wire 的投影共用这一份**(见 changes/0088):公式只此一处,显示的下限与判定的下限
    # 不可能对不上。注意 all-in 不受此限(amount == stack 即便不足一个完整加注也放行,决策 2)。
    return hand.last_bet + max(hand.last_raise_size, big_blind)


def _apply_bet(hand: Hand, player: Player, bet_amount: int | None, big_blind: int) -> Err | None:
    if bet_amount is None:
        return Err(ErrorCode.ILLEGAL_ACTION, "BET 必须带 bet_amount")
    amount = bet_amount  # 本街目标总额(合并跟注/加注)
    stack = player.points + player.bet_amount  # 本街可达上限(含已投入)
    if amount > stack:
        return Err(ErrorCode.ILLEGAL_ACTION, f"目标额 {amount} 超过可用 {stack}")
    is_all_in = amount == stack
    if amount < hand.last_bet and not is_all_in:
        return Err(ErrorCode.ILLEGAL_ACTION, f"目标额 {amount} 不足跟注 {hand.last_bet} 且非 all-in")

    old_last_bet = hand.last_bet
    if amount > old_last_bet:
        # 自愿加注须够 min-raise;all-in 超注即便不足完整加注也放行并重开(决策 2)
        if not is_all_in:
            min_target = min_raise_target(hand, big_blind)  # 此时 hand.last_bet 仍是 old_last_bet
            if amount < min_target:
                return Err(ErrorCode.ILLEGAL_ACTION, f"加注须到 {min_target}(min-raise)")
        hand.last_raise_size = max(hand.last_raise_size, amount - old_last_bet)  # 取 max 不缩小
        hand.last_bet = amount
        _invest(player, amount)
        player.has_acted = True
        if is_all_in:
            player.status = PlayerStatus.ALLIN
        _reopen(hand, player)  # 其余 ACTIVE 须回应
        return None

    # amount <= old_last_bet:跟注 或 短 all-in(跟不满)——都不重开、不改 last_bet
    _invest(player, amount)
    player.has_acted = True
    if is_all_in:
        player.status = PlayerStatus.ALLIN
    return None


def _invest(player: Player, amount: int) -> None:
    # 从 points 补足到本街目标总额 amount
    player.points -= amount - player.bet_amount
    player.bet_amount = amount


def _reopen(hand: Hand, raiser: Player) -> None:
    # 重开行动:除加注者外所有 ACTIVE 玩家须重新回应
    for p in hand.players:
        if p is not raiser and p.status is PlayerStatus.ACTIVE:
            p.has_acted = False


def street_closed(hand: Hand) -> bool:
    # 所有仍可行动(ACTIVE)者都已自愿行动且跟平 → 关闭;can_act 为空(全 all-in/单人)真空为真
    can_act = [p for p in hand.players if p.status is PlayerStatus.ACTIVE]
    return all(p.has_acted and p.bet_amount == hand.last_bet for p in can_act)


def settle_street(hand: Hand, big_blind: int) -> None:
    # 街关闭后:各 bet_amount 并入 contributed 并清零;重置 last_bet/last_raise_size/has_acted
    for p in hand.players:
        if p.bet_amount:
            hand.contributed[p.nickname] = hand.contributed.get(p.nickname, 0) + p.bet_amount
            p.bet_amount = 0
        p.has_acted = False
    hand.last_bet = 0
    hand.last_raise_size = big_blind


def next_active_position(hand: Hand, from_idx: int) -> int | None:
    # players 是行动序;返回 from_idx 之后(环形)下一个**其他** ACTIVE 的下标。
    # 无其他 ACTIVE 时返回 None;是否结束/进街由 reduce 先查 street_closed 决定,不在此判
    size = len(hand.players)
    for step in range(1, size):
        idx = (from_idx + step) % size
        if hand.players[idx].status is PlayerStatus.ACTIVE:
            return idx
    return None
