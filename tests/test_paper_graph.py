"""论文精读 LangGraph 工作流测试。"""

import importlib
import json

import pytest

from app.paper.schemas import PaperChunkData, PaperMetadata, PaperSectionData


class Response:
    def __init__(self, content: str):
        self.content = content


class FakeReportLLM:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0
        self.bound_options = None

    def bind(self, **kwargs):
        self.bound_options = kwargs
        return self

    async def ainvoke(self, _prompt):
        self.calls += 1
        return Response(json.dumps(self.payload, ensure_ascii=False))


def _graph():
    try:
        return importlib.import_module("app.paper.graph").build_paper_graph()
    except ModuleNotFoundError:
        pytest.fail("论文精读工作流尚未实现")


def _state():
    chunks = [
        PaperChunkData(
            paper_id=1,
            chunk_id="paper-1-chunk-0",
            section="Methods",
            page_start=2,
            page_end=2,
            ordinal=0,
            content="We propose a hybrid retrieval method using dense and sparse signals.",
        ),
        PaperChunkData(
            paper_id=1,
            chunk_id="paper-1-chunk-1",
            section="Experiments",
            page_start=3,
            page_end=3,
            ordinal=1,
            content="Experiments show an accuracy improvement of 5 percent.",
        ),
    ]
    return {
        "paper_id": 1,
        "paper_title": "Hybrid Retrieval",
        "metadata": PaperMetadata(title="Hybrid Retrieval").model_dump(),
        "sections": [
            PaperSectionData(title="Methods", normalized_title="method", page_start=2, page_end=2).model_dump(),
            PaperSectionData(title="Experiments", normalized_title="experiments", page_start=3, page_end=3).model_dump(),
        ],
        "chunks": [chunk.model_dump() for chunk in chunks],
        "retry_count": 0,
    }


def _valid_payload():
    report = {
        "background": "研究背景：混合检索领域。",
        "motivation": "动机：现有方法无法融合稠密与稀疏信号。",
        "existing_problems": "现有方法存在两点不足：\n1. 窗口局限；\n2. 全局信息利用不足。",
        "solution": "提出混合检索方法（hybrid retrieval），统一融合两类信号。",
        "contributions": "贡献：\n1. 提出混合检索框架。",
        "terms": [{"en": "hybrid retrieval", "zh": "混合检索"}],
    }
    citations = [
        {
            "paper_id": 1,
            "paper_title": "Hybrid Retrieval",
            "page": 2,
            "section": "Methods",
            "chunk_id": "paper-1-chunk-0",
            "quote": "hybrid retrieval method using dense and sparse signals",
        }
    ]
    return {"report": report, "citations": citations}


@pytest.mark.asyncio
async def test_report_graph_returns_required_sections_and_verified_citations():
    llm = FakeReportLLM(_valid_payload())

    result = await _graph().ainvoke(_state(), {"configurable": {"llm": llm}})

    assert set(result["report"]) >= {
        "background",
        "motivation",
        "existing_problems",
        "solution",
        "contributions",
        "terms",
    }
    assert result["citations"][0]["verified"] is True
    assert result["citations"][0]["page"] == 2
    assert result["artifact"]["type"] == "report"
    assert result["completed_nodes"] == [
        "metadata_extract",
        "section_analyze",
        "contribution_extract",
        "report_synthesize",
        "citation_verify",
        "artifact_persist",
    ]


@pytest.mark.asyncio
async def test_report_graph_applies_a_bounded_output_budget():
    llm = FakeReportLLM(_valid_payload())

    await _graph().ainvoke(_state(), {"configurable": {"llm": llm}})

    assert llm.bound_options == {"max_tokens": 8192}


@pytest.mark.asyncio
async def test_invalid_quote_retries_once_then_flags_unverified():
    payload = _valid_payload()
    payload["citations"][0]["quote"] = "a completely fabricated quote"
    llm = FakeReportLLM(payload)

    result = await _graph().ainvoke(_state(), {"configurable": {"llm": llm}})

    assert llm.calls == 2
    assert result["retry_count"] == 1
    assert result["citations"][0]["verified"] is False
    assert result["citations"][0]["page"] is None
    assert "原文未提供充分证据" in result["report"]["solution"]


@pytest.mark.asyncio
async def test_invented_page_is_corrected_without_retry():
    payload = _valid_payload()
    payload["citations"][0]["page"] = 99
    llm = FakeReportLLM(payload)

    result = await _graph().ainvoke(_state(), {"configurable": {"llm": llm}})

    # 引用原文真实存在: 页码以证据块实际页码纠正, 不触发重试
    assert llm.calls == 1
    assert result["citations"][0]["verified"] is True
    assert result["citations"][0]["page"] == 2
    assert result["citations"][0]["reason"] == "page_corrected"


@pytest.mark.asyncio
async def test_report_graph_overrides_llm_foreign_paper_context():
    payload = _valid_payload()
    payload["citations"][0]["paper_id"] = 999
    payload["citations"][0]["paper_title"] = "Foreign Paper"
    llm = FakeReportLLM(payload)

    result = await _graph().ainvoke(_state(), {"configurable": {"llm": llm}})

    assert llm.calls == 1
    assert result["citations"][0]["paper_id"] == 1
    assert result["citations"][0]["paper_title"] == "Hybrid Retrieval"
    assert result["citations"][0]["verified"] is True
