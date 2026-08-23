"""
分数融合（Score Fusion）

对多路检索结果进行分数融合，输出统一排序。
当前实现：Min-Max 归一化 + 加权求和。

保持纯函数，不依赖任何外部状态。
"""

from app.parser.models import DocumentChunk

# 类型别名：检索结果格式
SearchResult = list[tuple[DocumentChunk, float]]


def _min_max_normalize(results: SearchResult) -> dict[str, float]:
    """
    Min-Max 归一化。

    将分数映射到 [0, 1] 区间。
    如果所有分数相同（max == min），所有分数归一化为 1.0。
    """
    if not results:
        return {}

    scores = [score for _, score in results]
    min_score = min(scores)
    max_score = max(scores)

    if max_score == min_score:
        return {chunk.chunk_id: 1.0 for chunk, _ in results}

    return {
        chunk.chunk_id: (score - min_score) / (max_score - min_score)
        for chunk, score in results
    }


def weighted_fusion(
    vector_results: SearchResult,
    keyword_results: SearchResult,
    vector_weight: float = 0.7,
    keyword_weight: float = 0.3,
) -> SearchResult:
    """
    加权分数融合。

    流程：
    1. 分别对两路结果做 Min-Max 归一化
    2. 按 chunk_id 合并：final = w_vec * norm_vec + w_kw * norm_kw
    3. 按最终分数降序排列

    如果某路结果中不包含某个 chunk，该路分数为 0。

    Parameters
    ----------
    vector_results : SearchResult
        FAISS 向量检索结果
    keyword_results : SearchResult
        BM25 关键词检索结果
    vector_weight : float
        向量检索权重
    keyword_weight : float
        关键词检索权重

    Returns
    -------
    SearchResult
        融合后按分数降序排列的结果
    """
    # 归一化
    vec_norm = _min_max_normalize(vector_results)
    kw_norm = _min_max_normalize(keyword_results)

    # 合并所有出现过的 chunk（去重）
    all_chunks: dict[str, DocumentChunk] = {}
    for chunk, _ in vector_results:
        all_chunks[chunk.chunk_id] = chunk
    for chunk, _ in keyword_results:
        all_chunks[chunk.chunk_id] = chunk

    # 加权求和
    fused: list[tuple[DocumentChunk, float]] = []
    for chunk_id, chunk in all_chunks.items():
        v_score = vec_norm.get(chunk_id, 0.0)
        k_score = kw_norm.get(chunk_id, 0.0)
        final_score = vector_weight * v_score + keyword_weight * k_score
        fused.append((chunk, final_score))

    # 降序排列
    fused.sort(key=lambda x: x[1], reverse=True)
    return fused
