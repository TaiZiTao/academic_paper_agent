"""
ConversationMessage ORM Model

首个 SQLAlchemy Base 子类，映射到 conversation_messages 表。
导入此模块即自动注册到 Base.metadata。
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Index, Integer, String, Text

from app.database.base import Base


class ConversationMessage(Base):
    """
    对话消息持久化模型。

    按 session_id 分区，按时间排序，支持按 session 加载历史。
    """

    __tablename__ = "conversation_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), nullable=False, index=True)
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    citations = Column(Text, nullable=True)  # JSON array of chunk_ids
    created_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_session_id_created", "session_id", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<ConversationMessage(id={self.id}, "
            f"session_id={self.session_id!r}, "
            f"role={self.role!r})>"
        )
