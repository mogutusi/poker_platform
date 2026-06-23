# wire codegen 漂移守门(testing.md「CI 钩子:wire codegen 校验」/ wire.md 契约 #2):
# 改了 app/wire/*.py 却没重生成 frontend/src/types/wire.gen.ts → 产物与源不一致 → 本测试红。
# 无 node 依赖:生成器是纯 Python(见 scripts/gen_wire_ts.py / changes/0017)。

from scripts.gen_wire_ts import OUTPUT, _ts_type, generate
from app.wire.server import SeatView


def test_array_of_union_is_parenthesized():
    # 数组元素为并集时必须加括号:(X | null)[] 而非 X | null[](后缀 [] 优先级高于 |,会误解析)。
    # 防御本批未触发、但未来 tuple[X|None,...] 误用会静默生成语义错的 TS(字节守门测不出)。
    assert _ts_type(tuple[int | None, ...]) == "(number | null)[]"
    assert _ts_type(tuple[SeatView | None, ...]) == "(SeatView | null)[]"
    assert _ts_type(tuple[SeatView, ...]) == "SeatView[]"  # 非并集元素不加括号


def test_wire_ts_is_up_to_date():
    expected = generate()
    assert OUTPUT.exists(), f"缺生成产物 {OUTPUT};运行 python scripts/gen_wire_ts.py"
    actual = OUTPUT.read_text(encoding="utf-8")
    assert actual == expected, (
        "frontend/src/types/wire.gen.ts 与 app/wire/*.py 源不一致。"
        "重生成:cd service && python scripts/gen_wire_ts.py"
    )
