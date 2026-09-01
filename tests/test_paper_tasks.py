"""论文问答、翻译、笔记和汇报任务测试。"""

import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.base import Base
from app.models.paper import (
    PaperArtifact,
    PaperChunk,
    PaperMessage,
    PaperPage as StoredPaperPage,
    PaperTask,
    PaperTranslationBlock,
)
from app.paper.schemas import PaperMetadata, PaperPage, PaperSearchResult, PaperSectionData, ParsedPaper
from app.paper.service import PaperService


class TaskLLM:
    async def ainvoke(self, _prompt):
        payload = {
            "content": "这是基于论文证据生成的结果。",
            "citations": [
                {
                    "paper_id": 1,
                    "paper_title": "Task Paper",
                    "page": 1,
                    "section": "Methods",
                    "chunk_id": "paper-1-chunk-0",
                    "quote": "method evidence",
                }
            ],
        }
        return type("Response", (), {"content": json.dumps(payload, ensure_ascii=False)})()


class ForeignContextTaskLLM(TaskLLM):
    async def ainvoke(self, prompt):
        response = await super().ainvoke(prompt)
        payload = json.loads(response.content)
        payload["citations"][0]["paper_id"] = 999
        payload["citations"][0]["paper_title"] = "Foreign Paper"
        return type("Response", (), {"content": json.dumps(payload, ensure_ascii=False)})()


class TaskRetriever:
    def __init__(self):
        self.chunks = {}
        self.search_calls = 0

    async def build(self, paper_id, chunks):
        self.chunks[paper_id] = chunks

    async def search(self, paper_id, _query, k=8, section=None):
        self.search_calls += 1
        chunks = self.chunks.get(paper_id, [])
        if section:
            chunks = [chunk for chunk in chunks if chunk.section == section]
        return [PaperSearchResult(chunk=chunk, score=1.0) for chunk in chunks[:k]]

    def delete(self, _paper_id):
        return None


class ReportGraph:
    async def ainvoke(self, state, _config):
        return {
            **state,
            "report": {"method": "method evidence"},
            "citations": [],
            "artifact": {"title": "精读报告"},
        }


class TranslationLLM:
    def __init__(self):
        self.sources = []

    async def ainvoke(self, prompt):
        source = prompt.split("原文：\n", 1)[1]
        self.sources.append(source)
        return type("Response", (), {"content": f"中文译文-{len(self.sources)}"})()


class FailOnSecondTranslationLLM(TranslationLLM):
    async def ainvoke(self, prompt):
        source = prompt.split("原文：\n", 1)[1]
        self.sources.append(source)
        if len(self.sources) == 2:
            raise ConnectionError("temporary translation failure")
        return type("Response", (), {"content": f"中文译文-{len(self.sources)}"})()


def parsed_paper():
    return ParsedPaper(
        pages=[PaperPage(page_number=1, text="Methods\n" + "method evidence " * 60)],
        sections=[
            PaperSectionData(title="Methods", normalized_title="method", page_start=1, page_end=1)
        ],
        metadata=PaperMetadata(title="Task Paper"),
        language="en",
        page_count=1,
    )


async def make_service(tmp_path, parsed_document=None):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'tasks.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    service = PaperService(
        session_factory=session_factory,
        retriever=TaskRetriever(),
        llm=TaskLLM(),
        files_dir=tmp_path / "files",
        graph=ReportGraph(),
        parser_fn=lambda _path: parsed_document or parsed_paper(),
        chunk_size=500,
        chunk_overlap=50,
    )
    paper = await service.create_paper("task.pdf", b"%PDF fake")
    await service.process_paper(paper.id)
    return service, paper, session_factory, engine


def cross_page_introduction(first_page_body: str = "In this paper, we propose a novel prompting-empowered"):
    return ParsedPaper(
        pages=[
            PaperPage(
                page_number=1,
                text=f"I. INTRODUCTION\n{first_page_body}",
            ),
            PaperPage(
                page_number=2,
                text=(
                    "2\n"
                    "Sliding Window Matching Window\n"
                    "diagram labels\n"
                    "Fig. 1: Model overview spanning the top of the page.\n"
                    "lightweight image SR method that restores details.\n"
                    "II. RELATED WORK\nrelated work body"
                ),
            ),
        ],
        sections=[
            PaperSectionData(
                title="I. INTRODUCTION",
                normalized_title="introduction",
                ordinal=0,
                page_start=1,
                page_end=2,
            ),
            PaperSectionData(
                title="II. RELATED WORK",
                normalized_title="related_work",
                ordinal=1,
                page_start=2,
                page_end=2,
            ),
        ],
        metadata=PaperMetadata(title="Cross-page Paper"),
        language="en",
        page_count=2,
    )


