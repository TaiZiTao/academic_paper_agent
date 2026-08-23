"""Rebuild persisted pages, hierarchical sections, and chunks for one paper."""

import argparse
import asyncio
from pathlib import Path

from sqlalchemy import delete

from app.config.settings import settings
from app.database import async_session, init_db
from app.models.paper import (
    Paper,
    PaperArtifact,
    PaperChunk,
    PaperPage,
    PaperSection,
    PaperTranslationBlock,
)
from app.paper.chunker import chunk_pages
from app.paper.parser import parse_pdf


async def rebuild(paper_id: int) -> None:
    await init_db()
    async with async_session() as session:
        paper = await session.get(Paper, paper_id)
        if paper is None:
            raise SystemExit(f"paper {paper_id} does not exist")
        source = Path(settings.data_dir) / "papers" / "files" / paper.stored_filename

    parsed = await asyncio.to_thread(parse_pdf, source)
    chunks = chunk_pages(
        parsed.pages,
        paper_id=paper_id,
        chunk_size=settings.paper_chunk_size,
        overlap=settings.paper_chunk_overlap,
        sections=parsed.sections,
    )

    async with async_session() as session:
        await session.execute(
            delete(PaperArtifact).where(
                PaperArtifact.paper_id == paper_id,
                PaperArtifact.artifact_type == "translation",
            )
        )
        for model in (PaperTranslationBlock, PaperChunk, PaperSection, PaperPage):
            await session.execute(delete(model).where(model.paper_id == paper_id))
        session.add_all(
            PaperPage(paper_id=paper_id, page_number=page.page_number, text=page.text)
            for page in parsed.pages
        )
        session.add_all(
            PaperSection(
                paper_id=paper_id,
                title=section.title,
                normalized_title=section.normalized_title,
                level=section.level,
                ordinal=section.ordinal,
                page_start=section.page_start,
                page_end=section.page_end,
                summary=section.summary,
            )
            for section in parsed.sections
        )
        session.add_all(
            PaperChunk(
                paper_id=paper_id,
                chunk_id=chunk.chunk_id,
                section=chunk.section,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                ordinal=chunk.ordinal,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                content=chunk.content,
                metadata_json="{}",
            )
            for chunk in chunks
        )
        stored = await session.get(Paper, paper_id)
        stored.page_count = parsed.page_count
        await session.commit()

    print(f"paper={paper_id} sections={len(parsed.sections)} chunks={len(chunks)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("paper_id", type=int)
    args = parser.parse_args()
    asyncio.run(rebuild(args.paper_id))
