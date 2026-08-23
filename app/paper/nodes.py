"""论文精读工作流节点。"""

import json
import re
from typing import Any

from langgraph.types import RunnableConfig

from app.paper.citations import CitationValidator
from app.paper.prompts import REPORT_FIELDS, build_report_prompt
from app.paper.schemas import PaperChunkData, PaperCitation
from app.paper.state import PaperReadingState


def _config_value(config: RunnableConfig | None, key: str, default=None):
    if not config:
        return default
    return config.get("configurable", {}).get(key, default)


def _json_content(content: Any) -> dict:
    text = content if isinstance(content, str) else str(content)
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise ValueError("LLM 未返回合法 JSON")
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("LLM 返回值必须是 JSON 对象")
    return value


def metadata_extract_node(state: PaperReadingState) -> dict:
    metadata = dict(state.get("metadata", {}))
    metadata.setdefault("title", state.get("paper_title", ""))
    return {"metadata": metadata, "completed_nodes": ["metadata_extract"]}


def section_analyze_node(state: PaperReadingState) -> dict:
    analysis = [
        {
            "title": section.get("title", ""),
            "normalized_title": section.get("normalized_title", "other"),
            "page_start": section.get("page_start", 1),
            "page_end": section.get("page_end", 1),
        }
        for section in state.get("sections", [])
    ]
    return {"section_analysis": analysis, "completed_nodes": ["section_analyze"]}


def contribution_extract_node(state: PaperReadingState) -> dict:
    chunks = [PaperChunkData.model_validate(item) for item in state.get("chunks", [])]
    context = "\n".join(
        f"{chunk.section} (p.{chunk.page_start}): {chunk.content[:500]}" for chunk in chunks
    )
    return {"evidence_context": context, "completed_nodes": ["contribution_extract"]}


async def report_synthesize_node(
    state: PaperReadingState,
    config: RunnableConfig | None = None,
) -> dict:
    llm = _config_value(config, "llm")
    if llm is None:
        raise ValueError("生成精读报告需要 LLM")
    chunks = [PaperChunkData.model_validate(item) for item in state.get("chunks", [])]
    prompt = build_report_prompt(
        paper_title=state.get("paper_title", ""),
        metadata=state.get("metadata", {}),
        sections=state.get("sections", []),
        chunks=chunks,
        validation_errors=state.get("validation_errors", []),
    )
    response = await llm.bind(max_tokens=8192).ainvoke(prompt)
    payload = _json_content(response.content if hasattr(response, "content") else response)
    raw_report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
    report = {
        field: raw_report.get(field, [] if field == "terms" else "原文未提供充分证据")
        for field in REPORT_FIELDS
    }
    citations: list[dict] = []
    for item in payload.get("citations", []):
        if not isinstance(item, dict):
            continue
        item.setdefault("paper_id", state.get("paper_id"))
        item.setdefault("paper_title", state.get("paper_title", ""))
        try:
            citations.append(PaperCitation.model_validate(item).model_dump())
        except Exception:
            continue
    return {
        "report": report,
        "citations": citations,
        "validation_errors": [],
        "completed_nodes": ["report_synthesize"],
    }


def citation_verify_node(state: PaperReadingState) -> dict:
    chunks = [PaperChunkData.model_validate(item) for item in state.get("chunks", [])]
    citations = [PaperCitation.model_validate(item) for item in state.get("citations", [])]
    results = CitationValidator(chunks).validate_many(citations, state.get("paper_id", 0))
    errors = [result.reason for result in results if not result.valid]
    safe_citations = [result.citation.model_dump() for result in results]
    retry_count = state.get("retry_count", 0)

    if errors and retry_count < 1:
        return {
            "citations": safe_citations,
            "validation_errors": errors,
            "needs_retry": True,
            "retry_count": retry_count + 1,
            "completed_nodes": ["citation_verify"],
        }

    report = dict(state.get("report", {}))
    if errors:
        notice = "原文未提供充分证据，相关引用已降级。"
        solution = str(report.get("solution", "")).strip()
        report["solution"] = f"{solution}\n{notice}".strip()
    return {
        "report": report,
        "citations": safe_citations,
        "validation_errors": errors,
        "needs_retry": False,
        "completed_nodes": ["citation_verify"],
    }


def artifact_persist_node(state: PaperReadingState) -> dict:
    artifact = {
        "type": "report",
        "title": "单篇论文精读报告",
        "content": state.get("report", {}),
        "citations": state.get("citations", []),
    }
    return {"artifact": artifact, "completed_nodes": ["artifact_persist"]}


def route_after_verify(state: PaperReadingState) -> str:
    return "retry" if state.get("needs_retry") else "persist"
