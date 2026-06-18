# rules.md ① 开局定位与下盲(定庄/盲位/heads-up/排座 + 下盲);入局资格/免盲投票随 reduce 合篇

from app.core.domain import Hand, Player
from app.core.enums import PlayerStatus

BIG_BLIND_MULTIPLE = 2  # 大盲 = 2×小盲(本平台定义,见 rules.md ① / domain.Seat)


def advance_button(current_button: int, eligible: set[int]) -> int:
    # 庄移到下一个在局座位(环形);eligible = 本手在局(被发牌)的座位下标集合
    seats = sorted(eligible)
    for s in seats:
        if s > current_button:
            return s
    return seats[0]  # 环形回绕:无更大下标则取最小


def seat_order(button: int, eligible: set[int]) -> list[int]:
    # 行动序座位下标:players[0]=SB、players[1]=BB。
    # ≥3 人「庄之后→庄」(SB=庄下一位、庄在序尾);heads-up 特例:players[0]=庄=SB、players[1]=另一人=BB。
    seats = sorted(eligible)
    if len(seats) == 2:
        other = seats[1] if seats[0] == button else seats[0]
        return [button, other]
    i = seats.index(button)
    return seats[i + 1:] + seats[: i + 1]


def post_blinds(hand: Hand, small_blind: int) -> None:
    # players[0] 投小盲、players[1] 投大盲;下盲只进 bet_amount(本街),街结束才并入 contributed(见 rules.md ③)。
    # 不置 has_acted:SB/BB 还没自愿行动,尤其 BB 保留 preflop 选择权(见 rules.md ②)。
    big_blind = BIG_BLIND_MULTIPLE * small_blind
    _post(hand.players[0], small_blind)
    _post(hand.players[1], big_blind)
    hand.last_bet = big_blind  # 本街需跟到 BB
    hand.last_raise_size = big_blind  # min-raise 下限基准(见 rules.md ②)


def _post(player: Player, blind: int) -> None:
    amount = min(blind, player.points)  # 短码玩家盲注即 all-in(投不满整盲)
    player.points -= amount
    player.bet_amount = amount
    if player.points == 0:
        player.status = PlayerStatus.ALLIN