def paper_with_parent_and_child_sections():
    return ParsedPaper(
        pages=[
            PaperPage(
                page_number=1,
                text=(
                    "II. RELATED WORK\n"
                    "A. CNN Methods\ncnn child body\n"
                    "B. Transformer Methods\ntransformer child body"
                ),
            )
        ],
        sections=[
            PaperSectionData(
                title="II. RELATED WORK",
                normalized_title="related_work",
                level=1,
                ordinal=0,
                page_start=1,
                page_end=1,
            ),
            PaperSectionData(
                title="A. CNN Methods",
                normalized_title="other",
                level=2,
                ordinal=1,
                page_start=1,
                page_end=1,
            ),
            PaperSectionData(
                title="B. Transformer Methods",
                normalized_title="other",
                level=2,
                ordinal=2,
                page_start=1,
                page_end=1,
            ),
        ],
        metadata=PaperMetadata(title="Hierarchical Paper"),
        language="en",
        page_count=1,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("task_type", ["qa", "translation", "presentation"])
async def test_task_is_grounded_streamed_and_persisted(task_type, tmp_path):
    service, paper, session_factory, engine = await make_service(tmp_path)

    events = [
        event
        async for event in service.run_task(
            paper.id,
            task_type=task_type,
            input_text="Methods" if task_type == "translation" else "请分析方法",
            session_id="session-1",
            section="Methods" if task_type == "translation" else None,
        )
    ]

    assert [event["event"] for event in events][0] == "progress"
    done = next(event for event in events if event["event"] == "done")
    assert done["artifact_id"] > 0
    assert done["content"]
    assert done["citations"][0]["paper_id"] == paper.id
    assert done["citations"][0]["verified"] is True
    async with session_factory() as session:
        artifacts = await session.scalar(select(func.count()).select_from(PaperArtifact))
        assert artifacts == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_task_overrides_llm_foreign_paper_context(tmp_path):
    service, paper, _session_factory, engine = await make_service(tmp_path)
    service.llm = ForeignContextTaskLLM()

    events = [
        event
        async for event in service.run_task(
            paper.id,
            task_type="qa",
            input_text="请分析方法",
            session_id="foreign-context",
        )
    ]

    done = next(event for event in events if event["event"] == "done")
    assert done["citations"][0]["paper_id"] == paper.id
    assert done["citations"][0]["paper_title"] == "Task Paper"
    assert done["citations"][0]["verified"] is True
    await engine.dispose()


@pytest.mark.asyncio
async def test_qa_messages_are_restored_for_follow_up(tmp_path):
    service, paper, session_factory, engine = await make_service(tmp_path)
    async for _ in service.run_task(paper.id, "qa", "第一问", "same-session"):
        pass
    async for _ in service.run_task(paper.id, "qa", "第二问", "same-session"):
        pass

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(PaperMessage)
                .where(PaperMessage.paper_id == paper.id, PaperMessage.session_id == "same-session")
                .order_by(PaperMessage.id)
            )
        ).scalars().all()
    assert [row.role for row in rows] == ["user", "assistant", "user", "assistant"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_translation_uses_every_section_chunk_in_source_order(tmp_path):
    service, paper, session_factory, engine = await make_service(tmp_path)
    async with session_factory() as session:
        chunks = (
            await session.execute(
                select(PaperChunk)
                .where(PaperChunk.paper_id == paper.id, PaperChunk.section == "Methods")
                .order_by(PaperChunk.ordinal)
            )
        ).scalars().all()
        page_text = await session.scalar(
            select(StoredPaperPage.text).where(
                StoredPaperPage.paper_id == paper.id,
                StoredPaperPage.page_number == 1,
            )
        )
    assert len(chunks) >= 2

    llm = TranslationLLM()
    service.llm = llm
    service.retriever.search_calls = 0
    events = [
        event
        async for event in service.run_task(
            paper.id,
            task_type="translation",
            input_text="Methods",
            section="Methods",
        )
    ]

    block_events = [event for event in events if event["event"] == "block"]
    assert [event["block_index"] for event in block_events] == list(range(len(llm.sources)))
    assert {event["section"] for event in block_events} == {"Methods"}
    assert "".join("".join(source.split()) for source in llm.sources) == "".join(page_text.split())
    assert service.retriever.search_calls == 0
    done = next(event for event in events if event["event"] == "done")
    assert done["content"] == "\n\n".join(
        f"中文译文-{index}" for index in range(1, len(llm.sources) + 1)
    )
    await engine.dispose()


def paper_with_parent_intro():
    return ParsedPaper(
        pages=[
            PaperPage(
                page_number=1,
                text=(
                    "II. RELATED WORK\n"
                    "parent intro paragraph that introduces the whole chapter.\n"
                    "A. CNN Methods\ncnn child body\n"
                    "B. Transformer Methods\ntransformer child body"
                ),
            )
        ],
        sections=[
            PaperSectionData(
                title="II. RELATED WORK",
                normalized_title="related_work",
                level=1,
                ordinal=0,
                page_start=1,
                page_end=1,
            ),
            PaperSectionData(
                title="A. CNN Methods",
                normalized_title="other",
                level=2,
                ordinal=1,
                page_start=1,
                page_end=1,
            ),
            PaperSectionData(
                title="B. Transformer Methods",
                normalized_title="other",
                level=2,
                ordinal=2,
                page_start=1,
                page_end=1,
            ),
        ],
        metadata=PaperMetadata(title="Hierarchical Paper"),
        language="en",
        page_count=1,
    )


@pytest.mark.asyncio
async def test_translation_of_parent_translates_intro_and_all_children(tmp_path):
    service, paper, _session_factory, engine = await make_service(
        tmp_path,
        paper_with_parent_intro(),
    )
    llm = TranslationLLM()
    service.llm = llm

    events = [
        event
        async for event in service.run_task(
            paper.id,
            task_type="translation",
            input_text="II. RELATED WORK",
            section="II. RELATED WORK",
        )
    ]

    source = "\n".join(llm.sources)
    assert "parent intro paragraph" in source
    assert "cnn child body" in source
    assert "transformer child body" in source
    blocks = [event for event in events if event["event"] == "block"]
    assert {block["section"] for block in blocks} == {
        "II. RELATED WORK",
        "A. CNN Methods",
        "B. Transformer Methods",
    }
    done = next(event for event in events if event["event"] == "done")
    assert done["artifact_id"] == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_translation_of_parent_retranslates_all_children(tmp_path):
    service, paper, _session_factory, engine = await make_service(
        tmp_path,
        paper_with_parent_and_child_sections(),
    )
    llm = TranslationLLM()
    service.llm = llm

    async for _ in service.run_task(
        paper.id, "translation", input_text="A. CNN Methods", section="A. CNN Methods"
    ):
        pass
    async for _ in service.run_task(
        paper.id, "translation", input_text="B. Transformer Methods", section="B. Transformer Methods"
    ):
        pass
    calls_before = len(llm.sources)

    events = [
        event
        async for event in service.run_task(
            paper.id,
            task_type="translation",
            input_text="II. RELATED WORK",
            section="II. RELATED WORK",
        )
    ]

    blocks = [event for event in events if event["event"] == "block"]
    assert {block["section"] for block in blocks} == {
        "II. RELATED WORK",
        "A. CNN Methods",
        "B. Transformer Methods",
    }
    # 强制重新翻译:父章节(标题/引言) + 全部子章节都重新调用 LLM,不再复用
    assert len(llm.sources) == calls_before + 3
    await engine.dispose()


@pytest.mark.asyncio
async def test_translation_of_first_child_excludes_parent_intro(tmp_path):
    service, paper, _session_factory, engine = await make_service(
        tmp_path,
        paper_with_parent_intro(),
    )
    llm = TranslationLLM()
    service.llm = llm

    async for _ in service.run_task(
        paper.id, "translation", input_text="A. CNN Methods", section="A. CNN Methods"
    ):
        pass

    source = "\n".join(llm.sources)
    assert "parent intro paragraph" not in source
    assert "cnn child body" in source
    await engine.dispose()


@pytest.mark.asyncio
async def test_translation_removes_leading_figure_material_from_a_continued_page(tmp_path):
    service, paper, _session_factory, engine = await make_service(
        tmp_path,
        cross_page_introduction(),
    )
    llm = TranslationLLM()
    service.llm = llm

    async for _ in service.run_task(
        paper.id,
        task_type="translation",
        input_text="I. INTRODUCTION",
        section="I. INTRODUCTION",
    ):
        pass

    source = "\n".join(llm.sources)
    assert "diagram labels" not in source
    assert "Fig. 1:" not in source
    assert "prompting-empowered\nlightweight image SR method" in source
    await engine.dispose()


@pytest.mark.asyncio
async def test_translation_keeps_a_cross_page_continuation_in_one_reader_block(tmp_path):
    long_lead = ("context sentence. " * 240) + "a novel prompting-empowered"
    service, paper, _session_factory, engine = await make_service(
        tmp_path,
        cross_page_introduction(long_lead),
    )
    llm = TranslationLLM()
    service.llm = llm

    events = [
        event
        async for event in service.run_task(
            paper.id,
            task_type="translation",
            input_text="I. INTRODUCTION",
            section="I. INTRODUCTION",
        )
    ]

    assert len(llm.sources) == 1
    assert len([event for event in events if event["event"] == "block"]) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_translation_resumes_after_the_first_failed_block(tmp_path):
    service, paper, session_factory, engine = await make_service(tmp_path)
    service.translation_block_size = 500
    failing_llm = FailOnSecondTranslationLLM()
    service.llm = failing_llm
    first_events = [
        event
        async for event in service.run_task(
            paper.id,
            task_type="translation",
            input_text="Methods",
            section="Methods",
        )
    ]
    assert [event["event"] for event in first_events if event["event"] in {"block", "error"}] == [
        "block",
        "error",
    ]

    resumed_llm = TranslationLLM()
    service.llm = resumed_llm
    resumed_events = [
        event
        async for event in service.run_task(
            paper.id,
            task_type="translation",
            input_text="Methods",
            section="Methods",
        )
    ]
    blocks = [event for event in resumed_events if event["event"] == "block"]
    assert [event["block_index"] for event in blocks] == list(range(len(blocks)))
    # 按钮现在强制全量重新翻译:第二次运行不再复用已完成块
    assert len(resumed_llm.sources) == len(blocks)
    assert any(event["event"] == "done" for event in resumed_events)
    async with session_factory() as session:
        failed_count = await session.scalar(
            select(func.count())
            .select_from(PaperTranslationBlock)
            .where(PaperTranslationBlock.status == "failed")
        )
    assert failed_count == 0
    await engine.dispose()



def paper_with_table_only_chapter():

    """章节正文全是表格数据行(数字密集), 翻译源会被 _strip_table_rows 清空。"""

    return ParsedPaper(

        pages=[

            PaperPage(

                page_number=1,

                text=("Results\n" + "31.55 4.932 0.9730 0.0236 0.0552 0.0282 32.97 6.059\n" * 20)

            )

        ],

        sections=[

            PaperSectionData(title="Results", normalized_title="results", level=1, ordinal=0, page_start=1, page_end=1),

        ],

        metadata=PaperMetadata(title="Table Only Paper"),

        language="en",

        page_count=1,

    )





@pytest.mark.asyncio

async def test_translation_reports_filtered_chunks_in_done_warnings(tmp_path):

    """翻译源全被过滤(如纯表格数据章节)时, done 事件应带 warnings 提示。"""

    service, paper, _session_factory, engine = await make_service(

        tmp_path,

        paper_with_table_only_chapter(),

    )

    service.llm = TranslationLLM()

    events = [

        event

        async for event in service.run_task(

            paper.id,

            task_type="translation",

            input_text="Results",

            section="Results",

        )

    ]

    done = next(event for event in events if event["event"] == "done")

    assert done.get("warnings"), "全表格数据章节应产生过滤警告"

    assert any("被过滤" in w or "遗漏" in w for w in done["warnings"]), done.get("warnings")

    await engine.dispose()
