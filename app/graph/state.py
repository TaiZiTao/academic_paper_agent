"""
LangGraph 工作流全局状态定义

所有 Node 通过此 State 单向传递数据，禁止 Node 之间直接调用。
"""

from typing import TypedDict


class GraphState(TypedDict, total=False):
    """QA 工作流共享状态，Node 可通过返回 dict 做部分更新"""

    session_id: str
    kb_id: int

    query: str
    rewritten_query: str

    intent: str
    messages: list[dict]
    retrieved_documents: list[dict]

    answer: str
    citations: list[str]
    relevance_scores: list[dict]

    error: str
    retry_count: int
