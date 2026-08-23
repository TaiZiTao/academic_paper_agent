"""MinerU 标题提取 + infer_sections 混合集成回归测试。

关键场景: TalkPhoto 论文正文嵌入的提示词模板行
("1. The output format is {\"name\": ...}")在 MinerU 通道是普通正文块
(无 text_level), 不得进入章节树——这正是不再需要 JSON 字符守卫的原因。
"""

from app.paper.parser import (
    PaperPage,
    infer_sections,
    mineru_content_to_sections,
)


def _content_v1():
    """v1 schema: type='text' + text_level 的标题块。"""
    return [
        {"type": "text", "text": "III. METHOD", "text_level": 1, "page_idx": 2, "bbox": [200, 100, 500, 130]},
        {"type": "text", "text": "A. The Framework of TalkPhoto", "text_level": 2, "page_idx": 2, "bbox": [100, 300, 500, 320]},
        # 提示词模板行: 普通正文, 无 text_level → 必须被排除
        {"type": "text", "text": '1. The output format is {"name": [Name of function 1,..., Name of function N]}', "page_idx": 2},
        {"type": "text", "text": "IV. EXPERIMENTS", "text_level": 1, "page_idx": 4, "bbox": [200, 100, 500, 130]},
    ]


def _content_v2():
    """v2 schema: type='title' + content.title_content/level。"""
    return [
        {"type": "title", "content": {"title_content": "B. Plug-and-play and efficient invocation", "level": 2}, "page_idx": 2, "bbox": [100, 400, 500, 420]},
    ]


def test_mineru_content_to_sections_v1():
    result = mineru_content_to_sections(_content_v1(), page_count=6)
    assert 3 in result and 5 in result  # page_idx 2→3, 4→5
    page3 = result[3]
    assert len(page3) == 2  # III. METHOD + A. The Framework (提示词行被排除)
    titles = [h.title for _k, h in page3]
    assert "III. METHOD" in titles
    assert "The output format" not in " ".join(titles)
    # 编号标题层级按编号方案(罗马=1), A. 字母=2
    levels = {h.title: h.level for _k, h in page3}
    assert levels["III. METHOD"] == 1
    assert levels["A. The Framework of TalkPhoto"] == 2


def test_mineru_content_to_sections_v2():
    result = mineru_content_to_sections(_content_v2(), page_count=6)
    assert 3 in result
    titles = [h.title for _k, h in result[3]]
    assert "B. Plug-and-play and efficient invocation" in titles


def test_infer_sections_with_mineru():
    pages = [
        PaperPage(page_number=1, text="Abstract\nSome abstract text."),
        PaperPage(page_number=2, text="Introduction text here."),
        PaperPage(page_number=3, text="III. METHOD\nBody text of method."),
        PaperPage(page_number=4, text="A. The Framework of TalkPhoto\nMore text."),
        PaperPage(page_number=5, text="IV. EXPERIMENTS\nExperiment text."),
    ]
    mineru = mineru_content_to_sections(
        [
            {"type": "text", "text": "III. METHOD", "text_level": 1, "page_idx": 2, "bbox": [200, 100, 500, 130]},
            {"type": "text", "text": "A. The Framework of TalkPhoto", "text_level": 2, "page_idx": 3, "bbox": [100, 300, 500, 320]},
            {"type": "text", "text": "IV. EXPERIMENTS", "text_level": 1, "page_idx": 4, "bbox": [200, 100, 500, 130]},
            {"type": "text", "text": '1. The output format is {"name": [Name of function]}', "page_idx": 2},
        ],
        page_count=5,
    )
    sections = infer_sections(pages, mineru_sections=mineru)
    titles = [s.title for s in sections]
    assert "III. METHOD" in titles
    assert "A. The Framework of TalkPhoto" in titles
    assert "IV. EXPERIMENTS" in titles
    assert not any("output format" in t for t in titles)
    method = next(s for s in sections if s.title == "III. METHOD")
    sub = next(s for s in sections if s.title == "A. The Framework of TalkPhoto")
    assert method.level == 1
    assert sub.level == 2
