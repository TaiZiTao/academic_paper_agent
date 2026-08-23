from app.paper.library_nodes import intent_router_node


def test_intent_chitchat():
    r = intent_router_node({"input_text": "你好"})
    assert r["intent"] == "chitchat"


def test_intent_catalog():
    r = intent_router_node({"input_text": "库里有什么论文"})
    assert r["intent"] == "catalog"


def test_intent_qa():
    r = intent_router_node({"input_text": "超分的方法有什么问题"})
    assert r["intent"] == "qa"


def test_intent_catalog_intro():
    r = intent_router_node({"input_text": "介绍一下论文库"})
    assert r["intent"] == "catalog"


def test_intent_intro_not_catalog_when_method():
    r = intent_router_node({"input_text": "介绍论文库中轻量超分方法的原理"})
    assert r["intent"] == "qa"

import pytest
from app.paper.library_nodes import chat_node, catalog_node


@pytest.mark.asyncio
async def test_chat_node_no_llm_fallback():
    r = await chat_node({"input_text": "你好"}, None)
    assert "论文知识问答助手" in r["content"]


@pytest.mark.asyncio
async def test_catalog_node_empty_candidates():
    r = await catalog_node({"input_text": "有什么论文"}, None)
    assert "没有论文" in r["content"]


@pytest.mark.asyncio
async def test_catalog_node_no_llm_with_papers():
    import types
    p = types.SimpleNamespace(id=1, title="T1", research_field="超分辨率", publication_year=2024)
    r = await catalog_node({"input_text": "有什么论文", "candidates": [p]}, None)
    assert "1 篇" in r["content"]


from app.paper.library_nodes import direction_select_node, retrieve_node


@pytest.mark.asyncio
async def test_direction_select_normalizes_abbreviation(tmp_path):
    """方向归一化: 问题含"超分"时, 应匹配到库内"超分辨率"。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.database.base import Base
    from app.models.paper import Paper
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/d.db")
    sf = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with sf() as session:
        session.add(Paper(original_filename="a.pdf", stored_filename="a.pdf", title="SR paper", research_field="超分辨率", status="ready"))
        await session.commit()
    # llm=None: 跳过 LLM 提取, 直接走滑动窗口归一化
    r = await direction_select_node({"input_text": "超分的方法"}, {"configurable": {"session_factory": sf, "llm": None}})
    assert r["filters"]["field"] == "超分辨率"
    assert len(r["candidates"]) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_direction_select_degradation(tmp_path):
    """候选为空时降级: 全库为空才报错, 否则放宽条件。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.database.base import Base
    from app.models.paper import Paper
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/d2.db")
    sf = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with sf() as session:
        session.add(Paper(original_filename="a.pdf", stored_filename="a.pdf", title="SR", research_field="超分辨率", status="ready"))
        await session.commit()
    # LLM 返回一个不存在的方向 -> 候选空 -> 降级 all
    class FakeLLM:
        async def ainvoke(self, prompt):
            import json, types
            return types.SimpleNamespace(content=json.dumps({"field": "不存在方向", "year_min": None, "year_max": None, "authors": [], "keywords": [], "language": ""}))
    r = await direction_select_node({"input_text": "不存在方向的论文"}, {"configurable": {"session_factory": sf, "llm": FakeLLM()}})
    assert r["degraded"] == ["all"]
    assert len(r["candidates"]) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_direction_select_db_error_degrades():
    """候选查询 DB 异常: 节点内降级返回空候选, 不冒泡(service 不再需要兜底)。"""
    class BoomSessionFactory:
        def __call__(self):
            raise RuntimeError("db down")

    r = await direction_select_node(
        {"input_text": "超分方法"},
        {"configurable": {"session_factory": BoomSessionFactory(), "llm": None}},
    )
    assert r["candidates"] == []
    assert r["filters"] == {}
    assert r["degraded"] == []


