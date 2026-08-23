"""
文本切分器

将文本按固定大小滑动窗口切分为 DocumentChunk 列表。
支持从 [Page N] 标记中提取页码信息。
"""

import re

from app.parser.models import Document, DocumentChunk

# 匹配 [Page 3] 或 [Page 3 end] 标记
_PAGE_MARKER = re.compile(r"\[Page (\d+)\]")


def _detect_page(text: str, start: int) -> int | None:
    """从文本的 start 位置往前找最近的 [Page N] 标记"""
    search_region = text[max(0, start - 200):start]
    matches = list(_PAGE_MARKER.finditer(search_region))
    if matches:
        return int(matches[-1].group(1))
    return None


def chunk_text(
    text: str,
    chunk_size: int = 500,
    overlap: int = 50,
) -> list[DocumentChunk]:
    """
    按固定大小切分文本，自动检测 [Page N] 标记。

    Returns
    -------
    list[DocumentChunk]
        切分后的片段列表，metadata 含 page_number（如有）
    """
    if chunk_size <= overlap:
        raise ValueError(
            f"chunk_size ({chunk_size}) must be greater than overlap ({overlap})"
        )

    if not text:
        return []

    stride = chunk_size - overlap
    chunks: list[DocumentChunk] = []

    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk_content = text[start:end]

        # 去掉 chunk 内容中的 [Page N] 和 [Page N end] 标记（纯标记，非正文）
        cleaned = re.sub(r"\[Page \d+( end)?\]", "", chunk_content).strip()

        # 检测页码
        page = _detect_page(text, start)

        chunks.append(DocumentChunk(
            document_id="",
            content=cleaned if cleaned else chunk_content,
            chunk_index=len(chunks),
            metadata={
                "chunk_size": chunk_size,
                "overlap": overlap,
                "start_char": start,
                "end_char": end,
                "page_number": page,  # Phase: Step1 页码映射
            },
        ))

        if end >= len(text):
            break

        start += stride

    return chunks


def chunk_document(
    document: Document,
    chunk_size: int = 500,
    overlap: int = 50,
) -> list[DocumentChunk]:
    """
    对 Document 对象进行切分，自动关联 document_id + 页码。
    """
    chunks = chunk_text(document.content, chunk_size=chunk_size, overlap=overlap)
    for chunk in chunks:
        chunk.document_id = document.document_id
    return chunks
