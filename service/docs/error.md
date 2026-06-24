# 错误处理(err 模块)

## 设计思路:错误是「返回值」,不是「异常」,也不是「事件」

core(`reduce`)内**不用异常做控制流**,也**不把错误当成一种 `Event`**。处理失败就在**另一条返回臂**交回一个 `Err` 值——和 Go 的 `value, err` 一致:

```python
def reduce(work, cmd) -> tuple[list[Event], Err | None]:
    # 成功 → (events, None)        events 里只有 Broadcast / Personal / Persist,绝无错误
    # 失败 → ([], Err(...))        GameLoop 丢弃工作副本,world 一字节未动
```

**成功与失败互斥**,理由:

- reduce 要可单测、可推理;异常做控制流会让「给定输入产出什么」依赖调用方的 try/except。
- 把成功(`events`)和失败(`Err`)放进类型上互斥的两条臂,让「既失败又广播」根本无法表达。
- 错误要**精确回发给发起人**——发起人由命令的 `origin` 标识,GameLoop 据此回发,不广播。
- 异常只留给**真正的程序 bug**,由 GameLoop 的工作副本丢弃兜底。

三类错误,各走各路:

| 类别 | 例子 | 在哪产生 | 怎么走 |
|---|---|---|---|
| **业务校验失败**(可预期) | 非你回合、积分不足、状态非法 | `reduce` 内 | `return [], Err(code, detail)`;GameLoop 丢弃工作副本 + 回发发起人 |
| **协议/解析错误** | 消息格式非法、字段缺失 | Receiver 层 | 没形成合法 `Command`,**不进 reduce**;直接构造 `ErrorMessage(INVALID_MESSAGE)` 投该连接 Sender 队列 |
| **文本/滥用防护**(可预期) | 房聊空文本 / 超长 / 刷屏 | Receiver 层 | 进 reduce 前拦(`INVALID_MESSAGE`/`MESSAGE_TOO_LONG`/`RATE_LIMITED`),回发该连接、不占 GameLoop(见 [messaging.md](messaging.md) / changes/0033)|
| **未预期异常**(bug) | 越界、空指针 | `reduce` 内意外抛出 | GameLoop 接住 → 丢弃工作副本 → 以 `Err(INTERNAL)` 回发 + 落日志 |

## 数据结构(三层)

```python
# ① ErrorCode —— 机器可读错误码,枚举,杜绝裸字符串漂移
class ErrorCode(str, Enum):
    NO_HAND = "NO_HAND"
    NOT_YOUR_TURN = "NOT_YOUR_TURN"
    INSUFFICIENT_POINTS = "INSUFFICIENT_POINTS"
    ALREADY_IN_ROOM = "ALREADY_IN_ROOM"   # 已在别的房间(单房间约束,见 lobby.md/user.md)
    NO_SUCH_ROOM = "NO_SUCH_ROOM"         # JoinRoom 目标房不存在
    ROOM_FULL = "ROOM_FULL"               # JoinRoom 满座
    CANT_CHANGE_NICK_IN_ROOM = "CANT_CHANGE_NICK_IN_ROOM"   # 在房间内不能改昵称(见 rest.md/lobby.md)
    INTERNAL = "INTERNAL"
    # …随业务补充;后续做成配置。**权威清单以 app/core/errors.py 的 ErrorCode 为准**,本块是示意
    # (实现已含 NOT_IN_ROOM/SEAT_TAKEN/NOT_YOUR_SEAT/INVALID_STATUS_TRANSITION/HAND_IN_PROGRESS/
    #  ILLEGAL_ACTION/NOT_ENOUGH_PLAYERS/NO_VOTE_IN_PROGRESS/NOT_A_VOTER/CANNOT_OPEN_VOTE/INVALID_BUY_IN/INVALID_MESSAGE/MESSAGE_TOO_LONG/RATE_LIMITED 等)

# ② Err —— 纯错误值:「出了什么错」,不含收件人。core↔GameLoop 间传递,不进队列
@dataclass(frozen=True)
class Err:
    code: ErrorCode
    detail: str = ""          # 给人看的补充(谁、哪个座位、什么状态),便于定位

# ③ ErrorMessage —— 发给客户端的 wire 报文(属 Message,不属 Event)
class ErrorMessage(ServerMessage):
    type: Literal["error"]
    code: ErrorCode
    detail: str = ""
```

