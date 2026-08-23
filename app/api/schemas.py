"""
API HTTP 数据模型（DTO）

与 Service 层的 QAResponse 分离：
- schemas.py → HTTP DTO，面向客户端，随 API 版本变化
- qa_service.py → 业务对象，面向内部，随业务扩展变化
"""

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    """POST /api/v1/chat 请求体"""

    session_id: str = Field(
        ..., min_length=1, description="会话标识，客户端生成 UUID"
    )
    query: str = Field(
        ..., min_length=1, max_length=5000, description="用户问题"
    )
    kb_id: int = Field(default=0, description="知识库 ID，0=全局检索")


class AskResponse(BaseModel):
    """POST /api/v1/chat 响应体"""

    session_id: str
    answer: str
    citations: list[str] = Field(default_factory=list)
    intent: str = ""


class ErrorResponse(BaseModel):
    """通用错误响应"""

    detail: str
    error_type: str = "app_error"


# ============================================================
# KnowledgeBase CRUD (Phase 13)
# ============================================================

class KBCreateRequest(BaseModel):
    """POST /api/v1/kb 请求体"""
    name: str = Field(..., min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)


class KBUpdateRequest(BaseModel):
    """PUT /api/v1/kb/{id} 请求体（部分更新）"""
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)


class KBResponse(BaseModel):
    """知识库响应体"""
    id: int
    name: str
    description: str
    created_at: str
    updated_at: str


class KBListResponse(BaseModel):
    """知识库列表响应体"""
    items: list[KBResponse]
    total: int


# ============================================================
# Document Upload (Phase 13B)
# ============================================================

class DocumentUploadResponse(BaseModel):
    """POST /api/v1/documents/upload 响应体"""
    id: int = 0
    filename: str
    chunks_count: int
    kb_id: int


# ============================================================
# Document List (Phase 13C)
# ============================================================

class DocumentResponse(BaseModel):
    """文档列表项"""
    id: int
    kb_id: int
    kb_name: str = ""
    original_filename: str
    extension: str
    size: int
    chunk_count: int
    status: str
    created_at: str


class DocumentListResponse(BaseModel):
    """文档列表响应"""
    items: list[DocumentResponse]
    total: int
