"""文献检索下载 Agent 的领域对象与 API 共享结构。"""

from typing import Literal

from pydantic import BaseModel, Field


class SearchResult(BaseModel):
    """单个搜索结果(多源统一结构)。"""

    source: Literal["arxiv", "semantic_scholar", "openalex"]
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str = ""
    abstract: str = ""
    doi: str | None = None
    pdf_url: str | None = None
    page_url: str
    citations: int = 0
    published: bool = False  # 是否已发表(venue 非空)
    ccf_level: str | None = None  # "A"|"B"|"C"|None(CCF 推荐目录子集命中)
    oa_status: Literal["open", "closed", "unknown"] = "unknown"  # 开放获取状态(前端据此显示直链/付费墙)
    openalex_id: str | None = None  # OpenAlex Work ID(如 "W2741809807"), 用于双源去重合并


class ImportItem(BaseModel):
    """前端勾选后提交的下载入库项。"""

    source: Literal["arxiv", "semantic_scholar", "openalex"] = "arxiv"
    title: str = Field(min_length=1)
    year: int | None = None  # 发表年份(前端从 SearchResult.year 带入, L1.5 定位会议/卷页用)
    venue: str = ""  # 发表 venue(前端从 SearchResult.venue 带入, L1.5 free_pdf 按 venue 路由免费源)
    doi: str | None = None
    pdf_url: str | None = None
    page_url: str | None = None
    external_id: str | None = None


class ImportTaskOut(BaseModel):
    """paper_imports 行对外结构。"""

    id: int
    title: str
    source: str
    status: str
    progress: int
    error_message: str = ""
    paper_id: int | None = None
    created_at: str = ""
    updated_at: str = ""


class BrowserStatus(BaseModel):
    """VPN 浏览器会话状态。"""

    status: Literal["none", "alive", "expired"]
    message: str = ""


class SearchPlan(BaseModel):
    """PlanQuery 节点 LLM 输出。"""

    queries: list[str] = Field(min_length=1)
    sources: list[Literal["arxiv", "semantic_scholar", "openalex"]] = Field(default_factory=list)


class RankedResult(BaseModel):
    """RelevanceRank 节点 LLM 输出的单个排序项。"""

    index: int = Field(ge=0)
    score: int