- **`Err` 不含收件人**——业务失败永远回发发起人,收件人是命令的 `origin`,不必在错误里再存地址。
- **`code` 机器可读、`detail` 人可读**:前端按 `code` 决定 UI 与文案(多语言),`detail` 仅作补充。
- `ErrorCode` 具体取值后续做成配置(集中维护、挂文案),代码里**用枚举成员引用,绝不写裸字符串**。

## 怎么用(写在 reduce 里)

`reduce` 改的是 GameLoop 给的**工作副本**,失败整份丢弃(见 [architecture.md](architecture.md))。所以**失败安全由「丢弃副本」保证**,不再强制「先校验后改」:

```python
def reduce(work, cmd):
    match cmd:
        case PlayerAction():                  # 模型 2:命令不带 room
            room = work.rooms[work.users[cmd.origin].room]   # 目标房 = 发起人当前房
            if (e := validate_action(room, cmd)) is not None:
                return [], e                  # 工作副本被丢弃,world 原样
            apply_action(room, cmd)           # 改副本
            return advance(room), None
```

- **仍建议把校验前置**:读起来清晰、也省去无谓的修改。但即便「改了一半才发现非法」,`return [], Err(...)` 后工作副本被丢弃,`world` 照样没动——这正是工作副本模型相比旧「先校验后改」省心的地方。
- core 内的 helper 也用 **Go 风格**返回错误,绝不 `raise`:

```python
def validate_action(room, cmd) -> Err | None:        # 校验类:Err 或 None
    if room.hand is None:
        return Err(ErrorCode.NO_HAND)
    if acting_player(room.hand).nickname != cmd.origin:
        return Err(ErrorCode.NOT_YOUR_TURN, detail=f"acting={acting_player(room.hand).nickname}")
    return None

def take_from_pot(...) -> tuple[int, Err | None]: ... # 又出值又可能失败:(value, Err) 元组
```

> 异常只保留给真正的 bug——它应当暴露、应当让该命令整体失败。工作副本被丢弃即等于回滚,无需额外补偿。

## 怎么处理(shell 侧)

GameLoop 拿到 `(events, err)` 后**先分支**:

```python
events, err = reduce(work, cmd)
if err is not None:                       # 失败臂:丢弃 work,只回发发起人
    self.send_error(cmd, err)
else:
    commit(self.world, work)              # 成功才 commit(shell/world.py 模块函数)
    for ev in events:
        self.dispatch(ev)

def send_error(self, cmd, err: Err) -> None:
    if cmd.origin is None:                # 系统命令(Timeout/Cleanup/Disconnect…)无发起连接
        log.error("system cmd failed: %s err=%s", cmd, err.code)
        return
    if (conn := self.conns.get(cmd.origin)) is not None:   # origin = nick(模型 2),全局按 nick 取连接
        conn.outbound.put_nowait(ErrorMessage(code=err.code, detail=err.detail))
```

**发给谁?——命令的 `origin`(= 发起连接的 nick),不是命令里的业务昵称。** 模型 2 下连接全局绑 nick(见 [connection.md](connection.md)),Receiver 构造客户端命令时盖上 `origin = nick`;Timer / 断线产生的系统命令 `origin = None`。回发只需 `conns.get(nick)`,无需 room。

> **别拿业务昵称当回发目标。** `Timeout` 带的 `nickname` 是「轮到谁超时」(游戏语义),不是「谁在等回复」——它 `origin` 为空,失败只该落日志,绝不能把 `INTERNAL` 推给那个被超时的玩家。

- **业务失败**:回发发起连接 Sender 队列,只发他一个。
- **系统命令失败**(`origin` 空):无连接可回发,只 `log.error`。
- **Receiver 协议错误**:在 ws handler 里直接构造 `ErrorMessage` 投该连接 Sender 队列——绕过 reduce,但**不绕过 Sender**(守不变量 6),不在 Receiver 里直接 `ws.send`。
- **未预期异常**:GameLoop 丢弃工作副本,以 `Err(INTERNAL)` 回发 + `log.exception`,继续处理下一条命令——故障隔离在那一手。
- **Sender 自身发送失败**(连接已断):丢弃该连接队列并投 `Disconnect`,不影响他人。

## 后续待补

- `ErrorCode` → 配置化(集中目录 + 文案/多语言映射在前端),按 [config.md](config.md) 落地。
- 各命令的具体校验清单随业务补充到对应模块。
