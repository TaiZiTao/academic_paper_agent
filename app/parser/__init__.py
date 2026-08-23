"""
文档解析模块

提供文档加载、清洗、切分的完整流程。

流程：
    load_file(path) → Document → clean_text() → chunk_document() → List[DocumentChunk]
"""

from app.parser.chunker import chunk_document, chunk_text
from app.parser.cleaner import clean_text
from app.parser.loader import load_file, load_markdown, load_text
from app.parser.models import Document, DocumentChunk


async def parse_file(
    file_path: str,
    chunk_size: int = 500,
    overlap: int = 50,
) -> list[DocumentChunk]:
    """解析文件完整流程：加载 → 清洗 → 切分。PDF 异步解析不阻塞事件循环。"""
    doc = await load_file(file_path)
    doc.content = clean_text(doc.content)
    return chunk_document(doc, chunk_size=chunk_size, overlap=overlap)


__all__ = [
    "Document",
    "DocumentChunk",
    "load_file",
    "load_text",
    "load_markdown",
    "clean_text",
    "chunk_text",
    "chunk_document",
    "parse_file",
]
