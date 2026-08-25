# 用户资料 REST(rest.md §用户资料;P5 加密信封消费者,见 changes/0062 /user/me、0064 /user/password、
# 0065 /user/nickname、0097 /user/logout)。
# 信封拆包(open_request:查会话→REST 域密钥→MAC→解密→防重放窗)→ 身份 = 会话 name → 读/写 DB → 信封封回。
# 错误分层:信封不过=401(secure.py 统一);业务失败(旧密码错·在房=403 / 撞名=409 / 请求畸形=400);基础设施=500。
# 改昵称是独立工厂 make_nickname_router(需 Presence/conns,依赖面不同,免动 make_profile_router 既有签名)。

import logging
import time
from typing import Callable

from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import gameconfig
from app.auth.passwords import hash_password, verify_password
from app.auth.session import SessionStore
from app.db.models import User as DBUser
from app.db.queries import load_identity_by_name, load_password_for_change, load_profile_by_name, nickname_taken
from app.db.user_writes import update_nickname, update_password_hash
from app.rest.secure import SecureRequest, SecureResponse, open_request, seal_response
from app.shell.connection import Connection, ConnectionManager
from app.shell.presence import Presence

log = logging.getLogger(__name__)

# 昵称长度上限直接取自 schema(db/models.py 的 User.nickname `max_length`),不手抄字面量:
# 抄一份就是第二份事实源,改 schema 忘了改这里,就会「DB 收得下、接口先拒掉」或反过来(BUG-17)。
_NICKNAME_MAX_LEN: int = next(
    m.max_length for m in DBUser.model_fields["nickname"].metadata if hasattr(m, "max_length")
)


def make_profile_router(
    get_sessionmaker: Callable[[], async_sessionmaker[AsyncSession]],
    session_store: SessionStore,
    now: Callable[[], float] = time.time,
) -> APIRouter:
    # 迟绑 sessionmaker(同 make_login_router);session_store 为 shell 单例;now 可注入(测试确定过期)。
    router = APIRouter()

    @router.post("/user/me", response_model=SecureResponse)
    async def user_me(req: SecureRequest) -> SecureResponse:
        session, seq, _params = open_request(session_store, req, now())  # 信封不过 → 统一 401;/user/me 无参({})
        try:
            profile = await load_profile_by_name(get_sessionmaker(), session.name)
        except Exception:  # DB 错:已认证,非鉴权问题 → 如实 500(无敏感 body);真因落日志供运维
            log.exception("user_me: profile lookup failed")
            raise HTTPException(status_code=500, detail="internal")
        if profile is None:  # 会话在、DB 行没了 = 内部不一致(种子/迁移事故),同 500
            log.error("user_me: session name has no DB row")  # 不记 name 之外的敏感字段(本就无)
            raise HTTPException(status_code=500, detail="internal")
        name, nickname, points = profile
        return seal_response(session, seq, {"name": name, "nickname": nickname, "points": points})

    @router.post("/user/password", response_model=SecureResponse)
    async def change_password(req: SecureRequest) -> SecureResponse:
        # 改密码(信封内参 {old_password, new_password}):验旧密码(第二因子,防盗 token 锁死)→ 重算哈希 → 同步直写。
        session, seq, params = open_request(session_store, req, now())  # 信封不过 → 统一 401
        old_password = params.get("old_password")
        new_password = params.get("new_password")
        if not isinstance(old_password, str) or not isinstance(new_password, str):
            raise HTTPException(status_code=400, detail="bad request")  # 缺参 / 非字符串:请求畸形
        if not new_password.strip():
            raise HTTPException(status_code=400, detail="empty new password")  # 新密码非空底线(复杂度策略属未来)
        try:
            row = await load_password_for_change(get_sessionmaker(), session.name)
        except Exception:  # 基础设施错(已认证,非鉴权问题)→ 500;真因落日志(查询无密码明文)
            log.exception("change_password: lookup failed")
            raise HTTPException(status_code=500, detail="internal")
        if row is None:  # 会话在、DB 行没了 = 内部不一致
            log.error("change_password: session name has no DB row")
            raise HTTPException(status_code=500, detail="internal")
        uid, current_hash = row
        if current_hash is None or not verify_password(old_password, current_hash):
            # 旧密码不可验(未启用)或错:已认证但此操作不允许 → 403,不改库(密码/摘要不进日志)
            raise HTTPException(status_code=403, detail="password change denied")
        new_hash = hash_password(new_password, gameconfig.PWD_HASH_ROUNDS)  # 新盐重算(改成同密码也换哈希)
        try:
            await update_password_hash(get_sessionmaker(), uid, new_hash)  # 同步直写(鉴权列,delayDB 之外,见 db.md)
        except Exception:
            log.exception("change_password: write failed")
            raise HTTPException(status_code=500, detail="internal")
        # 改密即吊销该账号的其它会话(0097 翻掉 0064 的「v1 不吊销」):改密要求旧密码作第二因子,所以
        # 能走到这里的必是知道旧密码的本人;而「怀疑号被盗 → 改密码」是用户唯一的自救手段,旧会话还活着
        # 这个手段就等于没有。留下当前 sid,免得把正在操作的人自己踢下线。吊销会就地判死 Session 对象,
        # 别处那些连接在下一帧被关(4401,见 auth/session.py revoke)。
        revoked = session_store.revoke_all_for_name(session.name, except_id=req.sid)
        if revoked:
            log.info("password changed: revoked %d other session(s)", revoked)  # 不记 name/token(脱敏红线)
        return seal_response(session, seq, {"status": "ok"})

    @router.post("/user/logout", response_model=SecureResponse)
    async def logout(req: SecureRequest) -> SecureResponse:
        # 登出(信封内参 {}):吊销发起方自己这一个会话。信封验过 ⇒ req.sid 就是被认证的那个会话句柄。
        # 幂等:重复登出、或会话已过期,信封那一关先回 401,走到这里必然吊销成功。
        # 先封响应再吊销:seal_response 用的是会话密钥,而吊销会把这个 Session 判死——虽然 seal 本身不查
        # expires_at,但顺序写死才不依赖那个巧合,客户端也才一定收得到这次确认。
        session, seq, _params = open_request(session_store, req, now())  # 信封不过 → 统一 401
        response = seal_response(session, seq, {"status": "ok"})
        session_store.revoke(req.sid)
        return response

    return router


