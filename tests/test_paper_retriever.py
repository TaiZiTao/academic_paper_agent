"""单篇论文隔离混合索引测试。"""

import importlib

import pytest

from app.paper.schemas import PaperChunkData
from app.rag.embedding import BaseEmbedding


class TinyEmbedding(BaseEmbedding):
    @property
    def dimension(self):
        return 4

    async def embed_text(self, text: str):
        return (await self.embed_documents([text]))[0]

    async def embed_documents(self, texts: list[str]):
        vectors = []
        for text in texts:
            lower = text.lower()
            vector = [
                float(lower.count("alpha")),
                float(lower.count("beta")),
                float(lower.count("method")),
                1.0,
            ]
            vectors.append(vector)
        return vectors


def _retriever_class():
    try:
        return importlib.import_module("app.paper.retriever").PaperRetriever
    except ModuleNotFoundError:
        pytest.fail("app.paper.retriever 尚未实现")


def _chunk(paper_id: int, suffix: str, content: str, section: str = "Methods"):
    return PaperChunkData(
        paper_id=paper_id,
        chunk_id=f"paper-{paper_id}-{suffix}",
        section=section,
        page_start=1,
        page_end=1,
        ordinal=0,
        content=content,
    )


@pytest.mark.asyncio
async def test_search_never_returns_another_papers_chunk(tmp_path):
    PaperRetriever = _retriever_class()
    store = PaperRetriever(TinyEmbedding(), tmp_path)
    await store.build(1, [_chunk(1, "c1", "alpha method evidence")])
    await store.build(2, [_chunk(2, "c1", "alpha unrelated content")])

    results = await store.search(1, "alpha method", k=5)

    assert results
    assert {item.chunk.paper_id for item in results} == {1}


@pytest.mark.asyncio
async def test_index_can_be_loaded_by_a_new_instance(tmp_path):
    PaperRetriever = _retriever_class()
    first = PaperRetriever(TinyEmbedding(), tmp_path)
    await first.build(3, [_chunk(3, "c1", "beta persisted evidence")])

    second = PaperRetriever(TinyEmbedding(), tmp_path)
    results = await second.search(3, "beta", k=3)

    assert results[0].chunk.chunk_id == "paper-3-c1"


@pytest.mark.asyncio
async def test_search_can_filter_section(tmp_path):
    PaperRetriever = _retriever_class()
    store = PaperRetriever(TinyEmbedding(), tmp_path)
    await store.build(
        4,
        [
            _chunk(4, "m", "alpha method", "Methods"),
            _chunk(4, "e", "alpha experiment", "Experiments"),
        ],
    )

    results = await store.search(4, "alpha", k=5, section="Experiments")

    assert results
    assert {item.chunk.section for item in results} == {"Experiments"}


@pytest.mark.asyncio
async def test_delete_removes_only_target_paper_index(tmp_path):
    PaperRetriever = _retriever_class()
    store = PaperRetriever(TinyEmbedding(), tmp_path)
    await store.build(5, [_chunk(5, "c", "alpha")])
    await store.build(6, [_chunk(6, "c", "beta")])

    store.delete(5)

    assert not (tmp_path / "5").exists()
    assert (tmp_path / "6").exists()
