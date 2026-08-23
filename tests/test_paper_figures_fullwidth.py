"""通栏表格检测回归测试。

两栏排版中, 标题在单栏内但表格跨两栏(通栏)时, 按标题所在栏钳制 x 会把表格
裁成一半(2026-08-21 用户报告: 论文表格只显示左半)。_table_full_width_extent
用跨栏证据(网格/规则线/双栏数据行)识别通栏表, 并排除"并排两张单栏表"的误判。
"""

from types import SimpleNamespace

from app.paper.figures import _table_full_width_extent


def _fake_page(width, height, lines, words, tables):
    return SimpleNamespace(
        width=width,
        height=height,
        lines=lines,
        find_tables=lambda: tables,
        extract_words=lambda: words,
    )


def test_full_width_by_spanning_rule():
    """规则线跨中线 → 通栏表。"""
    page = _fake_page(
        600, 800,
        lines=[{"x0": 100, "y0": 200, "x1": 500, "y1": 200}],
        words=[], tables=[],
    )
    assert _table_full_width_extent(page, 150, 600) == (100.0, 500.0)


def test_full_width_by_spanning_grid():
    """网格表跨两栏 → 通栏表。"""
    page = _fake_page(
        600, 800,
        lines=[],
        words=[],
        tables=[SimpleNamespace(bbox=(80, 200, 520, 400))],
    )
    assert _table_full_width_extent(page, 150, 600) == (80.0, 520.0)


def test_full_width_borderless_by_rows():
    """无边框通栏表: 双栏同带都有数据行。"""
    words = [
        {"text": "IMDN", "x0": 54, "x1": 100, "top": 210, "bottom": 218},
        {"text": "694K", "x0": 180, "x1": 220, "top": 210, "bottom": 218},
        {"text": "38.72", "x0": 400, "x1": 440, "top": 210, "bottom": 218},
        {"text": "0.9435", "x0": 450, "x1": 490, "top": 210, "bottom": 218},
        {"text": "IMDN", "x0": 54, "x1": 100, "top": 230, "bottom": 238},
        {"text": "703K", "x0": 180, "x1": 220, "top": 230, "bottom": 238},
        {"text": "35.64", "x0": 400, "x1": 440, "top": 230, "bottom": 238},
        {"text": "0.8910", "x0": 450, "x1": 490, "top": 230, "bottom": 238},
    ]
    page = _fake_page(595, 800, lines=[], words=words, tables=[])
    ext = _table_full_width_extent(page, 150, 500)
    assert ext is not None
    assert ext[0] < 100.0 and ext[1] > 450.0


def test_side_by_side_tables_not_full_width():
    """左右各一张单栏表(规则簇互不跨中线) → 不是通栏, 不能误判。"""
    lines = [
        {"x0": 60, "y0": 200, "x1": 295, "y1": 200},   # 左表规则
        {"x0": 319, "y0": 200, "x1": 553, "y1": 200},  # 右表规则
    ]
    words = [
        {"text": "CARN", "x0": 54, "x1": 100, "top": 210, "bottom": 218},
        {"text": "1592", "x0": 150, "x1": 180, "top": 210, "bottom": 218},
        {"text": "91", "x0": 190, "x1": 205, "top": 210, "bottom": 218},
        {"text": "baseline", "x0": 320, "x1": 380, "top": 210, "bottom": 218},
        {"text": "188", "x0": 400, "x1": 420, "top": 210, "bottom": 218},
        {"text": "10.113", "x0": 430, "x1": 470, "top": 210, "bottom": 218},
    ]
    page = _fake_page(612, 800, lines=lines, words=words, tables=[])
    assert _table_full_width_extent(page, 150, 500) is None


def test_single_column_table_not_full_width():
    """真·单栏表: 右栏无表格数据/规则 → 不判通栏。"""
    words = [
        {"text": "CARN", "x0": 54, "x1": 100, "top": 210, "bottom": 218},
        {"text": "1592", "x0": 150, "x1": 180, "top": 210, "bottom": 218},
        {"text": "91", "x0": 190, "x1": 205, "top": 210, "bottom": 218},
        {"text": "CARN", "x0": 54, "x1": 100, "top": 230, "bottom": 238},
        {"text": "1592", "x0": 150, "x1": 180, "top": 230, "bottom": 238},
        {"text": "91", "x0": 190, "x1": 205, "top": 230, "bottom": 238},
    ]
    page = _fake_page(612, 800, lines=[], words=words, tables=[])
    assert _table_full_width_extent(page, 150, 500) is None
