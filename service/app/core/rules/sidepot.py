# rules.md ③ 边池分配(退还未叫注 / 分层削池 / 判池 + 奇数零头)
# 牌力以 strength(nick→treys 分,越小越强)快照传入,本模块不碰 treys。

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SidePot:
    amount: int  # 本子池筹码总额
    eligible: list[str]  # 有资格赢取者(投入达该档且未弃牌)的 nick


@dataclass(frozen=True, slots=True)
class Payout:
    refunds: dict[str, int]  # nick → 退还的未叫注(及退化情形的无主池退回)
    pots: list[SidePot]  # 分层后的子池(供事件/记录)
    winnings: dict[str, int]  # nick → 赢得的子池筹码
    total: dict[str, int]  # nick → refunds + winnings(结算时还回 points 的总额)


def settle(
    contributed: dict[str, int],
    live: set[str],
    strength: dict[str, int],
    seat_of: dict[str, int],
    button_position: int,
    seat_size: int,
) -> Payout:
    contributed = dict(contributed)  # 拷贝,不改入参
    refunds: dict[str, int] = {}

    # 第 1 步 · 退还未叫注:唯一最高投入者且 h1 > h2 → 退 (h1-h2),其 contributed 降到 h2
    positive = {n: c for n, c in contributed.items() if c > 0}
    if positive:
        amounts = sorted(positive.values())
        highest = amounts[-1]
        second = amounts[-2] if len(amounts) >= 2 else 0
        top = [n for n, c in positive.items() if c == highest]
        if len(top) == 1 and highest > second:
            refunds[top[0]] = highest - second
            contributed[top[0]] = second

    # 第 2 步 · 分层削池:每档差额 × 仍在该档的人数(含弃牌者)
    pots: list[SidePot] = []
    levels = sorted({c for c in contributed.values() if c > 0})
    prev = 0
    for level in levels:
        per = level - prev
        prev = level  # 无论本档是否成池,层差都要前进,否则下一档 per 会算错
        contributors = [n for n, c in contributed.items() if c >= level]
        eligible = [n for n in contributors if n in live]
        if not eligible:
            # 理论不应发生(每档至少一个未弃牌者撑起);退化时按本档投入退回各 contributor,
            # 守住筹码守恒(reduce 侧可据 refund 给弃牌者识别此异常)
            for n in contributors:
                refunds[n] = refunds.get(n, 0) + per
            continue
        pots.append(SidePot(amount=per * len(contributors), eligible=eligible))

    # 第 3 步 · 判池归属 + 奇数零头
    winnings: dict[str, int] = {}
    for pot in pots:
        best = min(strength[n] for n in pot.eligible)
        winners = [n for n in pot.eligible if strength[n] == best]
        share = pot.amount // len(winners)
        for n in winners:
            winnings[n] = winnings.get(n, 0) + share
        remainder = pot.amount - share * len(winners)
        if remainder:
            # 零头给最接近庄家左手者:(seat - button) % seat_size 最小(公式权威)
            closest = min(winners, key=lambda n: (seat_of[n] - button_position) % seat_size)
            winnings[closest] = winnings.get(closest, 0) + remainder

    total: dict[str, int] = {}
    for source in (refunds, winnings):
        for n, v in source.items():
            total[n] = total.get(n, 0) + v
    return Payout(refunds=refunds, pots=pots, winnings=winnings, total=total)
