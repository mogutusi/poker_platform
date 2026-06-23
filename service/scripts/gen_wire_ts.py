#!/usr/bin/env python3
# wire 协议 codegen:从后端 Pydantic 单一事实源(app/wire/)生成前端 TS 类型(只读产物)。
#
# 为何自包含、不使 pydantic2ts:pydantic2ts shell out 到 node 的 json-schema-to-typescript,
# 本机无 node;且 JSON-schema 中间层产物含 $ref/$defs 噪声。本生成器内省 wire 模型的 model_fields,
# 用一张 Python→TS 类型映射确定性地直接吐扁平可辨识联合(见 changes/0017;治理见 docs/wire.md)。
#
# 用法:
#   python scripts/gen_wire_ts.py          # 生成/覆盖 frontend/src/types/wire.gen.ts
#   python scripts/gen_wire_ts.py --check   # 不写盘;产物与源不一致则退出码 1(CI/pre-commit 守门)
#
# 漂移守门同时由 tests/wire/test_codegen_uptodate.py 兜住(改 .py 不重生成 → pytest 红)。

from __future__ import annotations

import dataclasses
import enum
import sys
import types as _types
import typing
from pathlib import Path
from typing import Literal, Union, get_args, get_origin

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # service/ 根,使 `app` 可导入(脱离 pytest 也能跑)

from pydantic import BaseModel

from app.core.cards import Card, CardRank, CardSuit
from app.core.enums import HandStatus, PlayerActionType, PlayerStatus, UserStatus
from app.core.errors import ErrorCode
from app.wire.client import CLIENT_MESSAGES
from app.wire.server import NickAmount, PlayerView, ServerMessage, ShowdownReveal, SERVER_MESSAGES

# 产物路径(相对本脚本:service/scripts/ → ../../frontend/src/types/wire.gen.ts)
OUTPUT = Path(__file__).resolve().parents[2] / "frontend" / "src" / "types" / "wire.gen.ts"

# 命名类型的规范输出顺序(TS 类型声明可前向引用,顺序仅为可读性;固定顺序保产物确定)。
# 新增 wire 用到的 enum / 值对象必须登记在此,否则生成时断言失败(防默默漏类型)。
_ENUM_ORDER: list[type[enum.Enum]] = [
    CardSuit,
    CardRank,
    PlayerStatus,
    HandStatus,
    PlayerActionType,
    UserStatus,
    ErrorCode,
]
_VALUE_OBJECT_ORDER: list[type] = [Card, PlayerView, ShowdownReveal, NickAmount]

_HEADER = """\
// ⚠️ GENERATED — DO NOT EDIT BY HAND.
// Source of truth: service/app/wire/{server,client}.py (Pydantic).
// Regenerate: cd service && python scripts/gen_wire_ts.py
// Drift is guarded by tests/wire/test_codegen_uptodate.py (pytest goes red on stale output).
"""


def _is_enum(t: object) -> bool:
    return isinstance(t, type) and issubclass(t, enum.Enum)


def _is_model(t: object) -> bool:
    return isinstance(t, type) and issubclass(t, BaseModel)


def _is_named(t: object) -> bool:
    # 需要单独声明 TS 类型的「命名类型」:enum / Pydantic 模型 / dataclass(Card)。
    return _is_enum(t) or _is_model(t) or dataclasses.is_dataclass(t)


def _ts_type(ann: object) -> str:
    # Python 注解 → TS 类型串。命名类型按其类名引用(声明在别处)。
    origin = get_origin(ann)
    if origin is Literal:
        return " | ".join(f'"{v}"' for v in get_args(ann))
    if origin is Union or origin is _types.UnionType:
        args = get_args(ann)
        non_none = [a for a in args if a is not type(None)]
        rendered = " | ".join(_ts_type(a) for a in non_none)
        return rendered + (" | null" if len(non_none) != len(args) else "")
    if origin is tuple:
        args = get_args(ann)
        if len(args) == 2 and args[1] is Ellipsis:  # tuple[T, ...] → T[]
            return _ts_type(args[0]) + "[]"
        return "[" + ", ".join(_ts_type(a) for a in args) + "]"  # tuple[A, B] → [A, B]
    if ann is int:
        return "number"
    if ann is str:
        return "string"
    if ann is bool:
        return "boolean"
    if _is_named(ann):
        return ann.__name__  # type: ignore[union-attr]
    raise TypeError(f"gen_wire_ts: unmapped annotation {ann!r}")


