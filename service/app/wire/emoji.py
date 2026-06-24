# 聊天表情目录(见 messaging.md「表情」/ changes/0034):后端单一事实源,codegen 吐 TS 供前端渲染。
# 后端纯透传——本目录不进任何 wire 报文、reduce 不感知;只作「前后端共享的封闭表情集」+ 默认字形。

from dataclasses import dataclass
from enum import StrEnum


# 封闭表情集的稳定 code(= 聊天文本里 [code] 令牌 + 前端渲染键)。取值即 code、自文档化;
# 人类显示名见下 EMOJI_CATALOG 的 label。code 限 [a-z0-9_]+ 以对齐前端令牌正则 \[([a-z0-9_]+)\]。
class EmojiCode(StrEnum):
    SMILE = "smile"
    LAUGH = "laugh"
    CRY = "cry"
    COOL = "cool"
    THINKING = "thinking"
    POKER_FACE = "poker_face"
    THUMBS_UP = "thumbs_up"
    CLAP = "clap"
    FIRE = "fire"
    GG = "gg"
    FOLD = "fold"
    ALL_IN = "all_in"


@dataclass(frozen=True, slots=True)
class EmojiMeta:
    label: str  # 中文显示名(前端可作 alt/title)
    glyph: str  # 默认 Unicode 字形;前端可按 code 覆盖为自定义贴纸图(故同一目录兼容表情与贴纸)


# code → 元数据。**必须覆盖 EmojiCode 全集**(test_emoji 守门);起始集偏扑克场景,后续加性扩展。
EMOJI_CATALOG: dict[EmojiCode, EmojiMeta] = {
    EmojiCode.SMILE: EmojiMeta("微笑", "😊"),
    EmojiCode.LAUGH: EmojiMeta("大笑", "😂"),
    EmojiCode.CRY: EmojiMeta("哭", "😭"),
    EmojiCode.COOL: EmojiMeta("酷", "😎"),
    EmojiCode.THINKING: EmojiMeta("思考", "🤔"),
    EmojiCode.POKER_FACE: EmojiMeta("扑克脸", "😐"),
    EmojiCode.THUMBS_UP: EmojiMeta("赞", "👍"),
    EmojiCode.CLAP: EmojiMeta("鼓掌", "👏"),
    EmojiCode.FIRE: EmojiMeta("火", "🔥"),
    EmojiCode.GG: EmojiMeta("打得好", "🎉"),
    EmojiCode.FOLD: EmojiMeta("弃牌", "🏳️"),
    EmojiCode.ALL_IN: EmojiMeta("全下", "🟢"),
}
