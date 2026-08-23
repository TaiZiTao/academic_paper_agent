"""
KnowledgeBase ORM Model

映射到 knowledge_bases 表。
Phase 13：知识库 CRUD 基础设施。
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.database.base import Base


class KnowledgeBase(Base):
    """知识库持久化模型"""

    __tablename__ = "knowledge_bases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, default="")
    embedding_vector = Column(Text, nullable=True)  # JSON array: KB 描述向量
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
        return f"<KnowledgeBase(id={self.id}, name={self.name!r})>"