@pytest.mark.asyncio
async def test_retrieve_returns_evidence(tmp_path):
    """retrieve: 逐篇采样返回 evidence。"""
    import types
    chunk = types.SimpleNamespace(paper_id=1, chunk_id="c1", section="s", page_start=1, page_end=1, ordinal=0, content="ev", metadata={})
    class FakeRetriever:
        async def search(self, pid, query, k=8, section=None):
            return [types.SimpleNamespace(chunk=chunk, score=0.9)]
    paper = types.SimpleNamespace(id=1)
    r = await retrieve_node({"candidates": [paper], "query": "超分方法"}, {"configurable": {"retriever": FakeRetriever()}})
    assert len(r["evidence"]) == 1
    assert r["evidence"][0].chunk_id == "c1"


@pytest.mark.asyncio
async def test_direction_select_llm_abbreviation_overridden(tmp_path):
    """LLM 输出缩写(field="超分")时, 滑动窗口应覆盖为标准方向名(迁移不回归)。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.database.base import Base
    from app.models.paper import Paper
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/d3.db")
    sf = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with sf() as session:
        session.add(Paper(original_filename="a.pdf", stored_filename="a.pdf", title="SR", research_field="超分辨率", status="ready"))
        await session.commit()

    class FakeLLM:
        async def ainvoke(self, prompt):
            import json, types
            return types.SimpleNamespace(content=json.dumps({"field": "超分", "year_min": None, "year_max": None, "authors": [], "keywords": [], "language": ""}))

    r = await direction_select_node({"input_text": "超分的方法"}, {"configurable": {"session_factory": sf, "llm": FakeLLM()}})
    assert r["filters"]["field"] == "超分辨率"
    assert len(r["candidates"]) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_direction_select_year_degradation(tmp_path):
    """年份过滤为空 -> 降级 ["year"], 方向条件保留。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.database.base import Base
    from app.models.paper import Paper
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/d4.db")
    sf = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with sf() as session:
        session.add(Paper(original_filename="a.pdf", stored_filename="a.pdf", title="SR", research_field="超分辨率", publication_year=2020, status="ready"))
        await session.commit()

    class FakeLLM:
        async def ainvoke(self, prompt):
            import json, types
            # 注: year_min 需在 parse_filter_response 的 1900-2100 范围内(2999 会被归一化为 None, 过滤不生效)
            return types.SimpleNamespace(content=json.dumps({"field": "超分辨率", "year_min": 2024, "year_max": None, "authors": [], "keywords": [], "language": ""}))

    r = await direction_select_node({"input_text": "2024年的超分论文"}, {"configurable": {"session_factory": sf, "llm": FakeLLM()}})
    assert r["degraded"] == ["year"]
    assert r["filters"]["field"] == "超分辨率"
    assert len(r["candidates"]) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_retrieve_empty_input():
    """retrieve: 无候选时返回空 evidence。"""
    r = await retrieve_node({"candidates": []}, {"configurable": {"retriever": object()}})
    assert r["evidence"] == []


from app.paper.library_nodes import relevance_evaluate_node, rewrite_query_node


def _chunk(content, section="s", page=1):
    import types
    return types.SimpleNamespace(section=section, page_start=page, content=content)


@pytest.mark.asyncio
async def test_relevance_top3_only():
    """仅 top-3 评分: 6 条 evidence 时 LLM 只被调用 3 次。"""
    import types
    calls = []

    class FakeLLM:
        async def ainvoke(self, prompt):
            calls.append(prompt)
            return types.SimpleNamespace(content="4")

    chunks = [_chunk("内容%d" % i) for i in range(6)]
    r = await relevance_evaluate_node({"evidence": chunks, "query": "超分辨率"}, {"configurable": {"llm": FakeLLM()}})
    assert len(calls) == 3
    assert len(r["relevance_scores"]) == 3


