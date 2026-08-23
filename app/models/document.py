"""
Document ORM Model

映射到 documents 表。
Phase 13C：文档管理 CRUD 基础设施。
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.database.base import Base


class Document(Base):
    """文档持久化模型"""

    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kb_id = Column(Integer, nullable=False, default=0, comment="所属知识库 ID")
    filename = Column(String(256), nullable=False, comment="存储文件名")
    original_filename = Column(String(256), nullable=False, comment="原始文件名")
    extension = Column(String(16), nullable=False, comment="文件扩展名")
    size = Column(Integer, nullable=False, default=0, comment="文件大小（字节）")
    chunk_count = Column(Integer, nullable=False, default=0, comment="切分片段数")
    status = Column(String(32), nullable=False, default="completed", comment="状态")
    chunk_ids = Column(Text, nullable=True, comment="JSON array of chunk_ids for index removal")
    created_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return f"<Document(id={self.id}, filename={self.original_filename!r})>"
