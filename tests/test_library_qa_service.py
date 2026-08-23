"""全库问答服务 run_library_qa 测试(LangGraph 图驱动)。

- test_parse_filter_response_*: 过滤条件解析的直接单元测试。
- test_library_qa_full_flow: 真实 aiosqlite 临时库 + FakeRetriever + FakeLLM 全链路
  (意图路由 → 方向选择 → 检索 → 相关性评估 → 生成 → 引用校验 → 会话落库)。
- test_library_qa_generation_failure_falls_back: 生成阶段解析失败 → generate 节点兜底文案。
- test_library_qa_empty_content_falls_back: content 为空 → 兜底文案。
- test_library_qa_empty_library_falls_back: 全库为空 → 兜底文案。
"""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.base import Base
from app.models.paper import Paper, PaperMessage
from app.paper.schemas import PaperChunkData, PaperSearchResult
from app.paper.service import PaperService


def _resp(payload: dict):
    return type("Response", (), {"content": json.dumps(payload, ensure_ascii=False)})()


def test_parse_filter_response_returns_defaults_when_no_json():
    from app.paper.prompts import parse_filter_response

    assert parse_filter_response("无法提取任何条件") == {
        "field": "",
        "year_min": None,
        "year_max": None,
        "authors": [],
        "keywords": [],
        "language": "",
    }


def test_parse_filter_response_normalizes_values():
    from app.paper.prompts import parse_filter_response

    filters = parse_filter_response(
        '{"field": "超分辨率", "year_min": "2020", "year_max": 2022, '
        '"authors": "张三", "keywords": ["SR"], "language": "zh"}'
    )
    assert filters["field"] == "超分辨率"
    assert filters["year_min"] == 2020
    assert filters["year_max"] == 2022
    assert filters["authors"] == ["张三"]
    assert filters["keywords"] == ["SR"]
    assert filters["language"] == "zh"


_FILTER_PAYLOAD = {
    "field": "超分辨率",
    "year_min": 2020,
    "year_max": None,
    "authors": [],
    "keywords": [],
    "language": "",
}


