"""文献检索 Agent Pydantic 模型测试。"""

import pytest
from pydantic import ValidationError

from app.research.schemas import ImportItem, ImportTaskOut, RankedResult, SearchResult


def test_search_result_defaults():
    r = SearchResult(
        source="arxiv", title="T", authors=["A"], venue="",
        abstract="", page_url="https://arxiv.org/abs/2401.1",
    )
    assert r.year is None
    assert r.doi is None
    assert r.pdf_url is None
    assert r.citations == 0
    assert r.published is False
    assert r.ccf_level is None


def test_import_item_requires_title_and_source():
    with pytest.raises(ValidationError):
        ImportItem(source="arxiv")  # 缺 title


def test_import_task_out_constructs():
    task = ImportTaskOut(
        id=1, title="T", source="arxiv", status="pending",
        progress=0, error_message="", paper_id=None, created_at="2026-08-21T00:00:00",
    )
    assert task.status == "pending"
    # status 当前为裸 str(计划规格), 任意字符串均可构造
    assert ImportTaskOut(id=2, title="T2", source="arxiv", status="anything", progress=0).status == "anything"


def test_ranked_result_index_non_negative():
    with pytest.raises(ValidationError):
        RankedResult(index=-1, score=5)


def test_search_result_openalex_source_and_oa_fields():
    """OpenAlex 源合法; oa_status 默认 unknown, openalex_id 默认 None。"""
    r = SearchResult(source="openalex", title="T", page_url="https://doi.org/10.1000/x")
    assert r.source == "openalex"
    assert r.oa_status == "unknown"
    assert r.openalex_id is None
    assert r.published is False  # venue 空 → 预印本


def test_search_result_oa_status_literal():
    r = SearchResult(
        source="openalex", title="T", page_url="u",
        venue="CVPR", published=True, ccf_level="A",
        oa_status="closed", openalex_id="W2741809807",
    )
    assert r.oa_status == "closed"
    assert r.openalex_id == "W2741809807"
    with pytest.raises(ValidationError):
        SearchResult(source="openalex", title="T", page_url="u", oa_status="paywalled")


def test_import_item_accepts_openalex_source():
    item = ImportItem(source="openalex", title="T", doi="10.1000/x", page_url="https://doi.org/10.1000/x")
    assert item.source == "openalex"


def test_import_item_year_field():
    """ImportItem.year: 默认 None, 接受 int(CVF L1.5 精准定位会议页用); 非 int 拒绝。"""
    assert ImportItem(source="arxiv", title="T").year is None
    assert ImportItem(source="arxiv", title="T", year=2023).year == 2023
    with pytest.raises(ValidationError):
        ImportItem(source="arxiv", title="T", year="abc")


def test_import_item_venue_field():
    """ImportItem.venue: 默认空串, 接受任意字符串(free_pdf 按 venue 路由免费源用)。"""
    assert ImportItem(source="arxiv", title="T").venue == ""
    assert ImportItem(source="arxiv", title="T", venue="CVPR 2023").venue == "CVPR 2023"
    assert ImportItem(source="openalex", title="T", venue="NeurIPS 2023").venue == "NeurIPS 2023"

