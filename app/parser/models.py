"""
Parser 数据对象

定义文档解析流程中的内存数据模型（Pydantic Schema）。
这些对象是瞬态的，不涉及数据库持久化。

与 app/models/ 的区别：
- app/models/         → SQLAlchemy ORM Model（数据库表映射）
- app/parser/models.py → Pydantic Schema（解析流程数据对象）
"""

import uuid

from pydantic import BaseModel, Field


def _new_id() -> str:
    """生成唯一 ID"""
    return uuid.uuid4().hex[:12]


class Document(BaseModel):
    """
    文件解析后的文档对象。

    由 Loader 创建，包含原始文件内容和元信息。
    """

    document_id: str = Field(default_factory=_new_id)
    filename: str
    content: str
    metadata: dict = Field(default_factory=dict)


class DocumentChunk(BaseModel):
    """
    文本切分后的片段对象。

    由 Chunker 创建，每个 Chunk 关联到来源 Document。
    """

    chunk_id: str = Field(default_factory=_new_id)
    document_id: str
    content: str
    chunk_index: int
    metadata: dict = Field(default_factory=dict)
