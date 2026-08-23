"""论文 PDF 解析与页码感知分块测试。"""

import importlib

import pytest


def _modules():
    try:
        schemas = importlib.import_module("app.paper.schemas")
        parser = importlib.import_module("app.paper.parser")
        chunker = importlib.import_module("app.paper.chunker")
    except ModuleNotFoundError as exc:
        pytest.fail(f"论文解析模块尚未实现: {exc.name}")
    return schemas, parser, chunker


def test_chunker_preserves_page_numbers_and_overlap():
    schemas, _, chunker = _modules()
    pages = [
        schemas.PaperPage(page_number=1, text="A" * 150),
        schemas.PaperPage(page_number=2, text="B" * 130),
    ]
    chunks = chunker.chunk_pages(pages, paper_id=7, chunk_size=100, overlap=20)

    assert chunks
    assert {chunk.page_start for chunk in chunks} == {1, 2}
    assert all(chunk.page_start == chunk.page_end for chunk in chunks)
    assert all(chunk.paper_id == 7 and len(chunk.content) <= 100 for chunk in chunks)
    page_one = [chunk for chunk in chunks if chunk.page_start == 1]
    assert page_one[0].content[-20:] == page_one[1].content[:20]


def test_infer_sections_recognizes_chinese_and_english_headings():
    schemas, parser, _ = _modules()
    pages = [
        schemas.PaperPage(page_number=1, text="Abstract\nThis paper studies retrieval."),
        schemas.PaperPage(page_number=2, text="1 Introduction\nBackground and motivation."),
        schemas.PaperPage(page_number=3, text="2 方法\n本文提出一种混合方法。"),
        schemas.PaperPage(page_number=4, text="3 Experiments\nResults and metrics."),
    ]

    sections = parser.infer_sections(pages)

    assert [section.normalized_title for section in sections] == [
        "abstract",
        "introduction",
        "method",
        "experiments",
    ]
    assert sections[1].page_start == 2
    assert sections[-1].page_end == 4


def test_infer_sections_builds_hierarchy_and_ignores_plain_table_label():
    schemas, parser, _ = _modules()
    pages = [
        schemas.PaperPage(page_number=1, text="Abstract\nSummary\nI. INTRODUCTION\nBody"),
        schemas.PaperPage(page_number=2, text="Methods\nII. RELATED WORK\nBody\nA. Image SR\nBody"),
        schemas.PaperPage(page_number=3, text="III. METHOD\nBody\n3.1 Cascade Prompt Block\nBody"),
        schemas.PaperPage(page_number=4, text="IV. EXPERIMENTS\nBody\nV. CONCLUSION\nBody"),
        schemas.PaperPage(page_number=5, text="REFERENCES\n[1] ..."),
    ]

    sections = parser.infer_sections(pages)

    assert [item.title for item in sections] == [
        "Abstract",
        "I. INTRODUCTION",
        "II. RELATED WORK",
        "A. Image SR",
        "III. METHOD",
        "3.1 Cascade Prompt Block",
        "IV. EXPERIMENTS",
        "V. CONCLUSION",
        "REFERENCES",
    ]
    assert [item.level for item in sections] == [1, 1, 1, 2, 1, 2, 1, 1, 1]
    assert sections[1].page_start == 1
    assert sections[-1].page_end == 5


def test_chunk_pages_assigns_same_page_text_to_the_correct_section():
    schemas, parser, chunker = _modules()
    pages = [
        schemas.PaperPage(
            page_number=1,
            text="I. INTRODUCTION\nintro body\nII. METHOD\nmethod body",
        )
    ]
    sections = parser.infer_sections(pages)

    chunks = chunker.chunk_pages(
        pages,
        paper_id=1,
        sections=sections,
        chunk_size=200,
        overlap=0,
    )

    assert [(chunk.section, chunk.content) for chunk in chunks] == [
        ("I. INTRODUCTION", "I. INTRODUCTION\nintro body"),
        ("II. METHOD", "II. METHOD\nmethod body"),
    ]


def test_section_continues_on_next_page_before_the_next_heading():
    schemas, parser, chunker = _modules()
    pages = [
        schemas.PaperPage(
            page_number=1,
            text="Abstract\nSummary\nI. INTRODUCTION\nfirst-page introduction",
        ),
        schemas.PaperPage(
            page_number=2,
            text=(
                "continued introduction on page two\n"
                "more introduction text\n"
                "II. RELATED WORK\nrelated-work body"
            ),
        ),
    ]

    sections = parser.infer_sections(pages)
    introduction = next(item for item in sections if item.normalized_title == "introduction")
    chunks = chunker.chunk_pages(
        pages,
        paper_id=1,
        sections=sections,
        chunk_size=500,
        overlap=0,
    )

    assert introduction.page_end == 2
    assert any(
        chunk.section == "I. INTRODUCTION"
        and chunk.page_start == 2
        and "continued introduction on page two" in chunk.content
        for chunk in chunks
    )


def test_infer_sections_handles_two_column_heading_lines_without_equation_false_positives():
    schemas, parser, _ = _modules()
    pages = [
        schemas.PaperPage(
            page_number=1,
            text=(
                "Abstract—Paper summary.\n"
                "I. INTRODUCTION unrelated text from the other column\n"
                "X = FFN(LN(X)) + X (12)\n"
                "20.13 / 0.7468 22.40 / 0.8393"
            ),
        ),
        schemas.PaperPage(
            page_number=2,
            text=(
                "incorporate priors through cross-attention, II. RELATEDWORKS\n"
                "A. Deep Networks for Image SR\nBody"
            ),
        ),
        schemas.PaperPage(
            page_number=3,
            text="V. CONCLUSION references begin in the other column\nBody REFERENCES",
        ),
        schemas.PaperPage(
            page_number=4,
            text='L. Van Gool, "Efficient modelling of image hierarchies."',
        ),
    ]

    sections = parser.infer_sections(pages)

    assert [item.title for item in sections] == [
        "Abstract",
        "I. INTRODUCTION",
        "II. RELATEDWORKS",
        "A. Deep Networks for Image SR",
        "V. CONCLUSION",
        "REFERENCES",
    ]
    assert sections[3].level == 2


