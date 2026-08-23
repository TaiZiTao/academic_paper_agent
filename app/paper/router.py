"""论文助手 REST、SSE 与 PDF 路由。"""

import json
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from app.api.dependencies import get_paper_service
from app.paper.schemas import PaperTaskRequest
from app.paper.service import PaperService


router = APIRouter(prefix="/papers", tags=["论文助手"])


def _iso(value):
    return value.isoformat() if value else ""


def _paper_dict(paper) -> dict:
    return {
        "id": paper.id,
        "original_filename": paper.original_filename,
        "title": paper.title,
        "authors": json.loads(paper.authors_json or "[]"),
        "abstract": paper.abstract or "",
        "keywords": json.loads(paper.keywords_json or "[]"),
        "language": paper.language,
        "page_count": paper.page_count,
        "status": paper.status,
        # getattr 兼容测试 mock 对象(SimpleNamespace 可能无此字段)
        "research_field": getattr(paper, "research_field", "") or "",
        "error_code": paper.error_code or "",
        "error_message": paper.error_message or "",
        "created_at": _iso(paper.created_at),
        "updated_at": _iso(paper.updated_at),
    }


def _section_dict(section) -> dict:
    return {
        "id": section.id,
        "title": section.title,
        "normalized_title": section.normalized_title,
        "level": section.level,
        "ordinal": section.ordinal,
        "page_start": section.page_start,
        "page_end": section.page_end,
        "summary": section.summary or "",
    }


def _artifact_dict(artifact) -> dict:
    try:
        content = json.loads(artifact.content_json or "{}")
    except json.JSONDecodeError:
        content = {"content": artifact.content_text}
    try:
        citations = json.loads(artifact.citations_json or "[]")
    except json.JSONDecodeError:
        citations = []
    return {
        "id": artifact.id,
        "task_id": artifact.task_id,
        "artifact_type": artifact.artifact_type,
        "title": artifact.title,
        "content": content,
        "content_text": artifact.content_text,
        "citations": citations,
        "created_at": _iso(artifact.created_at),
    }


def _message_dict(message) -> dict:
    try:
        citations = json.loads(message.citations_json or "[]")
    except json.JSONDecodeError:
        citations = []
    try:
        suggestions = json.loads(message.suggestions_json or "[]")
    except json.JSONDecodeError:
        suggestions = []
    return {
        "id": message.id,
        "session_id": message.session_id,
        "role": message.role,
        "content": message.content,
        "citations": citations,
        "suggestions": suggestions,
        "created_at": _iso(message.created_at),
    }


def _translation_block_dict(block) -> dict:
    return {
        "id": block.id,
        "section": block.section,
        "block_index": block.block_index,
        "page_start": block.page_start,
        "page_end": block.page_end,
        "content": block.translated_text,
        "status": block.status,
        "error_message": block.error_message or "",
    }


