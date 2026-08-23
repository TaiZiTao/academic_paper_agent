"""分块章节归属测试: 页内偏移精确切分、跨页归属、滑窗边界、归属校验。"""

from app.paper.chunker import _page_segments, _section_for_page, audit_chunks, chunk_pages
from app.paper.schemas import PaperChunkData, PaperPage, PaperSectionData


def _page(number: int, text: str) -> PaperPage:
    return PaperPage(page_number=number, text=text)


def _sec(title, start, end, ordinal=0):
    return PaperSectionData(title=title, page_start=start, page_end=end, ordinal=ordinal)


def test_same_page_two_sections_cut_at_heading_offset():
    """同页两个章节标题: 前一章内容归前一章, 后一章从标题处起。"""
    page = _page(1, "Introduction intro body here. ProposedMethod method body here.")
    sections = [_sec("Introduction", 1, 1, 0), _sec("ProposedMethod", 1, 1, 1)]
    segs = _page_segments(page, sections)
    assert [(t, text) for t, _o, text in segs] == [
        ("Introduction", "Introduction intro body here. "),
        ("ProposedMethod", "ProposedMethod method body here."),
    ]


def test_cross_page_previous_section_keeps_its_tail():
    """前一章在结束页的尾部内容(下一章标题之前)仍归前一章。"""
    p1 = _page(1, "Introduction starts here.")
    p2 = _page(2, "Intro tail continues. ProposedMethod method starts.")
    sections = [_sec("Introduction", 1, 2, 0), _sec("ProposedMethod", 2, 2, 1)]
    segs = _page_segments(p2, sections)
    assert [(t, text) for t, _o, text in segs] == [
        ("Introduction", "Intro tail continues. "),
        ("ProposedMethod", "ProposedMethod method starts."),
    ]


def test_heading_at_page_start_owns_whole_page():
    """标题在页首: 整页从标题起归该章。"""
    page = _page(1, "ProposedMethod method body.")
    sections = [_sec("ProposedMethod", 1, 1)]
    segs = _page_segments(page, sections)
    assert [(t, text) for t, _o, text in segs] == [("ProposedMethod", "ProposedMethod method body.")]


def test_no_section_matches_falls_back_to_full_text():
    """无章节匹配的页回退为整页。"""
    page = _page(3, "orphan page content")
    sections = [_sec("Introduction", 1, 2)]
    segs = _page_segments(page, sections)
    assert [(t, text) for t, _o, text in segs] == [("全文", "orphan page content")]
    assert _section_for_page(3, sections) == "全文"


def test_chunk_window_monotonic_and_section_consistent():
    """滑窗: 偏移单调、章节一致、拼接可还原原文。"""
    page = _page(1, "Introduction " + "word " * 40 + "ProposedMethod " + "token " * 40)
    sections = [_sec("Introduction", 1, 1, 0), _sec("ProposedMethod", 1, 1, 1)]
    chunks = chunk_pages([page], paper_id=1, chunk_size=60, overlap=10, sections=sections)
    assert chunks
    intro = [c for c in chunks if c.section == "Introduction"]
    method = [c for c in chunks if c.section == "ProposedMethod"]
    assert intro and method
    # 单调性
    for group in (intro, method):
        for prev, cur in zip(group, group[1:]):
            assert prev.char_start < cur.char_start
            assert cur.char_start < cur.char_end
    # 章节内容不混: Introduction 的 chunk 不应含 ProposedMethod 标题
    assert all("ProposedMethod" not in c.content for c in intro)
    assert "ProposedMethod" in method[0].content


def test_audit_chunks_flags_wrong_section_assignment():
    """归属校验: 章节归属错位(前一章结尾被划给后一章)应被检出。"""
    page = _page(2, "Intro tail. ProposedMethod body.")
    sections = [_sec("Introduction", 1, 2, 0), _sec("ProposedMethod", 2, 2, 1)]
    # 正确归属
    good = [
        PaperChunkData(paper_id=1, chunk_id="c0", section="Introduction", page_start=2, page_end=2,
                       ordinal=0, char_start=0, char_end=11, content="Intro tail."),
        PaperChunkData(paper_id=1, chunk_id="c1", section="ProposedMethod", page_start=2, page_end=2,
                       ordinal=1, char_start=11, char_end=33, content="ProposedMethod body."),
    ]
    assert audit_chunks([page], good, sections) == []
    # 错误归属: c0 的 Introduction 内容被划给 ProposedMethod
    bad = [
        PaperChunkData(paper_id=1, chunk_id="c0", section="ProposedMethod", page_start=2, page_end=2,
                       ordinal=0, char_start=0, char_end=11, content="Intro tail."),
        PaperChunkData(paper_id=1, chunk_id="c1", section="ProposedMethod", page_start=2, page_end=2,
                       ordinal=1, char_start=11, char_end=33, content="ProposedMethod body."),
    ]
    issues = audit_chunks([page], bad, sections)
    assert issues and "ProposedMethod" in issues[0]


