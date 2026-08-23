"""
BM25 关键词检索

基于 rank-bm25 的稀疏检索，与 FAISS 完全独立。
不依赖 Embedding，不涉及向量计算。

索引为纯内存结构，无需外部服务。
"""

from rank_bm25 import BM25Okapi

from app.parser.models import DocumentChunk


class KeywordStore:
    """
    BM25 关键词索引管理器

    使用 BM25Okapi 算法，支持中英文混合文本。
    tokenize 策略：英文按空格分词，中文按字符切分。
    """

    def __init__(self) -> None:
        self.bm25: BM25Okapi | None = None
        self.chunks: list[DocumentChunk] = []
        self._tokenized_corpus: list[list[str]] = []

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """
        简单分词：按空格分割，保留非空 token。

        不做中文分词或停用词过滤，保持简单可预测。
        后续可替换为 jieba 等分词器。
        """
        return [t for t in text.split() if t]

    def build_index(self, chunks: list[DocumentChunk]) -> None:
        """
        构建 BM25 索引。

        会完全替换已有索引（不支持增量）。
        """
        if not chunks:
            return

        self.chunks = list(chunks)
        self._tokenized_corpus = [self._tokenize(c.content) for c in chunks]
        self.bm25 = BM25Okapi(self._tokenized_corpus)

    def search(
        self, query: str, k: int = 5
    ) -> list[tuple[DocumentChunk, float]]:
        """
        关键词检索。

        Returns
        -------
        list[tuple[DocumentChunk, float]]
            按 BM25 分数降序排列的 (chunk, score) 列表
        """
        if self.bm25 is None or not self.chunks:
            return []

        tokenized_query = self._tokenize(query)
        scores = self.bm25.get_scores(tokenized_query)

        # 获取 top-k 索引
        k = min(k, len(scores))
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]

        return [(self.chunks[i], float(scores[i])) for i in top_indices]
