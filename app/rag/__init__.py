"""
RAG 检索系统

提供文档索引和混合检索能力。

模块架构：
    Retriever（统一入口）
      ├── VectorStore（FAISS 密集检索）── BaseEmbedding（向量化）
      ├── KeywordStore（BM25 稀疏检索）
      └── weighted_fusion（分数融合）
"""

from app.rag.embedding import BaseEmbedding, OpenAIEmbedding
from app.rag.fusion import weighted_fusion
from app.rag.keyword_store import KeywordStore
from app.rag.retriever import Retriever
from app.rag.vector_store import VectorStore

__all__ = [
    "BaseEmbedding",
    "OpenAIEmbedding",
    "VectorStore",
    "KeywordStore",
    "weighted_fusion",
    "Retriever",
]