@pytest.mark.asyncio
async def test_relevance_score_clamp():
    """LLM 返回越界/非数字: clamp 到 1-5。"""
    import types
    answers = iter(["9", "0", "完全无关"])

    class FakeLLM:
        async def ainvoke(self, prompt):
            return types.SimpleNamespace(content=next(answers))

    chunks = [_chunk("c%d" % i) for i in range(3)]
    r = await relevance_evaluate_node({"evidence": chunks, "query": "english query"}, {"configurable": {"llm": FakeLLM()}})
    scores = [s["score"] for s in r["relevance_scores"]]
    assert scores == [5, 1, 3]


@pytest.mark.asyncio
async def test_relevance_no_keywords_no_filter():
    """纯英文 query: 无中文关键词时不过滤(evidence 键不出现)。"""
    import types
    chunks = [_chunk("some english content") for _ in range(2)]

    class FakeLLM:
        async def ainvoke(self, prompt):
            return types.SimpleNamespace(content="4")

    r = await relevance_evaluate_node({"evidence": chunks, "query": "super resolution methods"}, {"configurable": {"llm": FakeLLM()}})
    assert "evidence" not in r
    assert len(r["relevance_scores"]) == 2


@pytest.mark.asyncio
async def test_rewrite_increments_retry():
    """FakeLLM 返回改写词: query 被改写且 retry_count+1。"""
    import types

    class FakeLLM:
        async def ainvoke(self, prompt):
            return types.SimpleNamespace(content="新的检索关键词")

    r = await rewrite_query_node({"query": "旧查询", "retry_count": 1}, {"configurable": {"llm": FakeLLM()}})
    assert r["query"] == "新的检索关键词"
    assert r["retry_count"] == 2


@pytest.mark.asyncio
async def test_rewrite_llm_none():
    """config 无 llm: 保留原 query 且 retry_count+1。"""
    r = await rewrite_query_node({"query": "原查询", "retry_count": 2}, None)
    assert r["query"] == "原查询"
    assert r["retry_count"] == 3


from app.paper.library_nodes import generate_node


