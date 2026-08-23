"""单篇论文精读六节点 LangGraph。"""

from langgraph.graph import END, START, StateGraph

from app.paper.nodes import (
    artifact_persist_node,
    citation_verify_node,
    contribution_extract_node,
    metadata_extract_node,
    report_synthesize_node,
    route_after_verify,
    section_analyze_node,
)
from app.paper.state import PaperReadingState


def build_paper_graph():
    builder = StateGraph(PaperReadingState)
    builder.add_node("metadata_extract", metadata_extract_node)
    builder.add_node("section_analyze", section_analyze_node)
    builder.add_node("contribution_extract", contribution_extract_node)
    builder.add_node("report_synthesize", report_synthesize_node)
    builder.add_node("citation_verify", citation_verify_node)
    builder.add_node("artifact_persist", artifact_persist_node)

    builder.add_edge(START, "metadata_extract")
    builder.add_edge("metadata_extract", "section_analyze")
    builder.add_edge("section_analyze", "contribution_extract")
    builder.add_edge("contribution_extract", "report_synthesize")
    builder.add_edge("report_synthesize", "citation_verify")
    builder.add_conditional_edges(
        "citation_verify",
        route_after_verify,
        {"retry": "report_synthesize", "persist": "artifact_persist"},
    )
    builder.add_edge("artifact_persist", END)
    return builder.compile()
