"""单篇精读真实 PDF 纵向闭环测试。"""

import json

import pytest
from reportlab.pdfgen import canvas
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.base import Base
from app.models.paper import Paper, PaperArtifact, PaperChunk, PaperPage
from app.paper.retriever import PaperRetriever
from app.paper.service import PaperService
from app.rag.embedding import BaseEmbedding


class DeterministicEmbedding(BaseEmbedding):
    @property
    def dimension(self):
        return 8

    async def embed_text(self, text):
        return (await self.embed_documents([text]))[0]

    async def embed_documents(self, texts):
        return [
            [
                float(text.lower().count("hybrid")),
                float(text.lower().count("method")),
                float(text.lower().count("experiment")),
                float(text.lower().count("accuracy")),
                1.0,
                0.5,
                0.25,
                0.125,
            ]
            for text in texts
        ]


class GroundedLLM:
    def bind(self, **_kwargs):
        return self

    async def ainvoke(self, prompt):
        citation = {
            "paper_id": 1,
            "paper_title": "Hybrid Retrieval Study",
            "page": 2,
            "section": "2 Methods",
            "chunk_id": "paper-1-chunk-1",
            "quote": "We propose a hybrid retrieval method.",
        }
        if "输出结构" in prompt:
            payload = {
                "report": {
                    "background": "研究检索增强方法。",
                    "research_question": "如何融合稠密与稀疏检索？",
                    "method": "提出混合检索方法（hybrid retrieval）。",
                    "experiments": "在测试集上比较准确率。",
                    "results": "实验观察到准确率提升。",
                    "innovations": "融合两类检索信号。",
                    "limitations": "数据集规模有限。",
                    "future_questions": "需要验证跨领域泛化。",
                    "terms": [{"en": "hybrid retrieval", "zh": "混合检索"}],
                },
                "citations": [citation],
            }
        else:
            payload = {"content": "论文提出了混合检索方法。", "citations": [citation]}
        return type("Response", (), {"content": json.dumps(payload, ensure_ascii=False)})()


def make_pdf(path):
    pdf = canvas.Canvas(str(path))
    pdf.setTitle("Hybrid Retrieval Study")
    pdf.setAuthor("Alice; Bob")
    pages = [
        ["Abstract", "This paper studies grounded retrieval for scientific reading.", "The goal is reliable evidence."],
        ["2 Methods", "We propose a hybrid retrieval method.", "It combines dense and sparse signals."],
        ["3 Experiments", "Experiments show an accuracy improvement of five percent.", "The test set contains 100 samples."],
    ]
    for lines in pages:
        y = 780
        for line in lines:
            pdf.drawString(72, y, line)
            y -= 24
        pdf.showPage()
    pdf.save()


@pytest.mark.asyncio
async def test_real_pdf_upload_report_qa_citations_and_cleanup(tmp_path):
    db_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'e2e.db'}")
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with db_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    source = tmp_path / "source.pdf"
    make_pdf(source)
    retriever = PaperRetriever(DeterministicEmbedding(), tmp_path / "index")
    service = PaperService(
        session_factory=session_factory,
        retriever=retriever,
        llm=GroundedLLM(),
        files_dir=tmp_path / "files",
        chunk_size=1000,
        chunk_overlap=100,
    )

    paper = await service.create_paper("study.pdf", source.read_bytes())
    await service.process_paper(paper.id)

    async with session_factory() as session:
        stored = await session.get(Paper, paper.id)
        assert stored.status == "ready"
        assert stored.title == "Hybrid Retrieval Study"
        assert stored.page_count == 3
        assert await session.scalar(select(func.count()).select_from(PaperPage)) == 3
        assert await session.scalar(select(func.count()).select_from(PaperChunk)) == 3
        report = (await session.execute(select(PaperArtifact).where(PaperArtifact.artifact_type == "report"))).scalar_one()
        assert "解决方案与创新点" in report.content_text

    events = [event async for event in service.run_task(paper.id, "qa", "论文的方法是什么？", "e2e-session")]
    done = next(event for event in events if event["event"] == "done")
    assert done["citations"][0]["verified"] is True
    assert done["citations"][0]["page"] == 2
    stored_file = service.file_path(await service.get_paper(paper.id))
    assert stored_file.exists()
    assert await service.delete_paper(paper.id) is True
    assert not stored_file.exists()
    assert not (tmp_path / "index" / str(paper.id)).exists()
    await db_engine.dispose()
