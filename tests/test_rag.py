"""
RAG 检索系统测试

所有测试使用 MockEmbedding，不调用真实 OpenAI API。
覆盖：Embedding / VectorStore / KeywordStore / Fusion / Retriever
"""

import hashlib
import struct

import pytest

from app.parser.models import DocumentChunk
from app.rag.embedding import BaseEmbedding
from app.rag.fusion import weighted_fusion
from app.rag.keyword_store import KeywordStore
from app.rag.retriever import Retriever
from app.rag.vector_store import VectorStore


# ============================================================
# Mock Embedding — 确定性伪向量，不调用 API
# ============================================================

class MockEmbedding(BaseEmbedding):
    """
    模拟 Embedding：使用文本 hash 生成确定性伪向量。

    优点：
    - 相同文本永远生成相同向量（可复现）
    - 不同文本生成不同向量（可验证区分度）
    - 不调用任何外部 API
    """

    def __init__(self, dimension: int = 128) -> None:
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def _hash_to_vector(self, text: str) -> list[float]:
        """将文本 hash 展开为指定维度的归一化向量"""
        hash_bytes = hashlib.sha256(text.encode()).digest()
        vec = []
        for i in range(self.dimension):
            # 从 hash 中循环取 4 字节转 float
            offset = (i * 4) % len(hash_bytes)
            val = struct.unpack(">f", hash_bytes[offset:offset + 4])[0]
            vec.append(val)
        # L2 归一化
        import math
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec]

    async def embed_text(self, text: str) -> list[float]:
        return self._hash_to_vector(text)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._hash_to_vector(t) for t in texts]


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def mock_embedding():
    return MockEmbedding(dimension=128)


@pytest.fixture
def sample_chunks():
    """创建 10 个内容不同的 DocumentChunk"""
    chunks = []
    topics = [
        "GraphRAG is a knowledge graph enhanced retrieval system",
        "FAISS is a library for efficient similarity search",
        "BM25 is a bag-of-words retrieval function",
        "Embedding models convert text to dense vectors",
        "Hybrid search combines sparse and dense retrieval",
        "Python is a programming language for AI development",
        "SQLite is an embedded database engine",
        "FastAPI is a modern web framework for building APIs",
        "LangGraph enables stateful multi-actor applications",
        "RAG stands for Retrieval-Augmented Generation",
    ]
    for i, text in enumerate(topics):
        chunks.append(DocumentChunk(
            document_id=f"doc_{i // 3}",
            content=text,
            chunk_index=i,
        ))
    return chunks


@pytest.fixture
def vector_store(mock_embedding):
    return VectorStore(mock_embedding, dimension=128)


@pytest.fixture
def keyword_store():
    return KeywordStore()


@pytest.fixture
def retriever(mock_embedding):
    return Retriever(mock_embedding)


# ============================================================
# Embedding Tests
# ============================================================

class TestMockEmbedding:
    """MockEmbedding 基本行为测试"""

    @pytest.mark.asyncio
    async def test_embed_text_returns_correct_dimension(self, mock_embedding):
        vec = await mock_embedding.embed_text("hello")
        assert len(vec) == 128

    @pytest.mark.asyncio
    async def test_embed_text_is_deterministic(self, mock_embedding):
        v1 = await mock_embedding.embed_text("hello")
        v2 = await mock_embedding.embed_text("hello")
        assert v1 == v2

    @pytest.mark.asyncio
    async def test_embed_documents_different_for_different_input(self, mock_embedding):
        v1 = await mock_embedding.embed_text("hello")
        v2 = await mock_embedding.embed_text("world")
        assert v1 != v2

    @pytest.mark.asyncio
    async def test_embed_documents_batch(self, mock_embedding):
        texts = ["one", "two", "three"]
        vecs = await mock_embedding.embed_documents(texts)
        assert len(vecs) == 3
        for v in vecs:
            assert len(v) == 128


# ============================================================
# VectorStore (FAISS) Tests
# ============================================================

class TestVectorStore:
    """FAISS VectorStore 测试"""

    @pytest.mark.asyncio
    async def test_add_documents_empty(self, vector_store):
        """空列表不报错"""
        await vector_store.add_documents([])
        assert vector_store.index.ntotal == 0

    @pytest.mark.asyncio
    async def test_add_documents(self, vector_store, sample_chunks):
        await vector_store.add_documents(sample_chunks)
        assert vector_store.index.ntotal == len(sample_chunks)
        assert len(vector_store.chunks) == len(sample_chunks)

    @pytest.mark.asyncio
    async def test_search_returns_results(self, vector_store, sample_chunks):
        await vector_store.add_documents(sample_chunks)
        results = await vector_store.search("GraphRAG retrieval", k=3)
        assert len(results) == 3
        for chunk, score in results:
            assert isinstance(chunk, DocumentChunk)
            assert isinstance(score, float)

    @pytest.mark.asyncio
    async def test_search_empty_store(self, vector_store):
        """无数据时返回空列表"""
        results = await vector_store.search("query")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_k_exceeds_total(self, vector_store, sample_chunks):
        """k > 索引总数时返回实际数量"""
        await vector_store.add_documents(sample_chunks[:3])
        results = await vector_store.search("query", k=10)
        assert len(results) <= 3


