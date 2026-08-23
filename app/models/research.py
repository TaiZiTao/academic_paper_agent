"""文献检索下载 Agent 的 SQLAlchemy 持久化模型。"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.database.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PaperImport(Base):
    """一次「搜索到下载入库」任务。"""

    __tablename__ = "paper_imports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(512), nullable=False, default="")
    year = Column(Integer, nullable=True)  # 发表年份(前端从 SearchResult.year 带入, L1.5 free_pdf 按年定位会议/卷页用)
    venue = Column(String(256), nullable=False, default="")  # 发表 venue(前端带入, L1.5 free_pdf 按 venue 路由免费源)
    source = Column(String(32), nullable=False, default="")  # arxiv | semantic_scholar
    external_id = Column(String(128), nullable=True)  # arxiv_id / S2 paperId
    doi = Column(String(256), nullable=True)
    pdf_url = Column(String(1024), nullable=True)
    page_url = Column(String(1024), nullable=True)
    status = Column(String(32), nullable=False, default="pending", index=True)
    progress = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=False, default="")
    paper_id = Column(Integer, ForeignKey("papers.id", ondelete="SET NULL"), nullable=True, index=True)  # 入库成功后关联 papers.id
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
