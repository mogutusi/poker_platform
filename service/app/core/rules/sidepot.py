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
    dead_money = 0  # 弃牌的唯一最高投入者的未叫注:forfeit,归并到最高 live 子池(见下 / rules.md ③④)
    dead_owner: str | None = None  # 该未叫注的原投入者(仅无 live 子池的退化兜底用)

    # 第 1 步 · 未叫注:唯一最高投入者且 h1 > h2 → 摘出 (h1-h2),其 contributed 降到 h2。
    # 在局者退回本人;弃牌者(0014 离桌/清理 auto-fold 可折掉唯一最高投入者)其未叫注 forfeit,
    # 不退本人——作为死钱归并到最高 live 子池,由仍在局的被注者赢取(rules.md ④「已投池中筹码 forfeit」)。
    positive = {n: c for n, c in contributed.items() if c > 0}
    if positive:
        amounts = sorted(positive.values())
        highest = amounts[-1]
        second = amounts[-2] if len(amounts) >= 2 else 0
        top = [n for n, c in positive.items() if c == highest]
        if len(top) == 1 and highest > second:
            excess = highest - second
            contributed[top[0]] = second  # 摘掉未叫部分,余下按 h2 与他人匹配分层
            if top[0] in live:
                refunds[top[0]] = excess
            else:
                dead_money, dead_owner = excess, top[0]

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
            # 本档投入者全弃,且非「唯一最高未叫注」(那部分已在第 1 步摘出):这是各弃牌者互相匹配的
            # 边池、无 live 资格者能赢、在局者也够不着(投入不及此档)→ 按本档退回各 contributor,守恒。
            for n in contributors:
                refunds[n] = refunds.get(n, 0) + per
            continue
        pots.append(SidePot(amount=per * len(contributors), eligible=eligible))

    # 弃牌唯一最高者的未叫注:归并到最高 live 子池(在局者本就面对这笔注;死钱档恒在所有 live 投入之上,
    # 故 pots[-1] 即被注的最高 live 子池)。无任何 live 子池(理论不应发生:在局者必有投入)→ 退回原投入者守恒。
    if dead_money:
        if pots:
            top_pot = pots[-1]
            pots[-1] = SidePot(amount=top_pot.amount + dead_money, eligible=top_pot.eligible)
        elif dead_owner is not None:
            refunds[dead_owner] = refunds.get(dead_owner, 0) + dead_money

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