class _LibraryLLM:
    """按调用顺序吐出预设响应字符串。

    图驱动下 LLM 调用序: direction_select(过滤 JSON) → relevance_evaluate(评分数字 xN)
    → generate(生成 JSON)。评分节点只读数字, 过滤/生成节点解析 JSON。
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def ainvoke(self, prompt):
        self.calls += 1
        raw = self._responses.pop(0) if self._responses else ""
        return type("Response", (), {"content": raw})()


class _LibraryRetriever:
    def __init__(self):
        self.chunks = {
            1: [
                PaperChunkData(
                    paper_id=1, chunk_id="lib-1-a", section="Intro",
                    page_start=1, page_end=1, content="evidence a",
                )
            ],
            2: [
                PaperChunkData(
                    paper_id=2, chunk_id="lib-2-b", section="Methods",
                    page_start=2, page_end=2, content="evidence b",
                )
            ],
        }

    async def search(self, paper_id, query, k=8, section=None):
        hits = [PaperSearchResult(chunk=c, score=1.0) for c in self.chunks.get(paper_id, [])]
        return hits[:k]


def _default_gen_payload():
    return {
        "content": "这是全库问答的聚合回答。",
        "citations": [
            {
                "paper_id": 1,
                "paper_title": "Paper One",
                "page": 1,
                "section": "Intro",
                "chunk_id": "lib-1-a",
                "quote": "evidence a",
            },
            {
                # paper_id 不在候选集 → M3 应跳过
                "paper_id": 999,
                "paper_title": "Ghost",
                "page": 1,
                "section": "X",
                "chunk_id": "lib-999-x",
                "quote": "ghost",
            },
            {
                "paper_id": 2,
                "paper_title": "Paper Two",
                "page": 2,
                "section": "Methods",
                "chunk_id": "lib-2-b",
                "quote": "evidence b",
            },
            {
                # paper_id 合法但 chunk_id 不在证据集 → M3 应跳过
                "paper_id": 2,
                "paper_title": "Paper Two",
                "page": 2,
                "section": "Methods",
                "chunk_id": "lib-2-ghost",
                "quote": "ghost chunk",
            },
        ],
    }


def _default_llm():
    """默认 LLM 序列: 过滤 → 评分 x2(2 篇候选) → 生成。"""
    return _LibraryLLM([
        json.dumps(_FILTER_PAYLOAD, ensure_ascii=False),
        "4",
        "4",
        json.dumps(_default_gen_payload(), ensure_ascii=False),
    ])


async def _make_library_service(tmp_path, llm=None):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'libqa.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        session.add_all(
            [
                Paper(
                    original_filename="p1.pdf", stored_filename="p1.pdf",
                    title="Paper One", research_field="超分辨率",
                    publication_year=2021, language="en", status="ready",
                ),
                Paper(
                    original_filename="p2.pdf", stored_filename="p2.pdf",
                    title="Paper Two", research_field="超分辨率",
                    publication_year=2022, language="en", status="ready",
                ),
                Paper(
                    original_filename="p3.pdf", stored_filename="p3.pdf",
                    title="Paper Three", research_field="图像去噪",
                    publication_year=2019, language="zh", status="ready",
                ),
            ]
        )
        await session.commit()
    service = PaperService(
        session_factory=session_factory,
        retriever=_LibraryRetriever(),
        llm=llm or _default_llm(),
        files_dir=tmp_path / "files",
        graph=object(),
        parser_fn=lambda _path: None,
    )
    return service, session_factory, engine


@pytest.mark.asyncio
async def test_library_qa_full_flow(tmp_path):
    """图驱动全链路: node 事件序列 + done content/citations; PaperMessage 落库。"""
    service, session_factory, engine = await _make_library_service(tmp_path)

    events = [
        event
        async for event in service.run_library_qa("近两年超分辨率论文的不足", session_id="lib-session")
    ]

    kinds = [event["event"] for event in events]
    assert kinds[-1] == "done"
    assert "node" in kinds

    node_names = [e["node"] for e in events if e["event"] == "node"]
    for n in ("intent_router", "direction_select", "retrieve", "relevance_evaluate", "generate", "cite_verify"):
        assert n in node_names, f"缺少节点事件 {n}"

    direction = next(e for e in events if e["event"] == "node" and e["node"] == "direction_select")
    assert direction["status"] == "completed"
    assert direction["filters"]["field"] == "超分辨率"
    assert direction.get("degraded", []) == []

    done = events[-1]
    assert done["content"] == "这是全库问答的聚合回答。"
    # M3: paper_id=999 与非法 chunk_id 的 2 条被跳过, 只剩 2 条
    assert len(done["citations"]) == 2
    assert {c["paper_id"] for c in done["citations"]} == {1, 2}
    assert all(c["verified"] is True and c["reason"] == "library_qa" for c in done["citations"])
    assert done["citations"][0]["quote"] == "evidence a"

    # done 前按 80 字符切片重放 token, 拼接应还原完整回答(打字机效果)
    tokens = "".join(e["content"] for e in events if e["event"] == "token")
    assert tokens == done["content"]

    # 会话落库: 2 行, paper_id=0, citations_json 可解析且与 done 一致
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(PaperMessage)
                .where(PaperMessage.session_id == "lib-session")
                .order_by(PaperMessage.id)
            )
        ).scalars().all()
    assert [row.role for row in rows] == ["user", "assistant"]
    assert all(row.paper_id == 0 for row in rows)
    assert json.loads(rows[1].citations_json) == done["citations"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_library_qa_generation_failure_falls_back(tmp_path):
    """M1: 生成阶段 LLM 返回非法 JSON → generate 节点兜底文案, 不产 error 事件。"""
    llm = _LibraryLLM([
        json.dumps(_FILTER_PAYLOAD, ensure_ascii=False),
        "4", "4",
        "这不是合法 JSON",  # generate 解析失败 → 兜底
    ])
    service, _session_factory, engine = await _make_library_service(tmp_path, llm=llm)

    # 注: 输入需命中 PAPER_TERMS 规则("超分")走 rag 分支; 若用通用问题串会经 relevance_check 判 general,
    # 旧图时期的 LLM 序列(过滤→评分→生成)不再适用
    events = [event async for event in service.run_library_qa("超分方法的问题")]
    done = next(event for event in events if event["event"] == "done")
    assert "error" not in [event["event"] for event in events]
    assert done["content"] == "未能在论文库中找到充分证据回答该问题。"
    assert done["citations"] == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_library_qa_empty_content_falls_back(tmp_path):
    """M2: LLM 返回空 content → 兜底文案。"""
    llm = _LibraryLLM([
        json.dumps(_FILTER_PAYLOAD, ensure_ascii=False),
        "4", "4",
        json.dumps({"content": "", "citations": []}, ensure_ascii=False),
    ])
    service, _session_factory, engine = await _make_library_service(tmp_path, llm=llm)

    # 同上: 输入命中规则走 rag 分支, 验证 generate 空 content 兜底
    events = [event async for event in service.run_library_qa("超分方法的问题", session_id="empty-session")]
    done = next(event for event in events if event["event"] == "done")
    assert done["content"] == "未能在论文库中找到充分证据回答该问题。"
    assert done["citations"] == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_library_qa_empty_library_falls_back(tmp_path):
    """全库为空: direction_select candidates=[] → retrieve evidence=[] → generate 兜底文案 → done。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'empty.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    llm = _LibraryLLM([
        json.dumps({"field": "", "year_min": None, "year_max": None, "authors": [], "keywords": [], "language": ""}),
    ])
    service = PaperService(
        session_factory=session_factory,
        retriever=_LibraryRetriever(),
        llm=llm,
        files_dir=tmp_path / "files",
        graph=object(),
        parser_fn=lambda _path: None,
    )

    events = [event async for event in service.run_library_qa("超分方法", session_id="empty-session")]
    done = next(event for event in events if event["event"] == "done")
    assert done["content"] == "未能在论文库中找到充分证据回答该问题。"
    assert done["citations"] == []
    node_names = [e["node"] for e in events if e["event"] == "node"]
    assert "generate" in node_names and "cite_verify" in node_names
    await engine.dispose()


