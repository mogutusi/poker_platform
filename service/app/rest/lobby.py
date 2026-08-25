# REST 大厅房间列表(lobby.md「房间列表(REST 读)」/ rest.md)。GET /lobby/rooms → [RoomMeta]。
#
# 这是**唯一读 `world` 的 REST 端点**:房间花名册/头数是内存权威、从不落库(storage.md 房态不持久),DB 里没有;
# 其余 REST(leaderboard/hands/profile)读的是结算后落库的数据,才守 rest.md「只读 DB」。读的是 committed world:
# 展示用、可滞后一拍,不做实时游戏裁定(裁定一律在 reduce)。安全性同 Presence 的只读消费范式——持稳定 world 引用、
# 单线程 asyncio 下 GameLoop.handle 全程无 await ⇒ 任何不 await 的读对它原子、不撕裂(不变量 2,见 presence.py)。
# dev 明文无鉴权(P5 上 JWT,与 ws 两套,见 rest.md)。

from typing import Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.domain import Room, World
from app.core.enums import RoomStatus, UserStatus
from app.core.rules import blinds


class RoomMeta(BaseModel):
    # 大厅房间条目:静态配置 + 实时头数/状态(lobby.md)。REST DTO ≠ core Room:完整游戏态(deck/hand/各人筹码)绝不上大厅。
    id: str  # 房间键(world.rooms 的 key);v1 无独立 name 字段 → 键即人读名
    small_blind: int  # 小盲额
    big_blind: int  # 大盲额(= 2×小盲,派生非存储,同 RoomConfigChanged)
    buy_in: int  # 房间默认买入额
    max_seats: int  # 座位总数(= len(room.seats))
    seated: int  # 在座人数(占用座位数,含 OFFLINE 保座)
    watching: int  # 观战人数(users_in_room 中 WATCHING 状态数)
    status: RoomStatus  # PENDING_START / HAND_STARTED


def _room_meta(room_id: str, room: Room) -> RoomMeta:
    seated = sum(1 for s in room.seats if s is not None)
    watching = sum(1 for st in room.users_in_room.values() if st is UserStatus.WATCHING)
    return RoomMeta(
        id=room_id,
        small_blind=room.small_blind,
        big_blind=blinds.BIG_BLIND_MULTIPLE * room.small_blind,  # 倍数是规则常量,不在这里手抄
        buy_in=room.buy_in,
        max_seats=len(room.seats),
        seated=seated,
        watching=watching,
        status=room.status,
    )


def list_rooms(world: World) -> list[RoomMeta]:
    # committed world.rooms → [RoomMeta],按 room id 排序(稳定展示序)。纯同步、无 await ⇒ 对 GameLoop 原子读、不撕裂。
    return [_room_meta(rid, world.rooms[rid]) for rid in sorted(world.rooms)]


def make_lobby_router(get_world: Callable[[], World | None]) -> APIRouter:
    # 迟绑 world getter:world 在 DevShell.setup() 后才建(create_app 传 lambda: shell.world);测试可注入 fake。
    router = APIRouter()

    @router.get("/lobby/rooms", response_model=list[RoomMeta])
    async def get_lobby_rooms() -> list[RoomMeta]:
        # 无入参:公开发现、身份不需(dev 无鉴权)。仅读 world,不写、不 await(读原子)。
        world = get_world()
        if world is None:  # setup() 未完成的极窄窗口(serving 前已建 world,理论不可达)
            raise HTTPException(status_code=503, detail="shell not ready")
        return list_rooms(world)

    return router
