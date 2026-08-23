"""论文精读 LangGraph 状态。"""

import operator
from typing import Annotated, Any, TypedDict


class PaperReadingState(TypedDict, total=False):
    paper_id: int
    paper_title: str
    metadata: dict[str, Any]
    sections: list[dict[str, Any]]
    chunks: list[dict[str, Any]]
    section_analysis: list[dict[str, Any]]
    evidence_context: str
    report: dict[str, Any]
    citations: list[dict[str, Any]]
    validation_errors: list[str]
    retry_count: int
    needs_retry: bool
    artifact: dict[str, Any]
    completed_nodes: Annotated[list[str], operator.add]
