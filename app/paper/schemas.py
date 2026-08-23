"""论文助手领域对象与 API 共享结构。"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class PaperPage(BaseModel):
    page_number: int = Field(ge=1)
    text: str


class PaperMetadata(BaseModel):
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    abstract: str = ""
    keywords: list[str] = Field(default_factory=list)


class PaperSectionData(BaseModel):
    title: str
    normalized_title: str = "other"
    level: int = 1
    ordinal: int = 0
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    summary: str = ""


class ParsedPaper(BaseModel):
    pages: list[PaperPage]
    sections: list[PaperSectionData] = Field(default_factory=list)
    metadata: PaperMetadata = Field(default_factory=PaperMetadata)
    language: Literal["zh", "en", "mixed", "unknown"] = "unknown"
    page_count: int = 0
    publication_year: int | None = None


class PaperChunkData(BaseModel):
    paper_id: int
    chunk_id: str
    section: str = ""
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    ordinal: int = 0
    char_start: int = 0
    char_end: int = 0
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class PaperCitation(BaseModel):
    paper_id: int
    paper_title: str = ""
    page: int | None = None
    section: str = ""
    chunk_id: str
    quote: str
    verified: bool = False
    reason: str = ""


class PaperSearchResult(BaseModel):
    chunk: PaperChunkData
    score: float


class PaperProgressEvent(BaseModel):
    event: str
    stage: str = ""
    status: str = ""
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class PaperTaskRequest(BaseModel):
    task_type: Literal["qa", "translation", "presentation", "review"]
    input_text: str = Field(min_length=1, max_length=20000)
    session_id: str = Field(default="", max_length=128)
    section: str | None = Field(default=None, max_length=512)
