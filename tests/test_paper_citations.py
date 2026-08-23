"""论文引用真实性校验测试。"""

import importlib

import pytest

from app.paper.schemas import PaperChunkData, PaperCitation


def _validator(chunks):
    try:
        cls = importlib.import_module("app.paper.citations").CitationValidator
    except ModuleNotFoundError:
        pytest.fail("app.paper.citations 尚未实现")
    return cls(chunks)


def _chunk(paper_id=1, page=6, content="The reported accuracy is 95.0% on the test set."):
    return PaperChunkData(
        paper_id=paper_id,
        chunk_id=f"paper-{paper_id}-chunk-1",
        section="Experiments",
        page_start=page,
        page_end=page,
        ordinal=1,
        content=content,
    )


def test_valid_citation_requires_matching_paper_page_chunk_and_quote():
    chunk = _chunk()
    citation = PaperCitation(
        paper_id=1,
        paper_title="Example",
        page=6,
        section="Experiments",
        chunk_id=chunk.chunk_id,
        quote="accuracy is 95.0%",
    )

    result = _validator([chunk]).validate(citation, paper_id=1)

    assert result.valid is True
    assert result.citation.verified is True
    assert result.citation.page == 6


def test_foreign_paper_citation_is_rejected():
    chunk = _chunk(paper_id=2)
    citation = PaperCitation(
        paper_id=2,
        page=6,
        chunk_id=chunk.chunk_id,
        quote="accuracy is 95.0%",
    )

    result = _validator([chunk]).validate(citation, paper_id=1)

    assert result.valid is False
    assert result.reason == "foreign_paper"
    assert result.citation.page is None


def test_invented_page_is_corrected_from_evidence_chunk():
    chunk = _chunk(page=2, content="Grounded evidence text.")
    citation = PaperCitation(
        paper_id=1,
        page=99,
        chunk_id=chunk.chunk_id,
        quote="Grounded evidence",
    )

    result = _validator([chunk]).validate(citation, paper_id=1)

    # 引用原文真实存在: 证据成立, 页码以证据块实际页码纠正(而非直接判无效)
    assert result.valid is True
    assert result.citation.verified is True
    assert result.citation.page == 2
    assert result.citation.reason == "page_corrected"


def test_missing_page_is_inferred_from_evidence_chunk():
    chunk = _chunk(page=4, content="Grounded evidence text.")
    citation = PaperCitation(
        paper_id=1,
        page=None,
        chunk_id=chunk.chunk_id,
        quote="Grounded evidence",
    )

    result = _validator([chunk]).validate(citation, paper_id=1)

    assert result.valid is True
    assert result.citation.verified is True
    assert result.citation.page == 4
    assert result.citation.reason == "page_inferred"


def test_missing_quote_is_rejected_without_fabricating_page():
    chunk = _chunk(page=3, content="The method uses two stages.")
    citation = PaperCitation(
        paper_id=1,
        page=3,
        chunk_id=chunk.chunk_id,
        quote="The method uses five stages.",
    )

    result = _validator([chunk]).validate(citation, paper_id=1)

    assert result.valid is False
    assert result.reason == "quote_not_found"
    assert result.citation.page is None
