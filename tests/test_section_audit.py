"""章节树审查 agent 测试。"""

from app.paper.schemas import PaperSectionData
from app.paper.section_audit import _rule_flags, _parse_llm_sections, _normalize


def _sec(title, level=1, page=1):
    return PaperSectionData(
        title=title, normalized_title="", level=level, ordinal=0,
        page_start=page, page_end=page, summary="",
    )


def test_rule_flags_drops_sentence_as_heading():
    # 正文句被当标题(如 GFPose 论文的 "4. We can find ...")
    sections = [
        _sec("Abstract"),
        _sec("1.Introduction"),
        _sec("4. We can find that GFPose (P) consistently outperforms"),
        _sec("6.Conclusion"),
    ]
    drop, issues = _rule_flags(sections)
    assert 2 in drop  # 正文句被丢弃
    assert any("正文句" in s for _, s in issues)


def test_rule_flags_keeps_normal_headings():
    # 正常编号/无编号标题不被误伤
    sections = [
        _sec("Abstract"),
        _sec("I. INTRODUCTION"),
        _sec("II. RELATED WORKS"),
        _sec("A. Deep Networks for Image SR", level=2),
        _sec("III. METHOD"),
        _sec("V. CONCLUSION"),
        _sec("REFERENCES"),
    ]
    drop, _ = _rule_flags(sections)
    assert drop == []


def test_rule_flags_signals_number_jump():
    # 一级标题编号跳变(1 -> 4 -> 6)触发 LLM 复核信号
    sections = [
        _sec("1. Introduction"),
        _sec("4. Method"),
        _sec("6. Conclusion"),
    ]
    drop, issues = _rule_flags(sections)
    assert drop == []
    assert any("编号跳变" in s for _, s in issues)


def test_rule_flags_signals_broken_heading():
    # 标题拆行信号: "Less Meets" + "More!" 同页相邻短标题
    sections = [
        _sec("Progressive Backward Self-Intervention: Less Meets", level=2, page=4),
        _sec("More!", level=2, page=4),
    ]
    drop, issues = _rule_flags(sections)
    assert drop == []
    assert any("标题拆行" in s for _, s in issues)


def test_parse_llm_sections_fenced():
    # 解析带 ```json 围栏的 LLM 输出
    raw = '"""json\n[{"title": "1. Introduction", "level": 1, "page_start": 1, "page_end": 2}]\n"""'
    data = _parse_llm_sections(raw)
    assert data is not None
    assert data[0]["title"] == "1. Introduction"


def test_normalize_strips_number():
    assert _normalize("1. Introduction") == "introduction"
    assert _normalize("II. RELATED WORK") == "related_work"