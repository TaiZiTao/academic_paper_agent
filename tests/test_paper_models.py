"""论文助手持久化模型测试。"""

import importlib

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.base import Base


def _paper_models():
    try:
        module = importlib.import_module("app.models.paper")
    except ModuleNotFoundError:
        pytest.fail("app.models.paper 尚未实现")
    return module


def test_paper_tables_are_registered():
    _paper_models()
    expected = {
        "papers",
        "paper_pages",
        "paper_sections",
        "paper_chunks",
        "paper_tasks",
        "paper_artifacts",
        "paper_messages",
    }
    assert expected.issubset(Base.metadata.tables)


@pytest.mark.asyncio
async def test_paper_domain_can_be_persisted(tmp_path):
    models = _paper_models()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'paper.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        paper = models.Paper(
            original_filename="sample.pdf",
            stored_filename="stored.pdf",
            status="parsing",
        )
        session.add(paper)
        await session.flush()
        session.add_all(
            [
                models.PaperPage(
                    paper_id=paper.id,
                    page_number=1,
                    text="Abstract text",
                ),
                models.PaperSection(
                    paper_id=paper.id,
                    title="Methods",
                    ordinal=1,
                    page_start=2,
                    page_end=4,
                ),
                models.PaperChunk(
                    paper_id=paper.id,
                    chunk_id="paper-1-chunk-1",
                    section="Methods",
                    page_start=2,
                    page_end=2,
                    ordinal=0,
                    content="method evidence",
                ),
                models.PaperTask(
                    paper_id=paper.id,
                    task_type="report",
                    status="pending",
                    input_json="{}",
                ),
                models.PaperArtifact(
                    paper_id=paper.id,
                    artifact_type="report",
                    title="精读报告",
                    content_json="{}",
                    content_text="报告",
                    citations_json="[]",
                ),
                models.PaperMessage(
                    paper_id=paper.id,
                    session_id="session-1",
                    role="user",
                    content="这篇论文的方法是什么？",
                ),
            ]
        )
        await session.commit()

    async with session_factory() as session:
        stored = (await session.execute(select(models.Paper))).scalar_one()
        assert stored.status == "parsing"
        assert stored.original_filename == "sample.pdf"

    await engine.dispose()


def test_paper_publication_year_column():
    from app.models.paper import Paper

    cols = {c.name for c in Paper.__table__.columns}
    assert "publication_year" in cols