@pytest.mark.asyncio
async def test_catalog_node_loads_all_from_db(tmp_path):
    """catalog 分支不经过 direction_select: candidates 为空时 catalog_node 自行查全库(回归 2026-08)。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.database.base import Base
    from app.models.paper import Paper
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/cat.db")
    sf = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with sf() as session:
        session.add_all([
            Paper(original_filename="a.pdf", stored_filename="a.pdf", title="SR", research_field="超分辨率", status="ready"),
            Paper(original_filename="b.pdf", stored_filename="b.pdf", title="Dehaze", research_field="图像去雾", status="ready"),
        ])
        await session.commit()
    # llm=None: 走默认文案"论文库当前有 N 篇论文"
    r = await catalog_node({"input_text": "有什么论文"}, {"configurable": {"session_factory": sf, "llm": None}})
    assert "2 篇" in r["content"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_generate_node_happy_path():
    """LLM 返回 content+citations: 透传 content 并带 raw_citations。"""
    import json, types
    paper = types.SimpleNamespace(id=1, title="SR")
    chunk = types.SimpleNamespace(paper_id=1, chunk_id="c1", section="方法", page_start=1, page_end=1, ordinal=0, content="超分辨率重建方法", metadata={})

    class FakeLLM:
        async def ainvoke(self, prompt):
            return types.SimpleNamespace(content=json.dumps({
                "content": "超分辨率重建主流方法…",
                "citations": [{"paper_id": 1, "paper_title": "SR", "page": 1, "section": "方法", "chunk_id": "c1", "quote": "超分辨率重建方法"}],
            }))

    r = await generate_node({"input_text": "有哪些超分方法", "candidates": [paper], "evidence": [chunk]},
                            {"configurable": {"llm": FakeLLM()}})
    assert r["content"] == "超分辨率重建主流方法…"
    assert len(r["raw_citations"]) == 1
    assert r["raw_citations"][0]["paper_id"] == 1


@pytest.mark.asyncio
async def test_generate_node_llm_error_fallback():
    """LLM 抛异常: 返回兜底文案 + 空 raw_citations(与空证据分支一致)。"""
    import types
    chunk = types.SimpleNamespace(paper_id=1, chunk_id="c1", section="方法", page_start=1, page_end=1, ordinal=0, content="超分辨率重建方法", metadata={})

    class BoomLLM:
        async def ainvoke(self, prompt):
            raise RuntimeError("llm down")

    r = await generate_node({"input_text": "有哪些超分方法", "candidates": [], "evidence": [chunk]},
                            {"configurable": {"llm": BoomLLM()}})
    assert "充分证据" in r["content"]
    assert r["raw_citations"] == []


from app.paper.library_nodes import relevance_check_node, general_chat_node


@pytest.mark.asyncio
async def test_relevance_rule_hits_paper_term(tmp_path):
    """规则1: 全库标题的大写缩写(PGDUN)命中问题文本 -> rag。
    relevance_check 在 direction_select 之前运行, candidates 恒为空, 规则1直接查库取标题。"""
    import types
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.database.base import Base
    from app.models.paper import Paper
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/rel.db")
    sf = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with sf() as session:
        session.add(Paper(original_filename="a.pdf", stored_filename="a.pdf",
                          title="PGDUN: Prompt-Guided Deep Unfolding", research_field="超分辨率", status="ready"))
        await session.commit()

    class FakeLLM:
        async def ainvoke(self, prompt):
            # 规则1命中则不会走到 LLM; 若规则1未命中, "false" -> general, 测试失败
            return types.SimpleNamespace(content="false")

    r = await relevance_check_node(
        {"input_text": "PGDUN 的 PGSA 怎么实现"},
        {"configurable": {"session_factory": sf, "llm": FakeLLM()}},
    )
    assert r["intent_route"] == "rag"
    await engine.dispose()


def test_relevance_rule_hits_direction():
    import asyncio
    r = asyncio.run(relevance_check_node({"input_text": "超分的方法有什么问题", "candidates": []}))
    assert r["intent_route"] == "rag"


def test_relevance_llm_general():
    import asyncio, types

    class FakeLLM:
        async def ainvoke(self, prompt):
            return types.SimpleNamespace(content="false")

    r = asyncio.run(relevance_check_node({"input_text": "什么是深度学习", "candidates": []}, {"configurable": {"llm": FakeLLM()}}))
    assert r["intent_route"] == "general"


def test_relevance_llm_rag():
    import asyncio, types

    class FakeLLM:
        async def ainvoke(self, prompt):
            return types.SimpleNamespace(content="true")

    r = asyncio.run(relevance_check_node({"input_text": "某篇论文的创新点", "candidates": []}, {"configurable": {"llm": FakeLLM()}}))
    assert r["intent_route"] == "rag"


def test_general_chat_no_llm():
    import asyncio
    r = asyncio.run(general_chat_node({"input_text": "你好"}, None))
    assert r["content"] != ""


def test_relevance_no_llm_no_rule_fallback_rag():
    """config 无 llm 且规则未命中: 保守走 rag。"""
    import asyncio
    r = asyncio.run(relevance_check_node({"input_text": "今天天气怎么样"}, None))
    assert r["intent_route"] == "rag"


def test_relevance_llm_empty_fallback_rag():
    """LLM 返回空串: 空输出保守走 rag(不能静默判 general)。"""
    import asyncio, types

    class FakeLLM:
        async def ainvoke(self, prompt):
            return types.SimpleNamespace(content="")

    r = asyncio.run(relevance_check_node({"input_text": "今天天气怎么样"}, {"configurable": {"llm": FakeLLM()}}))
    assert r["intent_route"] == "rag"


def test_relevance_llm_error_fallback_rag():
    """LLM 抛异常: 保守走 rag。"""
    import asyncio, types

    class BoomLLM:
        async def ainvoke(self, prompt):
            raise RuntimeError("llm down")

    r = asyncio.run(relevance_check_node({"input_text": "今天天气怎么样"}, {"configurable": {"llm": BoomLLM()}}))
    assert r["intent_route"] == "rag"


from app.paper.library_nodes import _match_papers_by_title


def test_match_single_paper():
    import types
    p1 = types.SimpleNamespace(id=1, title="PGDUN: Prompt-Guided Deep Unfolding for Hyperspectral")
    p2 = types.SimpleNamespace(id=2, title="MWAT-SR: A Lightweight Multi-Window Attention Transformer")
    matched = _match_papers_by_title("PGDUN 的 PGSA 模块怎么实现", [p1, p2])
    assert [m.id for m in matched] == [1]


def test_match_two_papers_comparison():
    import types
    p1 = types.SimpleNamespace(id=1, title="MWAT-SR: A Lightweight Multi-Window")
    p2 = types.SimpleNamespace(id=2, title="Dual-domain Modulation Network for Lightweight")
    p3 = types.SimpleNamespace(id=3, title="PromptSR: Cascade Prompting")
    matched = _match_papers_by_title("MWAT-SR 和 Dual-domain 有什么不同", [p1, p2, p3])
    assert sorted(m.id for m in matched) == [1, 2]


def test_match_no_paper():
    import types
    p = types.SimpleNamespace(id=1, title="PGDUN: Prompt-Guided")
    assert _match_papers_by_title("超分方向有哪些方法", [p]) == []


def test_match_hyphen_variants():
    import types
    p = types.SimpleNamespace(id=1, title="MWAT-SR: A Lightweight Multi-Window Attention Transformer")
    # 空格分隔写法 / 只写缩写, 都应命中连字符缩写 MWAT-SR
    assert [m.id for m in _match_papers_by_title("MWAT SR 是什么", [p])] == [1]
    assert [m.id for m in _match_papers_by_title("MWAT 是什么", [p])] == [1]


def test_match_no_title_object():
    import types
    p = types.SimpleNamespace(id=1)
    assert _match_papers_by_title("MWAT 是什么", [p]) == []


@pytest.mark.asyncio
async def test_retrieve_matched_papers_persists():
    """retrieve 匹配用 input_text(用户原问题): query 被 rewrite 改写后单篇深挖不丢。"""
    import types

    class FakeRetriever:
        async def search(self, pid, query, k=8, section=None):
            chunk = types.SimpleNamespace(paper_id=pid, chunk_id="c%d" % pid, section="s", page_start=1, page_end=1, ordinal=0, content="ev", metadata={})
            return [types.SimpleNamespace(chunk=chunk, score=0.9) for _ in range(k)]

    p1 = types.SimpleNamespace(id=1, title="MWAT-SR: A Lightweight Multi-Window Attention Transformer")
    p2 = types.SimpleNamespace(id=2, title="PromptSR: Cascade Prompting")
    r = await retrieve_node(
        {"candidates": [p1, p2], "input_text": "MWAT 是什么", "query": "完全不同的改写查询"},
        {"configurable": {"retriever": FakeRetriever()}},
    )
    assert r["matched_papers"] == [1]



# ============================================================
# Task5 回归: 单篇点名论文不被 field 幻觉过滤 / 缩写匹配真实标题
# ============================================================


def test_match_abbrev_via_title_initials():
    """问题侧全大写连字符缩写(MWAT-SR)应命中标题不含该缩写的论文:
    库内 paper3 真实标题 "A lightweight multi-window attention transformer
    for image super-resolution" -> 首字母串含 "mwat" 与 "sr"。
    """
    import types
    p3 = types.SimpleNamespace(
        id=3,
        title="A lightweight multi-window attention transformer for image super-resolution",
    )
    p2 = types.SimpleNamespace(
        id=2,
        title="Dual-domain Modulation Network for Lightweight Image Super-Resolution",
    )
    matched = _match_papers_by_title("MWAT-SR 和 Dual-domain 有什么不同", [p2, p3])
    assert sorted(m.id for m in matched) == [2, 3]


def test_match_abbrev_no_false_positive():
    """缩写部件必须都命中首字母串: 只含 sr 的超分论文不应因 MWAT-SR 被误命中。"""
    import types
    p = types.SimpleNamespace(id=1, title="PromptSR: Cascade Prompting for Lightweight Image Super-Resolution")
    assert _match_papers_by_title("MWAT-SR 是什么", [p]) == []


@pytest.mark.asyncio
async def test_direction_select_keeps_named_paper(tmp_path):
    """LLM 幻觉 field=超分辨率 时, 问题点名的 PGDUN(真实方向=图像复原)必须留在候选集。
    relevance_check 规则1 已按标题缩写判 rag, direction_select 不能把目标论文过滤掉。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.database.base import Base
    from app.models.paper import Paper
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/d5.db")
    sf = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with sf() as session:
        session.add(Paper(original_filename="a.pdf", stored_filename="a.pdf",
                          title="SR paper", research_field="超分辨率", status="ready"))
        session.add(Paper(original_filename="b.pdf", stored_filename="b.pdf",
                          title="PGDUN: Prompt-Guided Deep Unfolding", research_field="图像复原", status="ready"))
        await session.commit()

    class FakeLLM:
        async def ainvoke(self, prompt):
            import json, types
            # LLM 幻觉: 把 PGDUN 问题判成超分辨率方向
            return types.SimpleNamespace(content=json.dumps(
                {"field": "超分辨率", "year_min": None, "year_max": None,
                 "authors": [], "keywords": ["PGDUN"], "language": ""}))

    r = await direction_select_node(
        {"input_text": "PGDUN 的 PGSA 模块怎么实现"},
        {"configurable": {"session_factory": sf, "llm": FakeLLM()}},
    )
    ids = {p.id for p in r["candidates"]}
    assert 2 in ids  # PGDUN 论文必须进入候选集
    await engine.dispose()


