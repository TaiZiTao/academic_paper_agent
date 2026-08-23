"""
论文知识问答工作流构建

8 节点 → 条件重试：
  START → query_rewrite → intent_router → knowledge_selection
  → retrieval → relevance_evaluation
    ├─ 相关性不足 → 回到 query_rewrite（最多 2 次重试）
    └─ 相关性达标 → generation → citation_formatting → error_handler → END
"""

from langgraph.graph import END, START, StateGraph

from app.graph.nodes import (
    citation_formatting_node,
    error_handler_node,
    generation_node,
    intent_router_node,
    knowledge_selection_node,
    query_rewrite_node,
    relevance_evaluation_node,
    retrieval_node,
)
from app.graph.state import GraphState

MAX_RETRIES = 3


def _should_retry(state: GraphState) -> str:
    """相关性条件判断，返回 "retry" 或 "next" """
    retry_count = state.get("retry_count", 0)
    if retry_count >= MAX_RETRIES:
        return "next"

    scores = state.get("relevance_scores", [])
    docs = state.get("retrieved_documents", [])

    if not docs:
        need_retry = True
    elif not scores:
        need_retry = False
    else:
        avg = sum(s["score"] for s in scores) / len(scores)
        max_score = max(s["score"] for s in scores)
        need_retry = avg < 2 or max_score < 3

    if need_retry:
        state["retry_count"] = retry_count + 1
        return "retry"
    return "next"


def build_graph(checkpointer=None) -> StateGraph:
    """编译 QA 工作流图，可选注入 checkpointer 持久化状态"""
    graph = StateGraph(GraphState)

    for node in [
        ("query_rewrite", query_rewrite_node),
        ("intent_router", intent_router_node),
        ("knowledge_selection", knowledge_selection_node),
        ("retrieval", retrieval_node),
        ("relevance_evaluation", relevance_evaluation_node),
        ("generation", generation_node),
        ("citation_formatting", citation_formatting_node),
        ("error_handler", error_handler_node),
    ]:
        graph.add_node(*node)

    graph.add_edge(START, "query_rewrite")
    graph.add_edge("query_rewrite", "intent_router")
    graph.add_edge("intent_router", "knowledge_selection")
    graph.add_edge("knowledge_selection", "retrieval")
    graph.add_edge("retrieval", "relevance_evaluation")

    graph.add_conditional_edges("relevance_evaluation", _should_retry, {
        "retry": "query_rewrite",
        "next": "generation",
    })

    graph.add_edge("generation", "citation_formatting")
    graph.add_edge("citation_formatting", "error_handler")
    graph.add_edge("error_handler", END)

    if checkpointer:
        return graph.compile(checkpointer=checkpointer)
    return graph.compile()
