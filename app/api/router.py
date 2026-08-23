"""
API 路由 — 薄层

只做 HTTP 协议转换：接收请求 → 校验参数 → 调用 Service → 返回结果。
"""

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.dependencies import get_qa_config, get_qa_service, get_retriever
from app.api.schemas import (
    AskRequest,
    AskResponse,
    DocumentListResponse,
    DocumentResponse,
    DocumentUploadResponse,
    ErrorResponse,
    KBCreateRequest,
    KBListResponse,
    KBResponse,
    KBUpdateRequest,
)
from app.config.settings import settings
from app.database import async_session
from app.services.document_service import DocumentService
from app.services.kb_service import KBService
from app.services.qa_service import QAService
from app.utils.exceptions import AppException

# 上传文件存储目录
UPLOAD_DIR = Path(settings.data_dir) / "uploads"

router = APIRouter()

@router.post(
    "/chat",
    response_model=AskResponse,
    summary="知识问答",
    description="基于检索增强生成(RAG)的论文知识问答接口。",
    responses={
        200: {"description": "问答成功"},
        422: {"description": "请求参数校验失败"},
        500: {"description": "服务内部错误"},
    },
)
async def chat(
    request: AskRequest,
    service: QAService = Depends(get_qa_service),
    config: dict = Depends(get_qa_config),
):
    """
    知识问答接口。

    接收用户问题和会话 ID，返回基于知识库检索增强的答案。
    """
    try:
        result = await service.ask(
            session_id=request.session_id,
            query=request.query,
            config=config,
            kb_id=request.kb_id,
        )
        return AskResponse(
            session_id=result.session_id,
            answer=result.answer,
            citations=result.citations,
            intent=result.intent,
        )
    except AppException as e:
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                detail=e.message,
                error_type="app_error",
            ).model_dump(),
        )
    except Exception:
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                detail="服务器内部错误",
                error_type="internal_error",
            ).model_dump(),
        )


@router.post(
    "/chat/stream",
    summary="SSE 流式问答",
    description="基于 LangGraph astream 的实时流式问答，返回 SSE 事件。",
)
async def chat_stream(
    request: AskRequest,
    service: QAService = Depends(get_qa_service),
    config: dict = Depends(get_qa_config),
):
    """SSE 流式问答：每个 Node 完成后推送事件"""

    async def event_stream():
        try:
            async for chunk in service.ask_stream(
                session_id=request.session_id,
                query=request.query,
                config=config,
                kb_id=request.kb_id,
            ):
                yield chunk
        except Exception:
            import json
            yield f"event: error\ndata: {json.dumps({'detail': '服务器内部错误'})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================
# KnowledgeBase CRUD (Phase 13)
# ============================================================

def _get_kb_service() -> KBService:
    """KBService 工厂"""
    return KBService(async_session, embedding=get_retriever().vector_store.embedding)


@router.get("/kb", response_model=KBListResponse, summary="知识库列表")
async def list_kb(
    search: str = "",
    page: int = 1,
    page_size: int = 10,
    service: KBService = Depends(_get_kb_service),
):
    """获取知识库列表，支持名称搜索和分页"""
    result = await service.list_kb(search=search, page=page, page_size=page_size)
    items = [
        KBResponse(
            id=kb.id,
            name=kb.name,
            description=kb.description or "",
            created_at=kb.created_at.isoformat() if kb.created_at else "",
            updated_at=kb.updated_at.isoformat() if kb.updated_at else "",
        )
        for kb in result["items"]
    ]
    return KBListResponse(items=items, total=result["total"])


@router.post("/kb", response_model=KBResponse, status_code=201, summary="创建知识库")
async def create_kb(
    request: KBCreateRequest,
    service: KBService = Depends(_get_kb_service),
):
    """创建新的知识库"""
    kb = await service.create_kb(name=request.name, description=request.description)
    return KBResponse(
        id=kb.id,
        name=kb.name,
        description=kb.description or "",
        created_at=kb.created_at.isoformat() if kb.created_at else "",
        updated_at=kb.updated_at.isoformat() if kb.updated_at else "",
    )


