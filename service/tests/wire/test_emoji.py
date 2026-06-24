"""表情目录(messaging.md「表情」/ changes/0034-0035):封闭目录完整性 + code 形制 + codegen 吐目录。"""

import json
import re

from app.wire.emoji import EMOJI_CATALOG, EmojiCode, EmojiMeta
from scripts.gen_wire_ts import generate

_CODE_RE = re.compile(r"^[a-z0-9_]+$")  # 须对齐前端令牌正则 \[([a-z0-9_]+)\]


def test_catalog_covers_every_code_exactly():
    # 目录键 == EmojiCode 全集:无漏(每 code 有 meta)、无多(无孤儿 meta)。
    assert set(EMOJI_CATALOG) == set(EmojiCode)


def test_codes_match_token_charset():
    for code in EmojiCode:
        assert _CODE_RE.match(code.value), f"emoji code {code.value!r} 不匹配 [a-z0-9_]+(前端令牌无法解析)"


def test_meta_fields_non_empty():
    for meta in EMOJI_CATALOG.values():
        assert isinstance(meta, EmojiMeta) and meta.label.strip() and meta.glyph


def test_codegen_emits_emoji_catalog():
    # codegen 无条件吐表情目录:联合含每个 code、常量每项整行精确(glyph 绑定到其 code + JSON 形制,非裸子串)。
    ts = generate()
    assert "export interface EmojiMeta {" in ts
    assert "export const EMOJI_CATALOG: Record<EmojiCode, EmojiMeta>" in ts
    union_line = next(line for line in ts.splitlines() if line.startswith("export type EmojiCode ="))
    for code in EmojiCode:
        assert f'"{code.value}"' in union_line, f"{code.value} 不在 EmojiCode 联合"
    for code, meta in EMOJI_CATALOG.items():
        entry = (
            f"  {json.dumps(code.value)}: {{ label: {json.dumps(meta.label, ensure_ascii=False)}, "
            f"glyph: {json.dumps(meta.glyph, ensure_ascii=False)} }},"
        )
        assert entry in ts, f"目录项缺/不匹配: {entry}"