# ============================================================
# KeywordStore (BM25) Tests
# ============================================================

class TestKeywordStore:
    """BM25 KeywordStore 测试"""

    def test_build_index_empty(self, keyword_store):
        keyword_store.build_index([])
        assert keyword_store.bm25 is None

    def test_build_index(self, keyword_store, sample_chunks):
        keyword_store.build_index(sample_chunks)
        assert keyword_store.bm25 is not None
        assert len(keyword_store.chunks) == len(sample_chunks)

    def test_search_returns_results(self, keyword_store, sample_chunks):
        keyword_store.build_index(sample_chunks)
        results = keyword_store.search("FAISS similarity search", k=3)
        assert len(results) == 3
        for chunk, score in results:
            assert isinstance(chunk, DocumentChunk)
            assert isinstance(score, float)

    def test_search_empty_store(self, keyword_store):
        results = keyword_store.search("query")
        assert results == []

    def test_search_rebuild_replaces_index(self, keyword_store, sample_chunks):
        """build_index 应完全替换旧索引"""
        keyword_store.build_index(sample_chunks[:2])
        keyword_store.build_index(sample_chunks[2:5])
        assert len(keyword_store.chunks) == 3


# ============================================================
# Fusion Tests
# ============================================================

class TestFusion:
    """Score Fusion 测试"""

    def test_weighted_fusion_combines_results(self, sample_chunks):
        """验证两路结果被正确合并"""
        vec_results = [(sample_chunks[0], 0.9), (sample_chunks[1], 0.7)]
        kw_results = [(sample_chunks[1], 12.0), (sample_chunks[2], 8.0)]

        fused = weighted_fusion(vec_results, kw_results,
                                vector_weight=0.7, keyword_weight=0.3)

        # 三个不同的 chunk 都应出现
        chunk_ids = {c.chunk_id for c, _ in fused}
        assert len(chunk_ids) == 3

    def test_weighted_fusion_sorted_descending(self, sample_chunks):
        """融合结果按分数降序"""
        vec_results = [(sample_chunks[0], 0.9), (sample_chunks[1], 0.5)]
        kw_results = [(sample_chunks[0], 10.0), (sample_chunks[1], 15.0)]

        fused = weighted_fusion(vec_results, kw_results,
                                vector_weight=0.7, keyword_weight=0.3)

        scores = [s for _, s in fused]
        assert scores == sorted(scores, reverse=True)

    def test_weighted_fusion_empty_inputs(self):
        """两侧都为空"""
        fused = weighted_fusion([], [])
        assert fused == []

    def test_weighted_fusion_one_side_empty(self, sample_chunks):
        """一侧有结果，另一侧为空"""
        vec_results = [(sample_chunks[0], 0.9)]
        fused = weighted_fusion(vec_results, [],
                                vector_weight=0.7, keyword_weight=0.3)
        assert len(fused) == 1

    def test_weighted_fusion_identical_scores(self, sample_chunks):
        """所有分数相同时归一化不崩溃"""
        vec_results = [(sample_chunks[0], 0.5), (sample_chunks[1], 0.5)]
        kw_results = [(sample_chunks[0], 0.3), (sample_chunks[1], 0.3)]

        fused = weighted_fusion(vec_results, kw_results)
        # 不应抛出异常
        assert len(fused) == 2


# ============================================================
# Retriever (Integration) Tests
# ============================================================

class TestRetriever:
    """Retriever 集成测试"""

    @pytest.mark.asyncio
    async def test_add_documents(self, retriever, sample_chunks):
        await retriever.add_documents(sample_chunks)
        assert retriever.chunk_count == len(sample_chunks)

    @pytest.mark.asyncio
    async def test_add_documents_empty(self, retriever):
        await retriever.add_documents([])
        assert retriever.chunk_count == 0

    @pytest.mark.asyncio
    async def test_search(self, retriever, sample_chunks):
        await retriever.add_documents(sample_chunks)
        results = await retriever.search("FAISS similarity search", k=3)
        assert len(results) == 3
        # 结果按分数降序
        scores = [s for _, s in results]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_search_empty(self, retriever):
        results = await retriever.search("query")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_chunk_ids_unique(self, retriever, sample_chunks):
        """融合后每个 chunk 只出现一次"""
        await retriever.add_documents(sample_chunks)
        results = await retriever.search("retrieval", k=10)
        chunk_ids = [c.chunk_id for c, _ in results]
        assert len(chunk_ids) == len(set(chunk_ids))  # 无重复

    @pytest.mark.asyncio
    async def test_search_respects_k(self, retriever, sample_chunks):
        await retriever.add_documents(sample_chunks)
        for k in [1, 3, 5]:
            results = await retriever.search("test query", k=k)
            assert len(results) <= k
