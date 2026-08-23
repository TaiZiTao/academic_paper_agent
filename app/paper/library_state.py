from typing import TypedDict


class LibraryQAState(TypedDict, total=False):
    """论文全库问答工作流共享状态, 节点通过返回 dict 做部分更新。"""

    session_id: str
    input_text: str
    history: list[dict]          # 多轮上下文
    intent: str                 # chitchat | catalog | qa
    intent_route: str             # "rag" | "general"(相关性判定结果)
    matched_papers: list        # 单篇/对比匹配到的论文
    filters: dict               # 动态过滤条件
    candidates: list            # 候选论文(ORM Paper 对象)
    query: str                  # 当前检索词(重试时被改写)
    evidence: list              # PaperChunkData 列表
    relevance_scores: list      # top-3 评分
    retry_count: int
    content: str                # 最终回答
    citations: list             # 安全引用
    raw_citations: list         # generate 产出的未校验引用
    degraded: list[str]         # 降级标记
