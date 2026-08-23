"""论文助手领域的 SQLAlchemy 持久化模型。"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.database.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Paper(Base):
    __tablename__ = "papers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    original_filename = Column(String(256), nullable=False)
    stored_filename = Column(String(256), nullable=False)
    title = Column(String(512), nullable=False, default="")
    authors_json = Column(Text, nullable=False, default="[]")
    abstract = Column(Text, nullable=False, default="")
    keywords_json = Column(Text, nullable=False, default="[]")
    language = Column(String(16), nullable=False, default="unknown")
    page_count = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False, default="uploaded", index=True)
    # 研究方向(自由文本, 由 LLM 自动分类, 可手动修改; 空=未分类)
    research_field = Column(String(128), nullable=False, default="", index=True)
    # 发表年份(元数据/首页文本提取, 可空; 全库问答过滤用)
    publication_year = Column(Integer, nullable=True, index=True)
    error_code = Column(String(64), nullable=False, default="")
    error_message = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    pages = relationship("PaperPage", cascade="all, delete-orphan", passive_deletes=True)
    sections = relationship("PaperSection", cascade="all, delete-orphan", passive_deletes=True)
    chunks = relationship("PaperChunk", cascade="all, delete-orphan", passive_deletes=True)
    tasks = relationship("PaperTask", cascade="all, delete-orphan", passive_deletes=True)
    artifacts = relationship("PaperArtifact", cascade="all, delete-orphan", passive_deletes=True)
    messages = relationship("PaperMessage", cascade="all, delete-orphan", passive_deletes=True)
    translation_blocks = relationship(
        "PaperTranslationBlock",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class PaperPage(Base):
    __tablename__ = "paper_pages"
    __table_args__ = (UniqueConstraint("paper_id", "page_number", name="uq_paper_page"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    paper_id = Column(Integer, ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True)
    page_number = Column(Integer, nullable=False)
    text = Column(Text, nullable=False, default="")


class PaperSection(Base):
    __tablename__ = "paper_sections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    paper_id = Column(Integer, ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(512), nullable=False)
    normalized_title = Column(String(128), nullable=False, default="other")
    level = Column(Integer, nullable=False, default=1)
    ordinal = Column(Integer, nullable=False, default=0)
    page_start = Column(Integer, nullable=False)
    page_end = Column(Integer, nullable=False)
    summary = Column(Text, nullable=False, default="")


class PaperChunk(Base):
    __tablename__ = "paper_chunks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    paper_id = Column(Integer, ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True)
    chunk_id = Column(String(96), nullable=False, unique=True, index=True)
    section = Column(String(512), nullable=False, default="")
    page_start = Column(Integer, nullable=False)
    page_end = Column(Integer, nullable=False)
    ordinal = Column(Integer, nullable=False, default=0)
    char_start = Column(Integer, nullable=False, default=0)
    char_end = Column(Integer, nullable=False, default=0)
    content = Column(Text, nullable=False)
    metadata_json = Column(Text, nullable=False, default="{}")


class PaperTask(Base):
    __tablename__ = "paper_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    paper_id = Column(Integer, ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True)
    task_type = Column(String(32), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="pending", index=True)
    session_id = Column(String(128), nullable=False, default="", index=True)
    input_json = Column(Text, nullable=False, default="{}")
    error_code = Column(String(64), nullable=False, default="")
    error_message = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class PaperArtifact(Base):
    __tablename__ = "paper_artifacts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    paper_id = Column(Integer, ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id = Column(Integer, ForeignKey("paper_tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    artifact_type = Column(String(32), nullable=False, index=True)
    title = Column(String(512), nullable=False, default="")
    content_json = Column(Text, nullable=False, default="{}")
    content_text = Column(Text, nullable=False, default="")
    citations_json = Column(Text, nullable=False, default="[]")
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class PaperTranslationBlock(Base):
    __tablename__ = "paper_translation_blocks"
    __table_args__ = (
        UniqueConstraint(
            "paper_id",
            "section",
            "block_index",
            name="uq_paper_translation_block",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    paper_id = Column(Integer, ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id = Column(Integer, ForeignKey("paper_tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    section = Column(String(512), nullable=False, index=True)
    block_index = Column(Integer, nullable=False)
    page_start = Column(Integer, nullable=False)
    page_end = Column(Integer, nullable=False)
    source_text = Column(Text, nullable=False, default="")
    translated_text = Column(Text, nullable=False, default="")
    status = Column(String(32), nullable=False, default="pending", index=True)
    error_message = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class PaperFigure(Base):
    __tablename__ = "paper_figures"

    id = Column(Integer, primary_key=True, autoincrement=True)
    paper_id = Column(Integer, ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True)
    page = Column(Integer, nullable=False, index=True)
    kind = Column(String(16), nullable=False, default="figure")
    ordinal = Column(Integer, nullable=False, default=0)
    caption = Column(Text, nullable=False, default="")
    caption_translated = Column(Text, nullable=False, default="")
    bbox = Column(String(128), nullable=False, default="0,0,0,0")
    image_path = Column(String(512), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=utcnow)


class PaperMessage(Base):
    __tablename__ = "paper_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    paper_id = Column(Integer, ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id = Column(String(128), nullable=False, index=True)
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    citations_json = Column(Text, nullable=False, default="[]")
    suggestions_json = Column(Text, nullable=False, default="[]")
    created_at = Column(DateTime, nullable=False, default=utcnow, index=True)