@router.put("/kb/{kb_id}", response_model=KBResponse, summary="编辑知识库")
async def update_kb(
    kb_id: int,
    request: KBUpdateRequest,
    service: KBService = Depends(_get_kb_service),
):
    """编辑知识库名称或描述"""
    kb = await service.update_kb(
        kb_id, name=request.name, description=request.description
    )
    if kb is None:
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(detail="知识库不存在", error_type="not_found").model_dump(),
        )
    return KBResponse(
        id=kb.id,
        name=kb.name,
        description=kb.description or "",
        created_at=kb.created_at.isoformat() if kb.created_at else "",
        updated_at=kb.updated_at.isoformat() if kb.updated_at else "",
    )


@router.delete("/kb/{kb_id}", summary="删除知识库")
async def delete_kb(
    kb_id: int,
    service: KBService = Depends(_get_kb_service),
):
    """删除知识库（仅当库内无文档时允许）"""
    # 检查是否有文档
    from app.services.document_service import DocumentService as _DS
    doc_service = _DS(get_retriever(), session_factory=async_session)
    docs = await doc_service.list_documents(kb_id=kb_id, page_size=1)
    if docs["total"] > 0:
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(
                detail=f"知识库内还有 {docs['total']} 个文档，请先删除文档再删除知识库",
                error_type="validation_error",
            ).model_dump(),
        )

    ok = await service.delete_kb(kb_id)
    if not ok:
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(detail="知识库不存在", error_type="not_found").model_dump(),
        )
    return {"detail": "ok"}


# ============================================================
# Document Upload (Phase 13B)
# ============================================================

ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf", ".html", ".htm"}


@router.post(
    "/documents/upload",
    response_model=DocumentUploadResponse,
    summary="上传文档",
    description="上传 .txt 或 .md 文件，自动解析并加入知识库检索索引。",
)
async def upload_document(
    file: UploadFile = File(...),
    kb_id: int = Form(0),
):
    """上传文档 → 解析 → 切分 → 索引"""
    # 1. 校验文件类型
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(
                detail=f"不支持的文件类型：{suffix}，当前仅支持 {', '.join(ALLOWED_EXTENSIONS)}",
                error_type="validation_error",
            ).model_dump(),
        )

    # 2. 保存到临时目录
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex[:8]}_{file.filename}"
    file_path = UPLOAD_DIR / safe_name

    try:
        content = await file.read()
        file_path.write_bytes(content)

        # 3. 调用 DocumentService 摄入 + 写入 DB 记录
        retriever = get_retriever()
        doc_service = DocumentService(retriever, session_factory=async_session)
        doc_service._kb_id = kb_id
        doc_service._original_filename = file.filename or "unknown"

        chunks_count, chunk_ids = await doc_service.ingest_file(
            str(file_path),
            chunk_size=settings.parser_chunk_size,
            overlap=settings.parser_chunk_overlap,
        )

        # 4. 创建数据库记录（含 chunk_ids 用于后续精确删除）
        doc = await doc_service.create_record(
            kb_id=kb_id,
            original_filename=file.filename or "unknown",
            stored_filename=safe_name,
            extension=suffix,
            size=len(content),
            chunk_count=chunks_count,
            chunk_ids=chunk_ids,
        )

        return DocumentUploadResponse(
            id=doc.id,
            filename=file.filename or "unknown",
            chunks_count=chunks_count,
            kb_id=kb_id,
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                detail=f"文件处理失败：{e}",
                error_type="app_error",
            ).model_dump(),
        )
    finally:
        pass  # 保留文件，重启后可恢复索引


# ============================================================
# Conversation API (Phase 15)
# ============================================================

@router.get("/conversations", summary="会话列表")
async def list_conversations():
    """从 SQLite 获取会话列表，用首条用户消息做标题"""
    from sqlalchemy import text

    async with async_session() as session:
        result = await session.execute(
            text(
                "SELECT c.session_id, "
                "  COALESCE("
                "    (SELECT content FROM conversation_messages"
                "     WHERE session_id = c.session_id AND role = 'user'"
                "     ORDER BY created_at ASC LIMIT 1),"
                "    c.session_id"
                "  ) AS title, "
                "  MAX(c.created_at) AS updated_at "
                "FROM conversation_messages c "
                "WHERE c.session_id GLOB 'sess_[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'"  # 仅前端生成的 8 位 hex ID
                "GROUP BY c.session_id "
                "ORDER BY updated_at DESC LIMIT 100"
            )
        )
        rows = result.fetchall()
    return {
        "conversations": [
            {
                "id": row[0],
                "title": (row[1][:30] + "..." if len(row[1]) > 30 else row[1]),
                "updated_at": row[2] if row[2] else "",
            }
            for row in rows
        ]
    }