def test_audit_chunks_flags_orphan_section():
    """归属校验: chunk 章节不在该页段集合内应被检出。"""
    page = _page(1, "ProposedMethod body.")
    sections = [_sec("ProposedMethod", 1, 1)]
    chunks = [
        PaperChunkData(paper_id=1, chunk_id="c0", section="References", page_start=1, page_end=1,
                       ordinal=0, char_start=0, char_end=19, content="ProposedMethod body."),
    ]
    assert audit_chunks([page], chunks, sections)

class _FakePage:
    """模拟 pdfplumber 页面: 有 width 和 extract_words。"""
    width = 612.0

    def __init__(self, lines: list[str]):
        self.words: list[dict] = []
        for li, line in enumerate(lines):
            x = 50.0
            for tok in line.split():
                w = 10.0 * len(tok)
                self.words.append({
                    "text": tok,
                    "top": li * 12.0,
                    "bottom": li * 12.0 + 9.0,
                    "x0": x,
                    "x1": x + w,
                })
                x += w + 6.0

    def extract_words(self):
        return self.words


def test_region_has_table_rows_detects_body_text_crop():
    """内容自愈验证: 表格区域若裁到正文(无表格数据行), _region_has_table_rows 返回 False。"""
    from app.paper.figures import _region_has_table_rows
    page = _FakePage([
        "Method PSNR SSIM",
        "A 27.12 0.98",
        "B 26.50 0.97",
        "plain body paragraph text here continues",
        "more prose without any numbers at all",
    ])
    cap = {"x0": 50.0, "x1": 300.0}
    assert _region_has_table_rows(page, {"y0": 0.0, "y1": 40.0}, cap)
    assert not _region_has_table_rows(page, {"y0": 40.0, "y1": 130.0}, cap)

def test_spaced_title_located_in_compact_text():
    """排版层标题带空格, 页文本连写: 去空格后仍能定位切分点。"""
    page = _page(1, "Introduction intro body. ProposedMethod method body.")
    sections = [_sec("Introduction", 1, 1, 0), _sec("Proposed Method", 1, 1, 1)]
    segs = _page_segments(page, sections)
    assert [(t, text) for t, _o, text in segs] == [
        ("Introduction", "Introduction intro body. "),
        ("Proposed Method", "ProposedMethod method body."),
    ]


def test_spaced_title_singular_vs_plural_suffix():
    """标题与页文本有单复数/截断差异(去空格后是前缀)时也能定位。"""
    page = _page(1, "Intro. DehazingonReal-mask-syntheticHazyImages experiments.")
    sections = [_sec("Intro", 1, 1, 0), _sec("Dehazing on Real-mask-synthetic Hazy Image", 1, 1, 1)]
    segs = _page_segments(page, sections)
    assert [(t, text) for t, _o, text in segs] == [
        ("Intro", "Intro. "),
        ("Dehazing on Real-mask-synthetic Hazy Image", "DehazingonReal-mask-syntheticHazyImages experiments."),
    ]


def test_spaced_title_sections_chunk_correctly():
    """带空格标题的章节: chunk 归属按页内偏移精确切分, 前一章结尾不混入后一章。"""
    page = _page(2, "Intro tail contributions. ProposedMethod Framework of TIFF-CEM body.")
    sections = [_sec("Introduction", 1, 2, 0), _sec("Proposed Method", 2, 2, 1), _sec("Framework of TIFF-CEM", 2, 3, 2)]
    chunks = chunk_pages([page], paper_id=1, chunk_size=80, overlap=0, sections=sections)
    intro = [c for c in chunks if c.section == "Introduction"]
    method = [c for c in chunks if c.section == "Proposed Method"]
    framework = [c for c in chunks if c.section == "Framework of TIFF-CEM"]
    assert intro and method and framework
    assert all("contributions" in c.content for c in intro)
    assert "contributions" not in method[0].content
    assert "ProposedMethod" in method[0].content