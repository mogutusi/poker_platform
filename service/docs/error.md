# 错误处理(err 模块)

## 设计思路:错误是「返回值」,不是「异常」,也不是「事件」

core(`reduce`)不用异常做控制流,也不把错误当 `Event`。失败走另一条返回臂,和 Go 的 `value, err` 一样。

```python
def reduce(work, cmd) -> tuple[list[Event], Err | None]:
    # 成功 → (events, None)    events 里只有 Broadcast / Personal / Persist
    # 失败 → ([], Err(...))    GameLoop 丢弃工作副本,world 一字节未动
```

这样做的收益:

- 成功与失败在类型上互斥,所以 reduce 可单测、可推理,「既失败又广播」这种状态无法表达。
- 业务失败只回发命令的 `origin`,也就是发起人,不广播。
- 异常只留给真正的程序 bug,由 GameLoop 丢弃工作副本兜底。

各类错误的走向:

| 类别 | 例子 | 在哪产生 | 怎么走 |
|---|---|---|---|
| 业务校验失败(可预期) | 非你回合、积分不足、状态非法 | `reduce` 内 | `return [], Err(code, detail)`;GameLoop 丢弃工作副本 + 回发发起人 |
| 协议/解析错误 | 消息格式非法、字段缺失 | Receiver 层 | 未形成合法 `Command`,不进 reduce;直接构造 `ErrorMessage(INVALID_MESSAGE)` 投该连接 Sender 队列 |
| 文本/滥用防护(可预期) | 房聊空文本 / 超长 / 刷屏 | Receiver 层 | 进 reduce 前拦下,回发该连接,不占 GameLoop;按情况返回 `INVALID_MESSAGE`、`MESSAGE_TOO_LONG` 或 `RATE_LIMITED`,细节见 [messaging.md](messaging.md) 与 changes/0033 |
| 未预期异常(bug) | 越界、空指针 | `reduce` 内意外抛出 | GameLoop 接住 → 丢弃工作副本 → 以 `Err(INTERNAL)` 回发 + 落日志 |

## 数据结构(三层)

从 core 内部到客户端,错误依次变换三次形态:`ErrorCode` → `Err` → `ErrorMessage`。

```python
# ① ErrorCode —— 机器可读错误码,枚举,不用裸字符串
class ErrorCode(str, Enum):
    NO_HAND = "NO_HAND"
    NOT_YOUR_TURN = "NOT_YOUR_TURN"
    INSUFFICIENT_POINTS = "INSUFFICIENT_POINTS"
    ALREADY_IN_ROOM = "ALREADY_IN_ROOM"   # 已在别的房间(单房间约束,见 lobby.md/user.md)
    NO_SUCH_ROOM = "NO_SUCH_ROOM"         # JoinRoom 目标房不存在
    ROOM_FULL = "ROOM_FULL"               # JoinRoom 满座
    INTERNAL = "INTERNAL"
    # 权威清单以 app/core/errors.py 为准,本块是示意。实现还有 NOT_IN_ROOM/SEAT_TAKEN/
    # NOT_YOUR_SEAT/INVALID_STATUS_TRANSITION/HAND_IN_PROGRESS/ILLEGAL_ACTION/
    # NOT_ENOUGH_PLAYERS/NO_VOTE_IN_PROGRESS/NOT_A_VOTER/CANNOT_OPEN_VOTE/
    # INVALID_BUY_IN/INVALID_MESSAGE/MESSAGE_TOO_LONG/RATE_LIMITED 等

# ② Err —— 纯错误值,不含收件人。core↔GameLoop 间传递,不进队列
@dataclass(frozen=True)
class Err:
    code: ErrorCode
    detail: str = ""          # 给人看的补充(谁、哪个座位、什么状态)

# ③ ErrorMessage —— 发给客户端的 wire 报文(属 Message,不属 Event)
class ErrorMessage(ServerMessage):
    type: Literal["error"]
    code: ErrorCode
    detail: str = ""
```

- `code` 是机器可读的,前端按它决定 UI 与多语言文案;`detail` 只作人读补充。
- `ErrorCode` 的具体取值后续会做成配置,集中维护并挂上文案。