@router.get("/settings", summary="系统配置")
async def get_settings():
    """返回前端系统设置页所需的非敏感配置"""
    return {
        "app_name": settings.app_name,
        "app_version": settings.app_version,
        "llm_model": settings.llm_model,
        "llm_provider": settings.llm_provider,
        "embedding_model": settings.embedding_model,
        "chunk_size": settings.parser_chunk_size,
        "chunk_overlap": settings.parser_chunk_overlap,
        "top_k": settings.retrieval_top_k,
        "vector_weight": settings.retrieval_vector_weight,
        "keyword_weight": settings.retrieval_keyword_weight,
    }


@router.get("/stats", summary="首页统计")
async def get_stats():
    """返回首页仪表盘的统计数据"""
    from sqlalchemy import text
    async with async_session() as session:
        kb_count = (await session.execute(text("SELECT COUNT(*) FROM knowledge_bases"))).scalar() or 0
        doc_count = (await session.execute(text("SELECT COUNT(*) FROM documents"))).scalar() or 0
        sess_count = (await session.execute(text(
            "SELECT COUNT(DISTINCT session_id) FROM conversation_messages WHERE session_id GLOB 'sess_[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'"
        ))).scalar() or 0
        today_count = (await session.execute(text(
            "SELECT COUNT(*) FROM conversation_messages WHERE role='user' AND date(created_at)=date('now')"
        ))).scalar() or 0
    return {"kb_count": kb_count, "doc_count": doc_count, "session_count": sess_count, "today_qa_count": today_count}


@router.delete("/conversations/{session_id}", summary="删除会话")
async def delete_conversation(session_id: str):
    """删除指定会话的所有消息"""
    from app.memory.long_term import LongTermMemory
    ltm = LongTermMemory(async_session)
    await ltm.clear_session(session_id)
    return {"detail": "ok"}


@router.get("/conversations/{session_id}/messages", summary="会话历史")
async def get_conversation_messages(session_id: str, limit: int = 50):
    """获取指定会话的历史消息（从 SQLite 加载）"""
    from app.memory.long_term import LongTermMemory

    ltm = LongTermMemory(async_session)
    messages = await ltm.load_messages(session_id, limit=limit)
    return {
        "session_id": session_id,
        "messages": [
            {"role": m.role, "content": m.content, "citations": m.citations, "timestamp": m.timestamp}
            for m in messages
        ],
    }


# ============================================================
# Document List & Delete (Phase 13C)
# ============================================================

def _get_doc_service() -> DocumentService:
    """DocumentService 工厂"""
    return DocumentService(get_retriever(), session_factory=async_session)


@router.get("/documents", response_model=DocumentListResponse, summary="文档列表")
async def list_documents(
    kb_id: int | None = None,
    search: str = "",
    page: int = 1,
    page_size: int = 10,
    service: DocumentService = Depends(_get_doc_service),
):
    """获取文档列表，支持 KB 筛选、名称搜索和分页"""
    result = await service.list_documents(
        kb_id=kb_id, search=search, page=page, page_size=page_size,
    )
    # 批量查 KB 名称
    kb_ids = {doc.kb_id for doc in result["items"]}
    kb_names: dict[int, str] = {}
    if kb_ids:
        from sqlalchemy import select
        from app.models.knowledge_base import KnowledgeBase
        async with async_session() as s:
            kbs = (await s.execute(
                select(KnowledgeBase).where(KnowledgeBase.id.in_(kb_ids))
            )).scalars().all()
            kb_names = {k.id: k.name for k in kbs}

    items = [
        DocumentResponse(
            id=doc.id,
            kb_id=doc.kb_id,
            kb_name=kb_names.get(doc.kb_id, "—"),
            original_filename=doc.original_filename,
            extension=doc.extension,
            size=doc.size,
            chunk_count=doc.chunk_count,
            status=doc.status,
            created_at=doc.created_at.isoformat() if doc.created_at else "",
        )
        for doc in result["items"]
    ]
    return DocumentListResponse(items=items, total=result["total"])


@router.delete("/documents/{doc_id}", summary="删除文档")
async def delete_document(
    doc_id: int,
    service: DocumentService = Depends(_get_doc_service),
):
    """删除文档数据库记录"""
    ok = await service.delete_document(doc_id)
    if not ok:
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(detail="文档不存在", error_type="not_found").model_dump(),
        )
    return {"detail": "ok"}