async def _make_fields_db(tmp_path, fields):
    """建一个含给定研究方向集合的临时库, 返回 (engine, session_factory)。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.database.base import Base
    from app.models.paper import Paper
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/d6.db")
    sf = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with sf() as session:
        for i, f in enumerate(fields):
            session.add(Paper(original_filename=f"{i}.pdf", stored_filename=f"{i}.pdf",
                              title=f"paper {i}", research_field=f, status="ready"))
        await session.commit()
    return engine, sf


@pytest.mark.asyncio
async def test_direction_select_generic_prefix_not_matched(tmp_path):
    """回归: 问题"你了解图像超分吗"含通用词"图像"(多个方向名的共享前缀),
    不能把方向误判成"图像去雾"; 应命中方向特异性缩写"超分" -> 超分辨率。"""
    engine, sf = await _make_fields_db(tmp_path, ["超分辨率", "图像去雾", "图像复原", "图像编辑"])
    try:
        r = await direction_select_node(
            {"input_text": "你了解图像超分吗"},
            {"configurable": {"session_factory": sf, "llm": None}},
        )
        assert r["filters"]["field"] == "超分辨率"
        ids = {p.id for p in r["candidates"]}
        assert len(ids) == 1  # 只留超分辨率论文
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_direction_select_suffix_cue_matches(tmp_path):
    """方向线索可以是方向名的后缀: "去雾方法" -> 图像去雾。"""
    engine, sf = await _make_fields_db(tmp_path, ["超分辨率", "图像去雾", "图像复原", "图像编辑"])
    try:
        r = await direction_select_node(
            {"input_text": "去雾方法有哪些"},
            {"configurable": {"session_factory": sf, "llm": None}},
        )
        assert r["filters"]["field"] == "图像去雾"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_direction_select_generic_word_alone_no_override(tmp_path):
    """只含共享前缀"图像"而无方向特异线索时, 不得强制覆盖到某个图像方向。"""
    engine, sf = await _make_fields_db(tmp_path, ["超分辨率", "图像去雾", "图像复原", "图像编辑"])
    try:
        r = await direction_select_node(
            {"input_text": "图像处理相关研究"},
            {"configurable": {"session_factory": sf, "llm": None}},
        )
        assert "field" not in r["filters"] or r["filters"]["field"] == ""
    finally:
        await engine.dispose()