@pytest.mark.asyncio
async def test_library_qa_chitchat_uses_chat_node(tmp_path):
    """闲聊分支: intent=chitchat 走 chat_node, done.content 来自 chat_node 输出(不进 QA 子图)。"""
    llm = _LibraryLLM([
        json.dumps({"content": "你好!我是论文知识问答助手, 可以问我论文库里的内容。", "citations": []}, ensure_ascii=False),
    ])
    service, _session_factory, engine = await _make_library_service(tmp_path, llm=llm)

    events = [event async for event in service.run_library_qa("你好", session_id="chat-session")]

    kinds = [event["event"] for event in events]
    assert kinds[-1] == "done"
    node_names = [e["node"] for e in events if e["event"] == "node"]
    assert "intent_router" in node_names and "chat_node" in node_names
    # 闲聊分支不进 QA 子图
    assert "direction_select" not in node_names and "generate" not in node_names
    assert llm.calls == 1  # 仅 chat_node 一次调用

    done = events[-1]
    assert done["content"] == "你好!我是论文知识问答助手, 可以问我论文库里的内容。"
    assert done["citations"] == []
    # done 前 token 重放: 拼接 == done.content
    tokens = "".join(e["content"] for e in events if e["event"] == "token")
    assert tokens == done["content"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_library_qa_history_orders_and_limits(tmp_path):
    """历史接口: 只取最近 limit 条, 按时间正序返回, 同时间戳按 id 次键稳定。"""
    from datetime import datetime, timedelta

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'hist.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    # 时间线: q0@t0, q1@t1, q2@t1(与 q1 同 created_at, id 更大), q3@t2, q4@t3
    base = datetime(2026, 1, 1)
    async with session_factory() as session:
        session.add_all(
            [
                PaperMessage(
                    paper_id=0, session_id="hist-session", role="user",
                    content=f"q{i}", citations_json="[]",
                    created_at=base + timedelta(minutes={0: 0, 1: 1, 2: 1, 3: 2, 4: 3}[i]),
                )
                for i in range(5)
            ]
        )
        await session.commit()
    service = PaperService(
        session_factory=session_factory,
        retriever=object(),
        llm=object(),
        files_dir=tmp_path / "files",
    )
    msgs = await service.get_library_history("hist-session", limit=3)
    # 最近 3 条: q4, q3, q2(created_at DESC, id DESC 取前 3) → reverse 恢复正序
    assert [m["content"] for m in msgs] == ["q2", "q3", "q4"]
    assert all(m["role"] == "user" for m in msgs)
    assert all(isinstance(m["citations"], list) and m["timestamp"] for m in msgs)

    # 其他 session 的消息不混入
    async with session_factory() as session:
        session.add(
            PaperMessage(
                paper_id=0, session_id="other-session", role="user",
                content="other", citations_json="[]",
                created_at=base + timedelta(minutes=99),
            )
        )
        await session.commit()
    msgs2 = await service.get_library_history("hist-session", limit=3)
    assert [m["content"] for m in msgs2] == ["q2", "q3", "q4"]
    await engine.dispose()


def test_catalog_prompt_contains_paper_metadata():
    from app.paper.prompts import build_library_catalog_prompt
    import types

    p = types.SimpleNamespace(title="T1", research_field="超分辨率", publication_year=2024)
    prompt = build_library_catalog_prompt("有什么论文", [p])
    assert "T1" in prompt and "超分辨率" in prompt and "2024" in prompt


@pytest.mark.asyncio
async def test_library_qa_catalog_question_lists_papers(tmp_path):
    """库清单类问题走 catalog 分支: 不检索证据, 直接返回论文列表; 会话落库 citations 为空。"""
    llm = _LibraryLLM([
        json.dumps({"content": "论文库共 3 篇论文: Paper One、Paper Two、Paper Three。", "citations": []}, ensure_ascii=False),
    ])
    service, session_factory, engine = await _make_library_service(tmp_path, llm=llm)

    events = [event async for event in service.run_library_qa("你现在有什么论文", session_id="cat-session")]

    kinds = [event["event"] for event in events]
    assert kinds[-1] == "done"
    node_names = [e["node"] for e in events if e["event"] == "node"]
    assert "catalog_node" in node_names
    assert llm.calls == 1  # 仅 catalog 生成, 未走过滤/评分/证据检索

    done = events[-1]
    assert done["content"] == "论文库共 3 篇论文: Paper One、Paper Two、Paper Three。"
    assert done["citations"] == []

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(PaperMessage).where(PaperMessage.session_id == "cat-session")
            )
        ).scalars().all()
    assert [row.role for row in rows] == ["user", "assistant"]
    assert all(row.paper_id == 0 for row in rows)
    assert json.loads(rows[1].citations_json) == []
    await engine.dispose()


