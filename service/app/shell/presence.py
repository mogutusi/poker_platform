# REST 够到 `world` 的**唯一合规窗口**(presence.md):回答「这个人在不在房里」。
# 它是 architecture.md 不变量 2 点名的三处只读豁免之一——只读、读的是已 commit 的态、全程同步。
# 绝不写 world、绝不做实时游戏裁定(那一律在 reduce);容忍滞后一拍,只用于展示与软守门。
#
# 曾经还有 is_online / room_headcount / online_nicks 三个投影,零消费者(0102 删):
# 前两个没人要那个口径(lobby 要的是 seated/watching 两个更细的量),online_nicks 是对
# ConnectionManager 的纯转发。删完它就不再需要 ConnectionManager —— 现在纯读 world。
# 要用时从 git history 取回,别凭空再造一遍。
#
# **名字比行为宽**:它现在只答「在哪个房」,不答「在不在线」。没改名是因为 architecture.md
# 不变量 2 的豁免名单、presence.md、多处交叉链接都点名 presence,改名要一起动、纯文字收益。

from app.core.domain import World


class Presence:
    def __init__(self, world: World) -> None:
        # world 对象稳定(commit 原地替换其 .users/.rooms,见 shell/world.py)⇒ 每次读得最新提交态、不持快照。
        # 不撕裂:唯一写者 GameLoop.handle 全程无 await(见 gameloop.py),任何协程都无法在 commit 两行赋值间切入,
        # 故只读消费者要么读到提交前、要么提交后的整份一致态(不变量 2)。
        self._world = world

    def current_room(self, nick: str) -> str | None:
        # 在哪个房:world.users[nick].room;纯大厅用户不在 world.users → None(见 lobby.md)。
        user = self._world.users.get(nick)
        return user.room if user is not None else None