def test_parse_section_heading_rejects_equation_and_sentence_fragments():
    _, parser, _ = _modules()

    assert parser.parse_section_heading("0 encoder LR") is None
    assert parser.parse_section_heading(
        "4 RGs are well-clustered into 4 categories, indicating that Pi"
    ) is None


def test_extract_page_text_reads_two_columns_in_column_order():
    _, parser, _ = _modules()

    class FakePage:
        width = 600

        def extract_words(self, **_kwargs):
            return [
                {"text": "Paper", "x0": 40, "x1": 90, "top": 10, "bottom": 20},
                {"text": "Title", "x0": 100, "x1": 145, "top": 10, "bottom": 20},
                {"text": "I.", "x0": 40, "x1": 52, "top": 50, "bottom": 60},
                {"text": "CONCLUSION", "x0": 60, "x1": 150, "top": 50, "bottom": 60},
                {"text": "left-one", "x0": 40, "x1": 105, "top": 70, "bottom": 80},
                {"text": "left-two", "x0": 40, "x1": 105, "top": 90, "bottom": 100},
                {"text": "continued", "x0": 330, "x1": 400, "top": 50, "bottom": 60},
                {"text": "REFERENCES", "x0": 330, "x1": 430, "top": 70, "bottom": 80},
                {"text": "[1]", "x0": 330, "x1": 350, "top": 90, "bottom": 100},
                {"text": "citation", "x0": 360, "x1": 420, "top": 90, "bottom": 100},
            ]

        def extract_text(self):
            return "Paper Title\nI. CONCLUSION continued\nleft-one REFERENCES\nleft-two [1] citation"

    text = parser.extract_page_text(FakePage())

    assert text.splitlines() == [
        "Paper Title",
        "I. CONCLUSION",
        "left-one",
        "left-two",
        "continued",
        "REFERENCES",
        "[1] citation",
    ]


def test_extract_page_text_detects_ieee_page_with_narrow_column_gutter():
    _, parser, _ = _modules()

    class FakePage:
        width = 600

        def extract_words(self, **_kwargs):
            return [
                {"text": "Paper", "x0": 40, "x1": 90, "top": 10, "bottom": 20},
                {"text": "Title", "x0": 100, "x1": 145, "top": 10, "bottom": 20},
                {"text": "Abstract", "x0": 40, "x1": 100, "top": 50, "bottom": 60},
                {"text": "summary-one", "x0": 110, "x1": 295, "top": 50, "bottom": 60},
                {"text": "intro-one", "x0": 304, "x1": 410, "top": 50, "bottom": 60},
                {"text": "summary-two", "x0": 40, "x1": 295, "top": 70, "bottom": 80},
                {"text": "intro-two", "x0": 304, "x1": 410, "top": 70, "bottom": 80},
                {"text": "I.", "x0": 40, "x1": 52, "top": 90, "bottom": 100},
                {"text": "INTRODUCTION", "x0": 60, "x1": 295, "top": 90, "bottom": 100},
                {"text": "intro-three", "x0": 304, "x1": 410, "top": 90, "bottom": 100},
            ]

        def extract_text(self):
            return (
                "Paper Title\n"
                "Abstract summary-one intro-one\n"
                "summary-two intro-two\n"
                "I. INTRODUCTION intro-three"
            )

    text = parser.extract_page_text(FakePage())

    assert text.splitlines() == [
        "Paper Title",
        "Abstract summary-one",
        "summary-two",
        "I. INTRODUCTION",
        "intro-one",
        "intro-two",
        "intro-three",
    ]


def test_parse_pdf_uses_pdf_metadata_and_page_text(monkeypatch, tmp_path):
    _, parser, _ = _modules()

    class FakePage:
        def __init__(self, text):
            self._text = text

        def extract_text(self):
            return self._text

    class FakePdf:
        metadata = {"Title": "Grounded Paper", "Author": "Alice; Bob"}
        pages = [
            FakePage("Abstract\n" + "English research content. " * 8),
            FakePage("1 Methods\n" + "We propose a robust method. " * 8),
        ]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(parser.pdfplumber, "open", lambda _path: FakePdf())

    result = parser.parse_pdf(tmp_path / "paper.pdf")

    assert result.metadata.title == "Grounded Paper"
    assert result.metadata.authors == ["Alice", "Bob"]
    assert result.page_count == 2
    assert result.pages[1].page_number == 2
    assert result.language == "en"


def test_parse_pdf_rejects_scan_without_extractable_text(monkeypatch, tmp_path):
    _, parser, _ = _modules()

    class EmptyPage:
        def extract_text(self):
            return ""

    class EmptyPdf:
        metadata = {}
        pages = [EmptyPage(), EmptyPage()]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(parser.pdfplumber, "open", lambda _path: EmptyPdf())

    with pytest.raises(parser.UnsupportedScanError) as exc:
        parser.parse_pdf(tmp_path / "scan.pdf")
    assert exc.value.code == "unsupported_scan"