def _sse(event: dict) -> str:
    event_type = event.get("event", "message")
    payload = {key: value for key, value in event.items() if key != "event"}
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("", status_code=202)
async def upload_paper(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    service: PaperService = Depends(get_paper_service),
):
    filename = file.filename or ""
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 PDF 文件")
    content = await file.read()
    try:
        paper = await service.create_paper(filename, content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    background_tasks.add_task(service.process_paper, paper.id)
    return _paper_dict(paper)


@router.get("")
async def list_papers(
    search: str = "",
    page: int = 1,
    page_size: int = 20,
    field: str = "",
    group: bool = False,
    service: PaperService = Depends(get_paper_service),
):
    result = await service.list_papers(
        search=search, page=page, page_size=page_size, field=field, group=group
    )
    if group:
        return {
            "groups": [
                {
                    "field": item["field"],
                    "count": item["count"],
                    "items": [_paper_dict(p) for p in item["items"]],
                }
                for item in result["groups"]
            ],
            "fields": result["fields"],
            "total": result["total"],
        }
    return {
        "items": [_paper_dict(item) for item in result["items"]],
        "fields": result.get("fields", []),
        "total": result["total"],
    }


@router.post("/qa/stream")
async def library_qa_stream(
    request: dict,
    service: PaperService = Depends(get_paper_service),
):
    input_text = str(request.get("input_text", "")).strip()
    if not input_text:
        raise HTTPException(status_code=400, detail="问题不能为空")
    session_id = str(request.get("session_id", "") or "")

    async def stream():
        try:
            async for event in service.run_library_qa(input_text, session_id):
                yield _sse(event)
        except Exception as exc:
            yield _sse({"event": "error", "detail": str(exc)})

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.get("/qa/history")
async def library_qa_history(
    session_id: str,
    service: PaperService = Depends(get_paper_service),
):
    if not session_id:
        raise HTTPException(status_code=400, detail="缺少 session_id")
    messages = await service.get_library_history(session_id)
    return {"session_id": session_id, "messages": messages}


@router.get("/{paper_id}")
async def get_paper_detail(paper_id: int, service: PaperService = Depends(get_paper_service)):
    detail = await service.get_detail(paper_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="论文不存在")
    return {
        "paper": _paper_dict(detail["paper"]),
        "sections": [_section_dict(item) for item in detail["sections"]],
        "artifacts": [_artifact_dict(item) for item in detail["artifacts"]],
        "messages": [_message_dict(item) for item in detail["messages"]],
        "translation_blocks": [
            _translation_block_dict(item) for item in detail.get("translation_blocks", [])
        ],
        "figures": detail.get("figures", []),
    }


@router.get("/{paper_id}/events")
async def paper_events(paper_id: int, service: PaperService = Depends(get_paper_service)):
    async def stream():
        async for event in service.progress_events(paper_id):
            yield _sse(event)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.post("/{paper_id}/retry", status_code=202)
async def retry_paper(
    paper_id: int,
    background_tasks: BackgroundTasks,
    service: PaperService = Depends(get_paper_service),
):
    paper = await service.get_paper(paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="论文不存在")
    if paper.status != "failed":
        raise HTTPException(status_code=409, detail="只有失败的论文可以重试")
    background_tasks.add_task(service.process_paper, paper_id)
    return {"id": paper_id, "status": "uploaded"}


@router.post("/{paper_id}/tasks/stream")
async def run_paper_task(
    paper_id: int,
    request: PaperTaskRequest,
    service: PaperService = Depends(get_paper_service),
):
    async def stream():
        async for event in service.run_task(
            paper_id,
            task_type=request.task_type,
            input_text=request.input_text,
            session_id=request.session_id,
            section=request.section,
        ):
            yield _sse(event)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.get("/{paper_id}/figures/{figure_id}/image")
async def get_figure_image(
    paper_id: int,
    figure_id: int,
    service: PaperService = Depends(get_paper_service),
):
    image_path = await service.figure_image_path(paper_id, figure_id)
    if image_path is None or not image_path.exists():
        raise HTTPException(status_code=404, detail="图表图片不存在")
    return FileResponse(
        image_path,
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/{paper_id}/pdf")
async def get_paper_pdf(paper_id: int, service: PaperService = Depends(get_paper_service)):
    paper = await service.get_paper(paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="论文不存在")
    path = service.file_path(paper)
    if not path.exists():
        raise HTTPException(status_code=404, detail="论文 PDF 不存在")
    encoded = quote(paper.original_filename)
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{encoded}"},
    )


@router.put("/{paper_id}/field")
async def update_paper_field(
    paper_id: int,
    payload: dict[str, str],
    service: PaperService = Depends(get_paper_service),
):
    field = payload.get("field", "")
    paper = await service.update_field(paper_id, field)
    if paper is None:
        raise HTTPException(status_code=404, detail="论文不存在")
    return _paper_dict(paper)


@router.delete("/{paper_id}", status_code=204)
async def delete_paper(paper_id: int, service: PaperService = Depends(get_paper_service)):
    if not await service.delete_paper(paper_id):
        raise HTTPException(status_code=404, detail="论文不存在")
    return JSONResponse(status_code=204, content=None)
