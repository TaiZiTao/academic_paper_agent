"""论文全库问答 LangGraph 图构建。

START -> intent_router
  |-- chitchat -> chat_node -> END
  |-- catalog  -> catalog_node -> END
  `-- qa -> relevance_check
        |-- rag     -> direction_select -> retrieve -> relevance_evaluate
        |              ^                                |
        |              `-------- rewrite_query <--------+  (不足重试, 最多3次)
        |              达标 -> generate -> cite_verify -> END
        `-- general -> general_chat_node -> END
"""

from langgraph.graph import END, START, StateGraph

from app.paper.library_nodes import (
    catalog_node,
    chat_node,
    cite_verify_node,
    direction_select_node,
    general_chat_node,
    generate_node,
    intent_router_node,
    relevance_check_node,
    relevance_evaluate_node,
    retrieve_node,
    rewrite_query_node,
    should_retry,
)
from app.paper.library_state import LibraryQAState


def _route_after_intent(state: LibraryQAState) -> str:
    return {"chitchat": "chat_node", "catalog": "catalog_node", "qa": "relevance_check"}.get(
        state.get("intent", "qa"), "relevance_check"
    )


def _route_after_relevance(state: LibraryQAState) -> str:
    """根据相关性判定路由: rag -> direction_select, general -> general_chat_node。"""
    return "direction_select" if state.get("intent_route", "rag") == "rag" else "general_chat_node"


def build_library_graph():
    builder = StateGraph(LibraryQAState)
    for node in [
        ("intent_router", intent_router_node),
        ("chat_node", chat_node),
        ("catalog_node", catalog_node),
        ("direction_select", direction_select_node),
        ("relevance_check", relevance_check_node),
        ("general_chat_node", general_chat_node),
        ("retrieve", retrieve_node),
        ("relevance_evaluate", relevance_evaluate_node),
        ("rewrite_query", rewrite_query_node),
        ("generate", generate_node),
        ("cite_verify", cite_verify_node),
    ]:
        builder.add_node(*node)

    builder.add_edge(START, "intent_router")
    builder.add_conditional_edges("intent_router", _route_after_intent, {
        "chat_node": "chat_node",
        "catalog_node": "catalog_node",
        "relevance_check": "relevance_check",
    })
    builder.add_conditional_edges("relevance_check", _route_after_relevance, {
        "direction_select": "direction_select",
        "general_chat_node": "general_chat_node",
    })
    builder.add_edge("chat_node", END)
    builder.add_edge("catalog_node", END)
    builder.add_edge("general_chat_node", END)
    builder.add_edge("direction_select", "retrieve")
    builder.add_edge("retrieve", "relevance_evaluate")
    builder.add_conditional_edges("relevance_evaluate", should_retry, {
        "retry": "rewrite_query",
        "next": "generate",
    })
    builder.add_edge("rewrite_query", "retrieve")
    builder.add_edge("generate", "cite_verify")
    builder.add_edge("cite_verify", END)
    return builder.compile()
