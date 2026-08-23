"""
FAISS 向量存储

管理 FAISS 索引，负责：
- 文档向量化 + 入库
- 查询向量化 + 相似度检索

依赖 BaseEmbedding 接口，不绑定具体 Embedding 实现。
"""

import numpy as np
import faiss

from app.parser.models import DocumentChunk
from app.rag.embedding import BaseEmbedding


class VectorStore:
    """
    FAISS 向量索引管理器

    使用 IndexFlatIP（内积），配合归一化向量等同于余弦相似度。
    所有操作同步执行（FAISS 不支持 async），embedding 调用在外部已 await。
    """

    def __init__(self, embedding: BaseEmbedding, dimension: int = 1536) -> None:
        self.embedding = embedding
        self.dimension = dimension
        self.index: faiss.IndexFlatIP = faiss.IndexFlatIP(dimension)
        self.chunks: list[DocumentChunk] = []

    def _to_numpy(self, vectors: list[list[float]]) -> np.ndarray:
        """将向量列表转为 FAISS 兼容的 float32 numpy 数组"""
        arr = np.array(vectors, dtype=np.float32)
        # FAISS IndexFlatIP 要求向量已归一化
        faiss.normalize_L2(arr)
        return arr

    async def add_documents(self, chunks: list[DocumentChunk]) -> None:
        """
        将文档片段向量化并加入 FAISS 索引。

        步骤：embedding → numpy → L2 normalize → FAISS.add
        """
        if not chunks:
            return

        texts = [c.content for c in chunks]
        vectors = await self.embedding.embed_documents(texts)
        np_vectors = self._to_numpy(vectors)
        self.index.add(np_vectors)
        self.chunks.extend(chunks)

    async def search(
        self, query: str, k: int = 5
    ) -> list[tuple[DocumentChunk, float]]:
        """
        向量相似度检索。

        Returns
        -------
        list[tuple[DocumentChunk, float]]
            按相似度降序排列的 (chunk, score) 列表
        """
        if self.index.ntotal == 0:
            return []

        query_vec = await self.embedding.embed_text(query)
        np_query = self._to_numpy([query_vec])

        k = min(k, self.index.ntotal)
        scores, indices = self.index.search(np_query, k)

        results: list[tuple[DocumentChunk, float]] = []
        for idx, score in zip(indices[0], scores[0]):
            if idx >= 0 and idx < len(self.chunks):
                results.append((self.chunks[idx], float(score)))

        return results

    def save(self, dir_path: str) -> None:
        """保存 FAISS 索引和 chunks 到磁盘"""
        import os, pickle
        os.makedirs(dir_path, exist_ok=True)
        faiss.write_index(self.index, os.path.join(dir_path, "faiss.index"))
        with open(os.path.join(dir_path, "chunks.pkl"), "wb") as f:
            pickle.dump(self.chunks, f)

    def load(self, dir_path: str) -> bool:
        """从磁盘加载 FAISS 索引和 chunks，返回是否成功"""
        import os, pickle
        faiss_path = os.path.join(dir_path, "faiss.index")
        chunks_path = os.path.join(dir_path, "chunks.pkl")
        if not os.path.exists(faiss_path) or not os.path.exists(chunks_path):
            return False
        self.index = faiss.read_index(faiss_path)
        with open(chunks_path, "rb") as f:
            self.chunks = pickle.load(f)
        self.dimension = self.index.d
        return True
