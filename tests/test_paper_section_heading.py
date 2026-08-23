"""章节标题解析回归测试: 提示词模板行不得误判为章节标题。"""

from app.paper.parser import parse_section_heading


def test_prompt_template_line_not_heading():
    """TalkPhoto 论文正文嵌入的提示词编号行(含 JSON 结构)不是章节标题。"""
    line = '1. The output format is {"name": [Name of function 1,..., Name of function N], "answer": You first analyze the needs of users}'
    assert parse_section_heading(line) is None


def test_normal_headings_still_detected():
    """常规章节标题不受影响。"""
    assert parse_section_heading("III. METHOD") is not None
    assert parse_section_heading("A. The Framework of TalkPhoto") is not None
    assert parse_section_heading("1. Introduction") is not None
    assert parse_section_heading("5. Comparison with the state-of-the-art") is not None