## 怎么用(写在 reduce 里)

失败安全由「丢弃工作副本」保证,见 [architecture.md](architecture.md):即便改了一半才发现非法,`return [], Err(...)` 之后副本整份被丢弃,`world` 不动,等于回滚。所以不强制「先校验后改」,但仍然建议把校验写在前面,因为读起来清晰。

```python
def reduce(work, cmd):
    match cmd:
        case PlayerAction():                  # 模型 2:命令不带 room
            room = work.rooms[work.users[cmd.origin].room]   # 目标房 = 发起人当前房
            if (e := validate_action(room, cmd)) is not None:
                return [], e                  # 副本被丢弃,world 原样
            apply_action(room, cmd)           # 改副本
            return advance(room), None
```

core 内的 helper 也用 Go 风格返回错误,不 `raise`。异常只留给真正的 bug,让该命令整体失败。

```python
def validate_action(room, cmd) -> Err | None:        # 校验类:Err 或 None
    if room.hand is None:
        return Err(ErrorCode.NO_HAND)
    if acting_player(room.hand).nickname != cmd.origin:
        return Err(ErrorCode.NOT_YOUR_TURN, detail=f"acting={acting_player(room.hand).nickname}")
    return None

def take_from_pot(...) -> tuple[int, Err | None]: ... # 又出值又可能失败:(value, Err) 元组
```

## 怎么处理(shell 侧)

GameLoop 拿到 `(events, err)` 先分支;派发与回发都委托给它持有的 `Dispatcher`。`Dispatcher` 持有 `conns`、`persist`、`timer`,见 [connection.md](connection.md)「dispatch」。

```python
events, err = reduce(work, cmd)
if err is not None:                       # 失败臂:丢弃 work,只回发发起人
    self.dispatcher.send_error(cmd, err)
else:
    commit(self.world, work)              # 成功才 commit(shell/world.py 模块函数)
    for ev in events:
        self.dispatcher.dispatch(ev)

# —— send_error 在 Dispatcher 上(持 conns);GameLoop 委托 ——
def send_error(self, cmd, err: Err) -> None:
    if cmd.origin is None:                # 系统命令(Timeout/Cleanup/Disconnect…)无发起连接
        log.warning("system cmd %s failed: %s %s", type(cmd).__name__, err.code, err.detail)
        return
    if (conn := self.conns.get(cmd.origin)) is not None:   # origin = nick(模型 2),按 nick 取连接
        conn.outbound.put_nowait(ErrorMessage.from_err(err))
```

回发目标是命令的 `origin`,不是命令里的业务昵称。

- `origin` = 发起连接的 nick;模型 2 下连接是全局绑 nick 的,见 [connection.md](connection.md)。Receiver 构造客户端命令时盖上 `origin = nick`,Timer 与断线产生的系统命令 `origin = None`。
- 回发只需要 `conns.get(nick)`,不需要 room。
- 注意 `Timeout` 带的 `nickname` 是「轮到谁超时」,属于游戏语义,不是发起人。它的 `origin` 为空,所以失败只该落日志,不能把 `INTERNAL` 推给被超时的玩家。

各种失败的处理:

- 业务失败:只回发发起连接的 Sender 队列;系统命令失败(`origin` 空)没有连接可回发,只 `log.error`。
- Receiver 协议错误:在 ws handler 里直接构造 `ErrorMessage`,投该连接的 Sender 队列。这绕过了 reduce,但没有绕过 Sender,所以守住了不变量 6;不要在 Receiver 里直接 `ws.send`。
- 未预期异常:GameLoop 丢弃工作副本,以 `Err(INTERNAL)` 回发并 `log.exception`,然后继续处理下一条命令,故障隔离在那一条命令上。
- Sender 自身发送失败(连接已断):丢弃该连接队列并投 `Disconnect`,不影响他人。

## 后续待补

- `ErrorCode` → 配置化(集中目录 + 文案/多语言映射在前端),按 [config.md](config.md) 落地。
- 各命令的具体校验清单随业务补充到对应模块。
