"""
统一检索器

编排 VectorStore + KeywordStore + Fusion，提供单一检索入口。

职责：
- add_documents(): 同时写入 FAISS 和 BM25
- search(): 双路检索 → 融合 → Top-K

CLAUDE.md 约束：
- 不实现检索算法（委托给 vector/keyword store）
- 不处理业务逻辑
- 不直接操作数据库
"""

from app.parser.models import DocumentChunk
from app.rag.embedding import BaseEmbedding
from app.rag.fusion import weighted_fusion
from app.rag.keyword_store import KeywordStore
from app.rag.vector_store import VectorStore


class Retriever:
    """
    混合检索器

    对外暴露 add_documents 和 search 两个核心方法。
    内部协调 VectorStore（密集检索）和 KeywordStore（稀疏检索）。
    """

    def __init__(
        self,
        embedding: BaseEmbedding,
        vector_weight: float = 0.7,
        keyword_weight: float = 0.3,
    ) -> None:
        self.vector_store = VectorStore(embedding, dimension=embedding.dimension)
        self.keyword_store = KeywordStore()
        self.vector_weight = vector_weight
        self.keyword_weight = keyword_weight
        self._chunks: list[DocumentChunk] = []
        self._save_dir: str | None = None

    def set_save_dir(self, dir_path: str) -> None:
        """设置索引持久化目录"""
        import os
        os.makedirs(dir_path, exist_ok=True)
        self._save_dir = dir_path

    @property
    def chunk_count(self) -> int:
        """已入库文档片段数量"""
        return len(self._chunks)

    async def add_documents(self, chunks: list[DocumentChunk]) -> None:
        """
        将文档片段同时写入 FAISS 和 BM25 索引。

        两个索引独立更新，一个失败不影响另一个 ——
        但当前实现中，BM25 build_index 是全量替换，FAISS 是追加。
        因此调用方应一次性传入所有 chunks，不要多次调用。
        """
        if not chunks:
            return

        self._chunks.extend(chunks)

        # BM25 需要全量重建（不支持增量）
        self.keyword_store.build_index(self._chunks)

        # FAISS 增量追加（仅对新 chunks 做 embedding）
        await self.vector_store.add_documents(chunks)

        # 自动持久化到磁盘
        if self._save_dir:
            self.save(self._save_dir)

    async def search(
        self, query: str, k: int = 5, kb_id: int | None = None
    ) -> list[tuple[DocumentChunk, float]]:
        """
        混合检索：FAISS + BM25 → Fusion → Top-K

        支持 kb_id 过滤：多取候选，融合后按 kb_id 筛选。
        """
        if not query or not self._chunks:
            return []

        # 有 kb_id 过滤时多取候选，补偿过滤损失
        candidate_k = k * 4 if kb_id else k * 2

        vector_results = await self.vector_store.search(query, candidate_k)
        keyword_results = self.keyword_store.search(query, candidate_k)

        fused = weighted_fusion(
            vector_results,
            keyword_results,
            vector_weight=self.vector_weight,
            keyword_weight=self.keyword_weight,
        )

        # kb_id 过滤
        if kb_id:
            fused = [
                (c, s) for c, s in fused
                if c.metadata.get("kb_id") == kb_id
            ]

        return fused[:k]

    def remove_by_chunk_ids(self, chunk_ids: set[str]) -> None:
        """按 chunk_id 移除片段，同步清理 FAISS + BM25 + 磁盘"""
        import numpy as np, faiss

        # 找到要删除的索引位置
        indices_to_remove: list[int] = []
        keep_chunks: list = []
        for i, c in enumerate(self._chunks):
            if c.chunk_id in chunk_ids:
                indices_to_remove.append(i)
            else:
                keep_chunks.append(c)

        if not indices_to_remove:
            return

        # FAISS: 按 ID 删除
        ids_selector = faiss.IDSelectorArray(np.array(indices_to_remove, dtype=np.int64))
        self.vector_store.index.remove_ids(ids_selector)

        # 更新内存状态
        self._chunks = keep_chunks
        self.vector_store.chunks = keep_chunks

        # BM25 重建（基于保留的 chunks）
        self.keyword_store.build_index(self._chunks)

        # 存盘
        if self._save_dir:
            self.save(self._save_dir)

    def save(self, dir_path: str) -> None:
        """持久化 FAISS 索引 + BM25 chunks 到磁盘"""
        self.vector_store.save(dir_path)

    def load(self, dir_path: str) -> bool:
        """从磁盘恢复 FAISS 索引 + 重建 BM25"""
        ok = self.vector_store.load(dir_path)
        if ok and self.vector_store.chunks:
            self._chunks = list(self.vector_store.chunks)
            self.keyword_store.build_index(self._chunks)
        return ok
