"""图表审查 agent 测试。"""

from app.paper.figure_audit import _dedup_same_number, _parse_llm_captions, _GLUED


def test_dedup_same_number_drops_duplicate():
    # 同页同编号、caption 一个有空格一个没有 → 保留带空格者, 删无空格者
    items = [
        (1, 9, "table", "Figure 10: Comparison of our proposed WMA"),
        (2, 9, "table", "Figure 10:ComparisonofourproposedWMAandrelated"),
    ]
    drop = _dedup_same_number(items)
    assert drop == {2}


def test_dedup_keeps_different_kind_same_number():
    # 同页 figure 5 和 table 5 编号相同但类型不同 → 不删
    items = [
        (1, 7, "figure", "Figure 5: Visualization of reconstructed"),
        (2, 7, "table", "Table 5: Comparison of the proposed PGSA"),
    ]
    drop = _dedup_same_number(items)
    assert drop == set()


def test_dedup_keeps_different_page():
    # 不同页同编号 → 不删
    items = [
        (1, 2, "figure", "Fig. 1: Proposed framework"),
        (2, 5, "figure", "Fig. 1: Comparison of methods"),
    ]
    drop = _dedup_same_number(items)
    assert drop == set()


def test_glued_detection():
    assert _GLUED.search("Classifier-freeguidanceweightsover")
    assert not _GLUED.search("Classifier-free guidance weights over")


def test_parse_llm_captions():
    raw = '[{"index": 5, "caption": "Figure 1. Example figure"}]'
    data = _parse_llm_captions(raw)
    assert data is not None and data[0]["index"] == 5
    assert _parse_llm_captions("not json") is None