def test_candidate_filter_ignores_keywords(tmp_path):
    """回归: keywords 是检索词不参与 SQL 候选过滤, 否则会把候选集滤空。"""
    import asyncio

    from app.database.base import Base as _Base
    from app.models.paper import Paper as _Paper

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/kw.db")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _run():
        async with engine.begin() as conn:
            await conn.run_sync(_Base.metadata.create_all)
        async with session_factory() as session:
            session.add(_Paper(
                original_filename="a.pdf", stored_filename="a.pdf",
                title="MWAT-SR: lightweight super-resolution",
                research_field="超分辨率", status="ready",
            ))
            await session.commit()
        svc = PaperService.__new__(PaperService)
        svc.session_factory = session_factory
        svc.retriever = None

        # 构造 filters: field 命中 + keywords 是通用词(英文标题不含)
        filters = {
            "field": "超分辨率", "year_min": None, "year_max": None,
            "authors": [], "keywords": ["超分", "方法"], "language": "",
        }
        async with session_factory() as session:
            from sqlalchemy import select as _select
            stmt = _select(_Paper)
            conds = []
            if filters.get("field"):
                conds.append(_Paper.research_field == filters["field"])
            # keywords 不参与(与 service 实现一致)
            if conds:
                stmt = stmt.where(*conds)
            papers = (await session.execute(stmt)).scalars().all()
        assert len(papers) == 1
        await engine.dispose()

    asyncio.run(_run())

@pytest.mark.asyncio
async def test_library_qa_general_branch_uses_general_chat(tmp_path):
    """通用问题分支: relevance_check 判 false -> general_chat_node, done.content 来自 general_chat。
    回归 P0: service 收集 final_content 的白名单缺 general_chat_node 时 done.content 是兜底串。"""
    llm = _LibraryLLM([
        "false",  # relevance_check 判定 -> general
        json.dumps({"content": "深度学习是一类基于多层神经网络的机器学习方法。", "citations": []}, ensure_ascii=False),
    ])
    service, session_factory, engine = await _make_library_service(tmp_path, llm=llm)

    events = [event async for event in service.run_library_qa("什么是深度学习", session_id="gen-session")]

    kinds = [event["event"] for event in events]
    assert kinds[-1] == "done"
    node_names = [e["node"] for e in events if e["event"] == "node"]
    assert "relevance_check" in node_names and "general_chat_node" in node_names
    # general 分支不进 QA 子图
    assert "direction_select" not in node_names and "generate" not in node_names
    assert llm.calls == 2  # relevance_check 判定 + general_chat 生成

    done = events[-1]
    assert done["content"] == "深度学习是一类基于多层神经网络的机器学习方法。"
    assert done["content"] != "未能在论文库中找到充分证据回答该问题。"
    assert done["citations"] == []
    tokens = "".join(e["content"] for e in events if e["event"] == "token")
    assert tokens == done["content"]

    # 落库内容也是 general_chat 输出
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(PaperMessage).where(PaperMessage.session_id == "gen-session")
            )
        ).scalars().all()
    assert [row.role for row in rows] == ["user", "assistant"]
    assert rows[1].content == "深度学习是一类基于多层神经网络的机器学习方法。"
    await engine.dispose()

