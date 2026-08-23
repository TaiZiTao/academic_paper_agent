"""结构化引用校验：模型不能自行决定可信页码。"""

import re
import unicodedata

from pydantic import BaseModel

from app.paper.schemas import PaperChunkData, PaperCitation


class CitationValidationResult(BaseModel):
    valid: bool
    reason: str = ""
    citation: PaperCitation


def _normalize_for_match(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", normalized)


class CitationValidator:
    def __init__(self, chunks: list[PaperChunkData]) -> None:
        self._chunks = {chunk.chunk_id: chunk for chunk in chunks}

    @staticmethod
    def _invalid(citation: PaperCitation, reason: str) -> CitationValidationResult:
        safe = citation.model_copy(update={"page": None, "verified": False, "reason": reason})
        return CitationValidationResult(valid=False, reason=reason, citation=safe)

    def validate(self, citation: PaperCitation, paper_id: int) -> CitationValidationResult:
        if citation.paper_id != paper_id:
            return self._invalid(citation, "foreign_paper")

        chunk = self._chunks.get(citation.chunk_id)
        if chunk is None:
            return self._invalid(citation, "missing_chunk")
        if chunk.paper_id != paper_id:
            return self._invalid(citation, "foreign_chunk")

        quote = _normalize_for_match(citation.quote)
        content = _normalize_for_match(chunk.content)
        if not quote or quote not in content:
            return self._invalid(citation, "quote_not_found")

        # 引用原文在证据块中真实存在即视为证据成立;
        # 页码缺失/越界时以证据块的实际页码兜底(不是编造, 而是以真实块为准)
        if citation.page is None or not (chunk.page_start <= citation.page <= chunk.page_end):
            corrected = citation.model_copy(
                update={
                    "verified": True,
                    "page": chunk.page_start,
                    "reason": "page_corrected" if citation.page is not None else "page_inferred",
                }
            )
            return CitationValidationResult(valid=True, citation=corrected)

        verified = citation.model_copy(update={"verified": True, "reason": ""})
        return CitationValidationResult(valid=True, citation=verified)

    def validate_many(
        self,
        citations: list[PaperCitation],
        paper_id: int,
    ) -> list[CitationValidationResult]:
        return [self.validate(citation, paper_id) for citation in citations]
