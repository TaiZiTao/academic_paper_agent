"""论文上传、处理、持久化与清理服务测试。"""

import importlib
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.base import Base
from app.models.paper import Paper, PaperArtifact, PaperChunk, PaperPage, PaperSection
from app.paper.parser import UnsupportedScanError
from app.paper.schemas import PaperMetadata, PaperPage as PageData, PaperSectionData, ParsedPaper


class FakeRetriever:
    def __init__(self):
        self.built = {}
        self.deleted = []

    async def build(self, paper_id, chunks):
        self.built[paper_id] = chunks

    def delete(self, paper_id):
        self.deleted.append(paper_id)


class FakeGraph:
    async def ainvoke(self, state, _config):
        chunk = state["chunks"][0]
        citation = {
            "paper_id": state["paper_id"],
            "paper_title": state["paper_title"],
            "page": chunk["page_start"],
            "section": chunk["section"],
            "chunk_id": chunk["chunk_id"],
            "quote": chunk["content"][:20],
            "verified": True,
            "reason": "",
        }
        report = {
            "background": "背景",
            "research_question": "问题",
            "method": "方法",
            "experiments": "实验",
            "results": "结果",
            "innovations": "创新",
            "limitations": "局限",
            "future_questions": "问题",
            "terms": [],
        }
        return {
            **state,
            "report": report,
            "citations": [citation],
            "artifact": {"type": "report", "title": "精读报告", "content": report, "citations": [citation]},
        }


def parsed_paper():
    return ParsedPaper(
        pages=[
            PageData(page_number=1, text="Abstract\n" + "paper evidence " * 20),
            PageData(page_number=2, text="Methods\n" + "method evidence " * 20),
        ],
        sections=[
            PaperSectionData(title="Abstract", normalized_title="abstract", ordinal=0, page_start=1, page_end=1),
            PaperSectionData(title="Methods", normalized_title="method", ordinal=1, page_start=2, page_end=2),
        ],
        metadata=PaperMetadata(title="Test Paper", authors=["Alice"], abstract="paper evidence"),
        language="en",
        page_count=2,
        publication_year=2024,
    )


async def service_fixture(tmp_path, parser_fn=None):
    try:
        PaperService = importlib.import_module("app.paper.service").PaperService
    except ModuleNotFoundError:
        pytest.fail("app.paper.service 尚未实现")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'service.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    retriever = FakeRetriever()
    service = PaperService(
        session_factory=session_factory,
        retriever=retriever,
        llm=object(),
        files_dir=tmp_path / "files",
        graph=FakeGraph(),
        parser_fn=parser_fn or (lambda _path: parsed_paper()),
        chunk_size=120,
        chunk_overlap=20,
    )
    return service, session_factory, retriever, engine


@pytest.mark.asyncio
async def test_process_paper_persists_pages_sections_chunks_report_and_ready_status(tmp_path):
    service, session_factory, retriever, engine = await service_fixture(tmp_path)
    paper = await service.create_paper("sample.pdf", b"%PDF fake")

    await service.process_paper(paper.id)

    async with session_factory() as session:
        stored = await session.get(Paper, paper.id)
        assert stored.status == "ready"
        assert stored.title == "Test Paper"
        assert stored.page_count == 2
        assert stored.publication_year == 2024
        assert (await session.scalar(select(func.count()).select_from(PaperPage))) == 2
        assert (await session.scalar(select(func.count()).select_from(PaperSection))) == 2
        assert (await session.scalar(select(func.count()).select_from(PaperChunk))) >= 2
        artifact = (await session.execute(select(PaperArtifact))).scalar_one()
        assert artifact.artifact_type == "report"
        assert "方法" in artifact.content_text
    assert paper.id in retriever.built
    await engine.dispose()


@pytest.mark.asyncio
async def test_scan_failure_sets_explicit_failed_state_and_keeps_source_file(tmp_path):
    def reject(_path):
        raise UnsupportedScanError("scan")

    service, session_factory, _, engine = await service_fixture(tmp_path, parser_fn=reject)
    paper = await service.create_paper("scan.pdf", b"%PDF scan")

    await service.process_paper(paper.id)

    async with session_factory() as session:
        stored = await session.get(Paper, paper.id)
        assert stored.status == "failed"
        assert stored.error_code == "unsupported_scan"
        assert service.file_path(stored).exists()
    await engine.dispose()


@pytest.mark.asyncio
async def test_delete_paper_cleans_database_file_and_index(tmp_path):
    service, session_factory, retriever, engine = await service_fixture(tmp_path)
    paper = await service.create_paper("delete.pdf", b"%PDF fake")
    await service.process_paper(paper.id)
    source = service.file_path(paper)

    deleted = await service.delete_paper(paper.id)

    assert deleted is True
    assert not source.exists()
    assert retriever.deleted == [paper.id]
    async with session_factory() as session:
        assert await session.get(Paper, paper.id) is None
        assert (await session.scalar(select(func.count()).select_from(PaperChunk))) == 0
        assert (await session.scalar(select(func.count()).select_from(PaperArtifact))) == 0
    await engine.dispose()


def test_translation_page_cleanup_filters_mid_page_figure_content():
    PaperService = importlib.import_module("app.paper.service").PaperService
    source = """The method first builds global prompts from downscaled features.
Encoder
Conv Conv Conv
(a) PromptSR architecture
Fig. 2: Overall architecture of the proposed network.
The prompted features are then passed to the decoder."""

    cleaned = PaperService._clean_translation_page_prefix(source, starts_at_page_top=False)

    assert "first builds global prompts" in cleaned
    assert "passed to the decoder" in cleaned
    assert "Encoder" not in cleaned
    assert "Fig. 2" not in cleaned