def _fields(model: type) -> list[tuple[str, object, bool]]:
    # (name, annotation, required) — Pydantic 模型读 model_fields;dataclass(Card)读 dataclasses.fields。
    if _is_model(model):
        return [(n, fi.annotation, fi.is_required()) for n, fi in model.model_fields.items()]
    hints = typing.get_type_hints(model)
    return [(f.name, hints[f.name], True) for f in dataclasses.fields(model)]  # Card 字段皆必填


def _named_in(ann: object, out: list[type]) -> None:
    # 收集注解里引用到的命名类型(递归进 Literal/Union/tuple 的参数)。
    if _is_named(ann):
        if ann not in out:
            out.append(ann)  # type: ignore[arg-type]
        return
    for a in get_args(ann):
        _named_in(a, out)


def _discover(roots: list[type]) -> list[type]:
    # 从消息集 BFS 出全部可达命名类型(含 Card→CardRank/CardSuit、PlayerView→PlayerStatus…)。
    referenced: list[type] = []
    work = list(roots)
    while work:
        model = work.pop(0)
        for _name, ann, _req in _fields(model):
            found: list[type] = []
            _named_in(ann, found)
            for t in found:
                if t not in referenced:
                    referenced.append(t)
                    if _is_model(t) or dataclasses.is_dataclass(t):
                        work.append(t)  # 递归进值对象的字段
    return referenced


def _emit_enum(e: type[enum.Enum]) -> str:
    members = " | ".join(f'"{m.value}"' for m in e)
    return f"export type {e.__name__} = {members};"


def _emit_interface(model: type) -> str:
    lines = [f"export interface {model.__name__} {{"]
    for name, ann, required in _fields(model):
        # `type` 判别字段恒必填:它是可辨识联合的判别量(虽 Python 侧有默认值便于构造),
        # 出站必带、入站 Pydantic 也据它判别——TS 上必填才能正确收窄联合(wire.md 形状 #1)。
        opt = "" if (required or name == "type") else "?"  # 其余非必填(有默认)→ 发送方可省略 → TS 可选
        lines.append(f"  {name}{opt}: {_ts_type(ann)};")
    lines.append("}")
    return "\n".join(lines)


def _emit_union(name: str, classes: tuple[type, ...]) -> str:
    body = "\n".join(f"  | {c.__name__}" for c in classes)
    return f"export type {name} =\n{body};"


def generate() -> str:
    referenced = _discover([*SERVER_MESSAGES, *CLIENT_MESSAGES])
    ref_set = set(referenced)
    # 断言:所有被引用的命名类型都在规范顺序表里(新增类型未登记 → 这里失败,不会默默漏)。
    enums = [e for e in _ENUM_ORDER if e in ref_set]
    value_objects = [v for v in _VALUE_OBJECT_ORDER if v in ref_set]
    known = set(_ENUM_ORDER) | set(_VALUE_OBJECT_ORDER) | set(SERVER_MESSAGES) | set(CLIENT_MESSAGES)
    missing = [t.__name__ for t in referenced if t not in known]
    if missing:
        raise AssertionError(f"gen_wire_ts: referenced types not registered for output: {missing}")

    blocks: list[str] = [_HEADER.rstrip()]
    blocks.append("// ── enums ──")
    blocks += [_emit_enum(e) for e in enums]
    blocks.append("// ── value objects ──")
    blocks += [_emit_interface(v) for v in value_objects]
    blocks.append("// ── server → client messages ──")
    blocks += [_emit_interface(m) for m in SERVER_MESSAGES]
    blocks.append("// ── client → server messages ──")
    blocks += [_emit_interface(m) for m in CLIENT_MESSAGES]
    blocks.append("// ── discriminated unions ──")
    blocks.append(_emit_union("ServerMessage", SERVER_MESSAGES))
    blocks.append(_emit_union("ClientMessage", CLIENT_MESSAGES))
    return "\n\n".join(blocks) + "\n"


def main(argv: list[str]) -> int:
    content = generate()
    if "--check" in argv:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else None
        if current != content:
            print(f"wire codegen STALE: {OUTPUT} differs from source. Run: python scripts/gen_wire_ts.py")
            return 1
        print("wire codegen OK (up to date)")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(content, encoding="utf-8")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
