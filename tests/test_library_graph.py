from app.paper.library_nodes import should_retry


def test_should_retry_low_score():
    state = {"relevance_scores": [{"score": 1}, {"score": 1}], "retry_count": 0}
    assert should_retry(state) == "retry"


def test_should_retry_capped():
    state = {"relevance_scores": [{"score": 1}], "retry_count": 3}
    assert should_retry(state) == "next"


def test_should_retry_good_score():
    state = {"relevance_scores": [{"score": 5}, {"score": 4}], "retry_count": 0}
    assert should_retry(state) == "next"


def test_should_retry_no_scores():
    state = {"relevance_scores": [], "retry_count": 0}
    assert should_retry(state) == "next"


from app.paper.library_graph import build_library_graph


def test_graph_builds():
    g = build_library_graph()
    assert g is not None


def test_graph_has_all_nodes():
    g = build_library_graph()
    nodes = set(g.get_graph().nodes.keys())
    for n in ("intent_router", "chat_node", "catalog_node", "direction_select", "retrieve", "relevance_evaluate", "rewrite_query", "generate", "cite_verify"):
        assert n in nodes, f"missing node {n}"


import json
import types

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.base import Base
from app.models.paper import Paper


class _FakeLLM:
    """按调用顺序吐出预设响应; calls 记录调用次数供断言。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def ainvoke(self, prompt):
        self.calls.append(prompt)
        return types.SimpleNamespace(content=self._responses.pop(0))


class _FakeRetriever:
    def __init__(self, chunk):
        self._chunk = chunk

    async def search(self, pid, query, k=8, section=None):
        return [types.SimpleNamespace(chunk=self._chunk, score=0.9)]


@pytest.mark.asyncio
async def test_graph_end_to_end_retry_loop(tmp_path):
    """整图跑通: 两次低分触发重试环(rewrite_query -> retrieve), 第三次达标进入生成, 引用通过 cite_verify。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/g.db")
    sf = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with sf() as session:
        session.add(Paper(original_filename="a.pdf", stored_filename="a.pdf", title="SR", research_field="超分辨率", status="ready"))
        await session.commit()

    chunk = types.SimpleNamespace(
        paper_id=1, chunk_id="c1", section="方法", page_start=1, page_end=1,
        ordinal=0, char_start=0, char_end=0,
        content="超分辨率重建方法使用卷积神经网络实现图像增强", metadata={},
    )
    llm = _FakeLLM([
        json.dumps({"field": "超分辨率", "year_min": None, "year_max": None, "authors": [], "keywords": [], "language": ""}),
        "1",                                      # 第1次评分: 低分 -> 重试
        "超分辨率重建技术对比分析",                # 第1次改写
        "1",                                      # 第2次评分: 低分 -> 重试
        "图像超分辨率重建网络",                    # 第2次改写
        "5",                                      # 第3次评分: 达标 -> 生成
        json.dumps({
            "content": "超分辨率重建方法主要包括基于CNN的深度学习方法。",
            "citations": [{"paper_id": 1, "paper_title": "SR", "page": 1, "section": "方法", "chunk_id": "c1", "quote": "超分辨率重建方法使用卷积神经网络"}],
        }),
    ])
    config = {"configurable": {"llm": llm, "session_factory": sf, "retriever": _FakeRetriever(chunk)}}

    graph = build_library_graph()
    result = await graph.ainvoke({"input_text": "超分辨率重建方法有什么问题", "session_id": "s1"}, config)

    # 重试环: 改写2次(retry_count 0->1->2), 共 7 次 LLM 调用(过滤/评分x3/改写x2/生成)
    assert result["retry_count"] == 2, f"应经历两次改写重试, got {result['retry_count']}"
    assert len(llm.calls) == 7, f"LLM 调用次数应为 7, got {len(llm.calls)}"
    # 生成结果非兜底
    assert result["content"] == "超分辨率重建方法主要包括基于CNN的深度学习方法。"
    assert result["content"] != "未能在论文库中找到充分证据回答该问题。"
    # 引用通过校验
    assert len(result["citations"]) == 1
    assert result["citations"][0]["verified"] is True
    assert result["citations"][0]["paper_id"] == 1
    assert result["citations"][0]["chunk_id"] == "c1"
    assert len(result["evidence"]) == 1
    await engine.dispose()
from app.paper.library_graph import build_library_graph


def test_graph_has_relevance_nodes():
    g = build_library_graph()
    nodes = set(g.get_graph().nodes.keys())
    assert "relevance_check" in nodes
    assert "general_chat_node" in nodes


def test_route_after_relevance():
    from app.paper.library_graph import _route_after_relevance
    assert _route_after_relevance({"intent_route": "rag"}) == "direction_select"
    assert _route_after_relevance({"intent_route": "general"}) == "general_chat_node"

def test_route_after_relevance_missing_key_defaults_rag():
    from app.paper.library_graph import _route_after_relevance
    assert _route_after_relevance({}) == "direction_select"


@pytest.mark.asyncio
async def test_graph_end_to_end_general_branch(tmp_path):
    """整图 general 分支: relevance_check 判 false -> general_chat_node, 不进 QA 子图。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/g2.db")
    sf = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with sf() as session:
        session.add(Paper(original_filename="a.pdf", stored_filename="a.pdf", title="SR", research_field="超分辨率", status="ready"))
        await session.commit()

    llm = _FakeLLM([
        "false",  # relevance_check 判定 -> general
        json.dumps({"content": "深度学习是一类基于多层神经网络的机器学习方法。", "citations": []}),
    ])
    config = {"configurable": {"llm": llm, "session_factory": sf, "retriever": _FakeRetriever(None)}}
    graph = build_library_graph()

    seq, result = [], {}
    async for step in graph.astream({"input_text": "什么是深度学习", "session_id": "s2"}, config, stream_mode="updates"):
        for n, upd in step.items():
            if n.startswith("__"):
                continue
            seq.append(n)
            if isinstance(upd, dict):
                result.update(upd)

    assert seq == ["intent_router", "relevance_check", "general_chat_node"], seq
    assert result["content"] == "深度学习是一类基于多层神经网络的机器学习方法。"
    assert result["content"] != "未能在论文库中找到充分证据回答该问题。"
    assert "evidence" not in result
    assert len(llm.calls) == 2
    await engine.dispose()