def _belongs_to(conn: Connection, account: str, dev_key: str) -> bool:
    # 这条连接是不是 `account` 这个账号的?加密连接看会话上的账号名;dev 明文连接无会话,退回 dev 端点的
    # 不变量:`Connection.create(nick=nick, session_id=nick, …)`,所以 session_id 就是它握手时报的那个 nick。
    if conn.session is not None:
        return conn.session.name == account
    return conn.session_id == dev_key


def make_nickname_router(
    get_sessionmaker: Callable[[], async_sessionmaker[AsyncSession]],
    session_store: SessionStore,
    get_presence: Callable[[], "Presence | None"],
    conns: ConnectionManager,
    now: Callable[[], float] = time.time,
) -> APIRouter:
    # 改昵称(rest.md/presence.md,changes/0065)。独立工厂:依赖 Presence(setup() 后才建 → 迟绑 getter)
    # 与 ConnectionManager,与 make_profile_router 依赖面不同,分开免动既有签名/测试。
    router = APIRouter()

    @router.post("/user/nickname", response_model=SecureResponse)
    async def change_nickname(req: SecureRequest) -> SecureResponse:
        # 仅大厅可改(nickname 是 world 的键,在用时改会键错乱)→ DB 直写 → 会话表 + 连接键联动(三处,顺序见 0065)。
        session, seq, params = open_request(session_store, req, now())  # 信封不过 → 统一 401
        new_nick = params.get("new_nickname")
        if (
            not isinstance(new_nick, str)
            or not new_nick
            or new_nick != new_nick.strip()  # 首尾空白拒:" Bob" 与 "Bob" 视觉同名、键不同 = 冒充面(0065 自 review)
            or len(new_nick) > _NICKNAME_MAX_LEN
        ):
            raise HTTPException(status_code=400, detail="bad nickname")  # 非串 / 空 / 带首尾空白 / 超长:请求畸形
        presence = get_presence()
        if presence is None:  # 未接线(启动序错):基础设施问题
            log.error("change_nickname: presence not wired")
            raise HTTPException(status_code=500, detail="internal")
        try:
            row = await load_identity_by_name(get_sessionmaker(), session.name)
        except Exception:
            log.exception("change_nickname: lookup failed")
            raise HTTPException(status_code=500, detail="internal")
        if row is None:  # 会话在、DB 行没了 = 内部不一致
            log.error("change_nickname: session name has no DB row")
            raise HTTPException(status_code=500, detail="internal")
        uid, old_nick = row  # 昵称以 DB 为准(会话表可能滞后)
        if new_nick == old_nick:
            raise HTTPException(status_code=400, detail="nickname unchanged")  # 同名无意义,拒(rename 语义干净)
        if presence.current_room(old_nick) is not None:
            # 在房不许改(world 键错乱);presence 读 committed world、可滞后一拍(presence.md 记档接受)
            raise HTTPException(status_code=403, detail="cannot change nickname in room")
        try:
            if await nickname_taken(get_sessionmaker(), new_nick):
                raise HTTPException(status_code=409, detail="nickname taken")  # 预查:干净拒
            # CAS:WHERE uid AND nickname=old_nick——同账号并发双改名只有一个赢(0 命中 = 输者 → 409,不做内存联动)
            if not await update_nickname(get_sessionmaker(), uid, old_nick, new_nick):
                raise HTTPException(status_code=409, detail="nickname changed concurrently")
        except IntegrityError:
            # 预查与写之间有 await 窗,并发撞名由唯一约束兜底 → 同 409(见 0065 决策 3)
            raise HTTPException(status_code=409, detail="nickname taken")
        except HTTPException:
            raise
        except Exception:
            log.exception("change_nickname: write failed")
            raise HTTPException(status_code=500, detail="internal")
        # 窗后复查「是否已进房」(0074·C):122 行那次检查读的是 committed world,而其后 126/129 两次 DB
        # await 期间 GameLoop 完全可能提交这个用户的 JoinRoom。此时 world 以 **old_nick** 为键,而 shell
        # 不写 world(不变量 2)⇒ 照旧做内存联动会让「world / DB / 会话表 / 连接键」四处永久发散:旧 nick
        # 成幽灵成员(广播查不到连接)、该用户一切命令按新 nick 解析不到房而 NOT_IN_ROOM(连 LeaveRoom 都发不出、
        # 无法自救)、Disconnect 同样落空使座位筹码永不回收、且再次 JoinRoom 会绕过单房间约束复制一份积分。
        # 故此处复查:已进房则把 DB 改回去(同款 CAS),四处回到一致的 old_nick,返 403(与窗前在房同码)。
        if presence.current_room(old_nick) is not None:
            try:
                reverted = await update_nickname(get_sessionmaker(), uid, new_nick, old_nick)
            except Exception:
                log.exception("change_nickname: revert failed after mid-rename join uid=%s", uid)
                reverted = False
            if not reverted:
                # 回滚没命中/抛错:DB 已是 new_nick 而 world 挂 old_nick,自动修复不了 → CRITICAL 留人工介入
                log.critical("change_nickname: joined room mid-rename, DB revert missed uid=%s", uid)
                raise HTTPException(status_code=500, detail="internal")
            raise HTTPException(status_code=403, detail="cannot change nickname in room")
        # DB 已落定(CAS 赢家)→ 内存联动(纯同步、其间无 await ⇒ 原子):该账号全部会话 + 本账号的活连接。
        session_store.rename_nickname(session.name, new_nick)
        # 连接**此刻**才查(0083 / BUG-4),不用 await 窗前捕获的引用:窗内本人若被 ws 顶替,那个引用已是死对象,
        # `rekey` 会走 else 分支只改死对象的 `.nick`,真正的活连接永久挂在 old_nick 键下——用户在线却收不到任何消息。
        # 从上面最后一次 await(CAS `update_nickname`)返回到这里全程同步 ⇒ 查表与 rekey 之间没有窗口。
        # 归属校验补上 0065 早捕获本要防的另一头:窗内别人可能改名占走 old_nick 键,那时表里挂的是**他人**的连接,
        # 不能把它重挂到我的新昵称上(那等于把别人的 socket 认领成我的——他之后发的每条命令都会按我的 uid 解析)。
        # 加密连接按会话账号名判定(一个账号可有多个会话,故比 name、不比对象身份)。
        # dev 明文连接没有会话(session=None),只能退一步按 dev 端点自己立的不变量判:它建连时 session_id 就盖成
        # 那个 ?nick=,所以「session_id == old_nick」才算本人;光看「无会话就算本人」是 TOCTOU——DB 行是**建连时**
        # 查的,到这一步 old_nick 名下早已没有行了。
        live_conn = conns.get(old_nick)
        if live_conn is not None and _belongs_to(live_conn, session.name, old_nick):
            conns.rekey(live_conn, new_nick)
        return seal_response(session, seq, {"status": "ok", "nickname": new_nick})

    return router
