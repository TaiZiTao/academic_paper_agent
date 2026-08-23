"""全库聚合检索器: 按篇采样策略与逐篇采样行为测试。"""

from app.paper.aggregate_retriever import build_evidence_plan


def test_plan_small():
    # 3 篇论文 → 每篇 3 个片段
    assert build_evidence_plan(paper_count=3, max_chars=20000, avg_chunk_chars=800) == 3


def test_plan_many():
    # 30 篇 → 自动降配, 但每篇至少 1 个
    plan = build_evidence_plan(paper_count=30, max_chars=20000, avg_chunk_chars=800)
    assert 1 <= plan <= 3


def test_plan_never_zero():
    assert build_evidence_plan(paper_count=100, max_chars=20000, avg_chunk_chars=800) >= 1


def test_plan_zero_papers():
    assert build_evidence_plan(0) == 0


def test_plan_mid():
    # 10 篇 → 中间档: 每篇 2 个
    assert build_evidence_plan(paper_count=10, max_chars=20000, avg_chunk_chars=800) == 2

import pytest


class _FakeRetriever:
    def __init__(self, results_by_paper):
        self.results_by_paper = results_by_paper

    async def search(self, paper_id, query, k=8, section=None):
        hits = self.results_by_paper.get(paper_id, [])[:k]
        return hits


def _fake_hit(chunk_id):
    from types import SimpleNamespace
    chunk = SimpleNamespace(
        paper_id=1, chunk_id=chunk_id, section="s", page_start=1, page_end=1,
        ordinal=0, content="c", metadata={},
    )
    return SimpleNamespace(chunk=chunk, score=0.9)


@pytest.mark.asyncio
async def test_sample_papers_covers_all():
    from app.paper.aggregate_retriever import AggregateRetriever
    fake = _FakeRetriever({1: [_fake_hit("a"), _fake_hit("b")], 2: [_fake_hit("c")]})
    agg = AggregateRetriever(fake)
    chunks = await agg.sample_papers([1, 2], "query", per_paper=2)
    assert len(chunks) == 3
    assert {c.chunk_id for c in chunks} == {"a", "b", "c"}


@pytest.mark.asyncio
async def test_sample_empty():
    from app.paper.aggregate_retriever import AggregateRetriever
    agg = AggregateRetriever(_FakeRetriever({}))
    assert await agg.sample_papers([], "q") == []


class _RaisingRetriever(_FakeRetriever):
    def __init__(self, results_by_paper, failing_ids):
        super().__init__(results_by_paper)
        self.failing_ids = set(failing_ids)

    async def search(self, paper_id, query, k=8, section=None):
        if paper_id in self.failing_ids:
            raise RuntimeError(f"index missing for paper {paper_id}")
        return await super().search(paper_id, query, k=k, section=section)


@pytest.mark.asyncio
async def test_sample_papers_skips_failing_paper():
    from app.paper.aggregate_retriever import AggregateRetriever
    fake = _RaisingRetriever(
        {1: [_fake_hit("a")], 2: [_fake_hit("b")], 3: [_fake_hit("c")]},
        failing_ids={2},
    )
    agg = AggregateRetriever(fake)
    chunks = await agg.sample_papers([1, 2, 3], "query", per_paper=2)
    assert {c.chunk_id for c in chunks} == {"a", "c"}


@pytest.mark.asyncio
async def test_per_paper_zero_falls_back_to_one():
    # per_paper=0 视为非法, 回退为每篇至少 1 个, 不报错
    from app.paper.aggregate_retriever import AggregateRetriever
    fake = _FakeRetriever({1: [_fake_hit("a"), _fake_hit("b")]})
    agg = AggregateRetriever(fake)
    chunks = await agg.sample_papers([1], "query", per_paper=0)
    assert [c.chunk_id for c in chunks] == ["a"]


@pytest.mark.asyncio
async def test_sample_papers_passes_k_through():
    # 某篇有 3 个 hit, per_paper=2 → k=2 透传, 只取 2 个
    from app.paper.aggregate_retriever import AggregateRetriever

    class _RecordingRetriever(_FakeRetriever):
        def __init__(self, results_by_paper):
            super().__init__(results_by_paper)
            self.k_calls = []

        async def search(self, paper_id, query, k=8, section=None):
            self.k_calls.append((paper_id, k))
            return await super().search(paper_id, query, k=k, section=section)

    fake = _RecordingRetriever({1: [_fake_hit("a"), _fake_hit("b"), _fake_hit("c")]})
    agg = AggregateRetriever(fake)
    chunks = await agg.sample_papers([1], "query", per_paper=2)
    assert [c.chunk_id for c in chunks] == ["a", "b"]
    assert fake.k_calls == [(1, 2)]

