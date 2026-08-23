"""SearchAgent 编排测试(假 LLM + 假 searcher)。"""

import pytest

from app.research.agent import ALL_SOURCES, SearchAgent
from app.research.schemas import SearchResult


class FakeLLM:
    """按 prompt 内容返回不同的 JSON(plan / grading / 兼容旧 rank)。"""

    def __init__(self, plan_json: str, rank_json: str):
        self.plan_json = plan_json
        self.rank_json = rank_json
        self.grade_json = None  # 设置后用于相关性分级 prompt

    async def ainvoke(self, prompt: str):
        if "检索规划" in prompt:
            return type("Msg", (), {"content": self.plan_json})()
        if "相关性筛选" in prompt and self.grade_json is not None:
            return type("Msg", (), {"content": self.grade_json})()
        return type("Msg", (), {"content": self.rank_json})()


class CountingLLM(FakeLLM):
    """额外统计 rank 调用次数(断言大结果集跳过 LLM 排序)。"""

    def __init__(self, plan_json: str, rank_json: str):
        super().__init__(plan_json, rank_json)
        self.rank_calls = 0

    async def ainvoke(self, prompt: str):
        if "检索规划" not in prompt:
            self.rank_calls += 1
        return await super().ainvoke(prompt)


class FailingLLM(FakeLLM):
    def __init__(self):
        super().__init__(plan_json="{}", rank_json="{}")

    async def ainvoke(self, prompt: str):
        raise RuntimeError("llm down")


def make_results() -> list[SearchResult]:
    return [
        SearchResult(source="arxiv", title="Paper A", authors=[], abstract="about attention", page_url="u1", pdf_url="p1", year=2024),
        SearchResult(source="semantic_scholar", title="Paper A", authors=[], abstract="duplicate", page_url="u2", year=2024),  # 标题重复
        SearchResult(source="arxiv", title="Paper B", authors=[], abstract="about super resolution", page_url="u3"),
    ]


class FakeSearchers:
    async def search(self, query, top_k, start=0):
        return make_results(), 3


class FakeArxivSearcher:
    """具名假 searcher: 匹配靠 SOURCE_NAME 类属性, 与类名无关。"""

    SOURCE_NAME = "arxiv"

    def __init__(self):
        self.calls: list[str] = []

    async def search(self, query, top_k, start=0):
        self.calls.append(query)
        return [SearchResult(source="arxiv", title="Arxiv Hit", authors=[], abstract="a", page_url="ua1", year=2024)], 1


class FakeS2Searcher:
    SOURCE_NAME = "semantic_scholar"

    def __init__(self):
        self.calls: list[str] = []

    async def search(self, query, top_k, start=0):
        self.calls.append(query)
        return [SearchResult(source="semantic_scholar", title="S2 Hit", authors=[], abstract="b", page_url="us1", year=2024)], 1


# ---------- 原有: 编排 / LLM 故障降级 ----------

@pytest.mark.asyncio
async def test_agent_plan_dedupe_grade():
    """plan → 并行检索 → 去重; 排序遵循 已发表/CCF/被引量 规则(分级不过滤时保持稳定序)。"""
    llm = FakeLLM(
        plan_json='{"queries": ["lightweight super-resolution attention"], "sources": ["arxiv", "semantic_scholar"]}',
        rank_json='{"ranking": []}',
        # 分级 JSON 用旧字段(无 "results" key) → 解析为空 → 不过滤
    )
    agent = SearchAgent(llm=llm, searchers=FakeSearchers())
    events = []
    results, total = await agent.run("轻量超分注意力", top_k=10, on_event=events.append)
    # 去重后 2 条(Paper A、Paper B); 分级未过滤时保持合并稳定序
    assert [r.title for r in results] == ["Paper A", "Paper B"]
    assert total == 3  # 各组合 total 的最大值
    assert any(e["event"] == "plan" for e in events)
    # 契约 §6.1: results 事件须携带 items 载荷且长度与去重后结果一致
    assert any(e["event"] == "results" and len(e.get("items", [])) == 2 for e in events)
    # 契约: results 事件携带 total 与 offset
    assert any(e["event"] == "results" and e.get("total") == 3 and e.get("offset") == 0 for e in events)


@pytest.mark.asyncio
async def test_relevance_grade_filters_irrelevant():
    """WisPaper 式: LLM 判定为 irrelevant 的论文被硬过滤(≥RANK_MIN_KEEP 时), 其余保留。"""
    from app.research.agent import RANK_MIN_KEEP

    llm = FakeLLM(
        plan_json='{"queries": ["image editing"], "sources": ["arxiv"]}',
        rank_json='{"ranking": []}',
    )
    # 21 条: index 0 无关, 其余 perfect → 过滤后 20 条 ≥ RANK_MIN_KEEP
    n = RANK_MIN_KEEP + 1
    grade_entries = [{"index": 0, "level": "irrelevant"}] + [{"index": i, "level": "perfect"} for i in range(1, n)]
    llm.grade_json = '{"results": ' + str(grade_entries).replace("'", '"') + '}'

    class ManySearcher:
        SOURCE_NAME = "arxiv"

        async def search(self, query, top_k, start=0):
            items = [SearchResult(source="arxiv", title="Image Manipulation Localization", authors=[], abstract="detection", page_url="u0", year=2024)]
            items += [SearchResult(source="arxiv", title=f"Editing Diffusion Models {i}", authors=[], abstract="edit", page_url=f"u{i}", year=2024) for i in range(1, n)]
            return items, 100

    agent = SearchAgent(llm=llm, searchers=ManySearcher())
    results, _ = await agent.run("图像编辑", top_k=10)
    assert len(results) == RANK_MIN_KEEP
    assert all(r.title.startswith("Editing Diffusion Models") for r in results)  # 无关的被剔除


def test_dedupe_prefers_richer_version():
    """未补全的 arXiv 预印本与 OpenAlex 正式版重复时, 保留带被引量的 OpenAlex 版本。"""
    from app.research.agent import SearchAgent

    arxiv_preprint = SearchResult(source="arxiv", title="Enhanced Deep Residual Networks for Single Image Super-Resolution", authors=[], abstract="a", page_url="u1", pdf_url="p1", year=2017, citations=0, published=False, ccf_level=None)
    openalex_pub = SearchResult(source="openalex", title="Enhanced Deep Residual Networks for Single Image Super-Resolution", authors=[], venue="CVPR", abstract="b", page_url="u2", year=2017, citations=615, published=True, ccf_level="A")
    out = SearchAgent._dedupe([arxiv_preprint, openalex_pub])
    assert len(out) == 1
    assert out[0].source == "openalex"
    assert out[0].citations == 615
    assert out[0].published is True
    # 补全后的 arXiv 版本(有被引量)仍优先于 OpenAlex 纯版本(带 PDF 直链)
    arxiv_enriched = SearchResult(source="arxiv", title="Enhanced Deep Residual Networks for Single Image Super-Resolution", authors=[], venue="CVPR", abstract="a", page_url="u1", pdf_url="p1", year=2017, citations=615, published=True, ccf_level="A")
    out2 = SearchAgent._dedupe([openalex_pub, arxiv_enriched])
    assert len(out2) == 1
    assert out2[0].source == "arxiv"


def test_phrase_relevance_filter_removes_noise():
    """深窗口抓取的无关高被引噪声被检索词短语过滤掉, 相关论文保留。"""
    from app.research.agent import SearchAgent

    relevant = SearchResult(source="openalex", title="Enhanced Deep Residual Networks for Single Image Super-Resolution", authors=[], abstract="a", page_url="u1", year=2017, citations=5000, published=True, ccf_level="A")
    noise = SearchResult(source="openalex", title="Generalization in Deep Learning", authors=[], abstract="we study generalization bounds", page_url="u2", year=2017, citations=400, published=True, ccf_level=None)
    kept = SearchAgent._phrase_relevance_filter([relevant, noise], ["super resolution", "image super resolution"])
    assert [r.title for r in kept] == ["Enhanced Deep Residual Networks for Single Image Super-Resolution"]
    # 全部不命中时保留原集(防空结果)
    no_match = SearchAgent._phrase_relevance_filter([noise], ["super resolution"])
    assert len(no_match) == 1


@pytest.mark.asyncio
async def test_apply_priority_sorts_by_citations():
    """Google Scholar 式: 同组(已发表+同 CCF)内按被引量降序, 经典论文优先于 LLM 顺序。"""
    from app.research.agent import SearchAgent

    low = SearchResult(source="openalex", title="Low Cited", authors=[], venue="CVPR", abstract="a", page_url="u1", year=2024, citations=10, published=True, ccf_level="A")
    high = SearchResult(source="openalex", title="High Cited", authors=[], venue="CVPR", abstract="b", page_url="u2", year=2020, citations=5000, published=True, ccf_level="A")
    preprint = SearchResult(source="arxiv", title="Preprint", authors=[], abstract="c", page_url="u3", year=2024, published=False)
    # LLM 顺序: low 在前; 但 citations 排序应把 high 提到同组最前, preprint 仍最后
    results = [low, high, preprint]
    ranked = [low, high, preprint]
    ordered = SearchAgent._apply_priority(results, ranked)
    assert [r.title for r in ordered] == ["High Cited", "Low Cited", "Preprint"]


@pytest.mark.asyncio
async def test_agent_direct_mode_on_llm_failure():
    agent = SearchAgent(llm=FailingLLM(), searchers=FakeSearchers())
    events = []
    results, total = await agent.run("any query", top_k=10, on_event=events.append)
    # LLM 失败 → 直查: 原样按 searcher 顺序返回(去重后 2 条, Paper A 在前)
    assert len(results) == 2
    assert results[0].title == "Paper A"
    assert total == 3
    assert any(e["event"] == "plan" and e.get("direct") for e in events)
    # 直查模式 results 事件同样须携带 items 载荷
    assert any(e["event"] == "results" and len(e.get("items", [])) == 2 for e in events)


class FakeHugeTotalSearcher:
    """combo_total 极大(模拟 arXiv 分词检索的几十万 totalResults)。"""

    SOURCE_NAME = "arxiv"

    async def search(self, query, top_k, start=0):
        return [SearchResult(source="arxiv", title="Hit", authors=[], abstract="a", page_url="u1", year=2024)], 999_999


@pytest.mark.asyncio
async def test_agent_total_capped_for_display():
    """total 钳制到 TOTAL_DISPLAY_CAP, 避免展示"共约 43 万条"式无意义数字。"""
    from app.research.agent import TOTAL_DISPLAY_CAP

    agent = SearchAgent(llm=FailingLLM(), searchers=FakeHugeTotalSearcher())
    events = []
    results, total = await agent.run("q", top_k=10, on_event=events.append)
    assert len(results) == 1
    assert total == TOTAL_DISPLAY_CAP
    assert any(e["event"] == "results" and e.get("total") == TOTAL_DISPLAY_CAP for e in events)


# ---------- Critical 1: source→searcher 名称匹配(SOURCE_NAME 属性) ----------

@pytest.mark.asyncio
async def test_source_mapping_filters_to_available_sources():
    """ALL_SOURCES=["arxiv","openalex"]: plan 请求的 semantic_scholar 被过滤, 不查 S2。"""
    llm = FakeLLM(
        plan_json='{"queries": ["q1"], "sources": ["arxiv", "semantic_scholar"]}',
        rank_json='{"ranking": []}',
    )
    arxiv = FakeArxivSearcher()
    s2 = FakeS2Searcher()
    agent = SearchAgent(llm=llm, searchers=[arxiv, s2])
    await agent.run("q", top_k=10)
    assert arxiv.calls == ["q1"]
    assert s2.calls == []  # S2 本次不在 ALL_SOURCES, 不得被查询


@pytest.mark.asyncio
async def test_source_mapping_unknown_source_falls_back_to_all():
    """plan 只请求不可用源 semantic_scholar → 过滤为空 → 回退 ALL_SOURCES(["arxiv","openalex"])。"""
    llm = FakeLLM(
        plan_json='{"queries": ["q1"], "sources": ["semantic_scholar"]}',
        rank_json='{"ranking": []}',
    )
    arxiv = FakeArxivSearcher()
    s2 = FakeS2Searcher()
    agent = SearchAgent(llm=llm, searchers=[arxiv, s2])
    await agent.run("q", top_k=10)
    assert arxiv.calls == ["q1"]  # 回退到 ALL_SOURCES 后只查 arxiv
    assert s2.calls == []


# ---------- Important 3: LLM 返回空查询串时回退到原 query ----------

@pytest.mark.asyncio
async def test_empty_queries_falls_back():
    llm = FakeLLM(
        plan_json='{"queries": [""], "sources": ["arxiv", "semantic_scholar"]}',
        rank_json='{"ranking": []}',
    )
    agent = SearchAgent(llm=llm, searchers=FakeSearchers())
    events = []
    results, total = await agent.run("any query", top_k=10, on_event=events.append)
    assert len(results) == 2  # 空查询串不得清空结果
    assert total == 3
    assert events[0]["queries"] == ["any query"]


# ---------- Important 4: 排序 index 去重 ----------

@pytest.mark.asyncio
async def test_duplicate_rank_index_deduped():
    llm = FakeLLM(
        plan_json='{"queries": ["q1"], "sources": ["arxiv", "semantic_scholar"]}',
        rank_json='{"ranking": [{"index": 1, "score": 9}, {"index": 1, "score": 9}, {"index": 0, "score": 5}]}',
    )
    agent = SearchAgent(llm=llm, searchers=FakeSearchers())
    results, total = await agent.run("q", top_k=10)
    assert len(results) == 2  # 去重后不重复(旧排名路径退化时也不重复)
    assert total == 3
    assert [r.title for r in results] == ["Paper A", "Paper B"]  # 分级不过滤时保持合并稳定序


# ---------- Important 6: 单源故障隔离 ----------

@pytest.mark.asyncio
async def test_single_source_failure_isolated():
    class BoomSearcher:
        SOURCE_NAME = "arxiv"

        async def search(self, query, top_k, start=0):
            raise RuntimeError("source down")

    class GoodSearcher:
        SOURCE_NAME = "semantic_scholar"

        def __init__(self):
            self.calls: list[str] = []

        async def search(self, query, top_k, start=0):
            self.calls.append(query)
            return [SearchResult(source="semantic_scholar", title="S2 Only", authors=[], abstract="ok", page_url="us1", year=2024)], 7

    llm = FakeLLM(
        plan_json='{"queries": ["q1"], "sources": ["arxiv", "semantic_scholar"]}',
        rank_json='{"ranking": []}',
    )
    good = GoodSearcher()
    agent = SearchAgent(llm=llm, searchers=[BoomSearcher(), good])
    results, total = await agent.run("q", top_k=10)
    # arxiv 故障被隔离 → 空结果; S2 不在 ALL_SOURCES, 不被兜底调用
    assert results == []
    assert total == 0
    assert good.calls == []


# ---------- 分页: offset 透传 / 大结果集跳过 LLM 排序 ----------

@pytest.mark.asyncio
async def test_offset_passthrough():
    """run(offset=20) → 每个组合以 start=20 调用 searcher, total 取各组合最大值。"""
    class OffsetSearcher:
        SOURCE_NAME = "arxiv"

        def __init__(self):
            self.starts: list[int] = []

        async def search(self, query, top_k, start=0):
            self.starts.append(start)
            return [SearchResult(source="arxiv", title="P", authors=[], abstract="a", page_url="u", year=2024)], 50

    llm = FakeLLM(
        plan_json='{"queries": ["q1"], "sources": ["arxiv"]}',
        rank_json='{"ranking": []}',
    )
    s = OffsetSearcher()
    agent = SearchAgent(llm=llm, searchers=s)
    events = []
    results, total = await agent.run("q", top_k=20, offset=20, on_event=events.append)
    assert s.starts == [20]
    assert total == 50
    assert len(results) == 1
    # results 事件携带本次 offset 与 total
    assert any(e["event"] == "results" and e.get("offset") == 20 and e.get("total") == 50 for e in events)


@pytest.mark.asyncio
async def test_large_results_skip_llm_grade():
    """超过 RANK_GRADE_MAX(60)条结果跳过 LLM 分级(省 token 且防超时), 保持规则排序。"""
    class BigSearcher:
        SOURCE_NAME = "arxiv"

        async def search(self, query, top_k, start=0):
            results = [
                SearchResult(source="arxiv", title=f"Paper {i}", authors=[], abstract="a", page_url=f"u{i}", year=2024)
                for i in range(70)
            ]
            return results, 100

    llm = CountingLLM(
        plan_json='{"queries": ["q1"], "sources": ["arxiv"]}',
        rank_json='{"ranking": [{"index": 0, "score": 9}]}',
    )
    agent = SearchAgent(llm=llm, searchers=BigSearcher())
    results, total = await agent.run("q", top_k=20)
    assert len(results) == 70
    assert total == 100
    assert llm.rank_calls == 0  # 大结果集不得调用 LLM 分级
    assert [r.title for r in results[:3]] == ["Paper 0", "Paper 1", "Paper 2"]  # 保持原序


class CountingPlanLLM(FakeLLM):
    """统计 plan 调用次数(断言分页复用首页规划)。"""

    def __init__(self, plan_json: str, rank_json: str):
        super().__init__(plan_json, rank_json)
        self.plan_calls = 0

    async def ainvoke(self, prompt: str):
        if "检索规划" in prompt:
            self.plan_calls += 1
        return await super().ainvoke(prompt)


class SingleHitSearcher:
    """返回 1 条结果的简单 searcher(offset 透传)。"""

    SOURCE_NAME = "arxiv"

    async def search(self, query, top_k, start=0):
        return [SearchResult(source="arxiv", title="P", authors=[], abstract="a", page_url="u", year=2024)], 100


class FixedListSearcher:
    """返回固定列表的 searcher, 统计调用次数(缓存行为测试用)。"""

    SOURCE_NAME = "arxiv"

    def __init__(self, count: int = 25):
        self.count = count
        self.calls = 0

    async def search(self, query, top_k, start=0):
        self.calls += 1
        items = [
            SearchResult(source="arxiv", title=f"Paper {i}", authors=[], abstract="a", page_url=f"u{i}", year=2024)
            for i in range(self.count)
        ]
        return items, 100


@pytest.mark.asyncio
async def test_result_cache_same_page_stable():
    """Google Scholar 式: 同查询同页再次请求 → 直接切片返回, 不重跑管线。"""
    llm = CountingLLM(
        plan_json='{"queries": ["q1"], "sources": ["arxiv"]}',
        rank_json='{"ranking": []}',
    )
    s = FixedListSearcher(count=25)
    agent = SearchAgent(llm=llm, searchers=s)
    r1, _ = await agent.run("q", top_k=10, offset=0)
    assert len(r1) == 25  # run 返回全量有序列表
    assert s.calls == 1
    r2, _ = await agent.run("q", top_k=10, offset=0)
    assert s.calls == 1  # 命中缓存, 不再调用 searcher
    assert [x.title for x in r1] == [x.title for x in r2]


@pytest.mark.asyncio
async def test_result_cache_slices_pages():
    """缓存命中翻页: 结果事件只带当前页切片(≤top_k), searcher 不再调用。"""
    llm = CountingLLM(
        plan_json='{"queries": ["q1"], "sources": ["arxiv"]}',
        rank_json='{"ranking": []}',
    )
    s = FixedListSearcher(count=25)
    agent = SearchAgent(llm=llm, searchers=s)
    events: list[dict] = []
    await agent.run("q", top_k=10, offset=0, on_event=events.append)
    assert s.calls == 1
    res_event = next(e for e in events if e["event"] == "results")
    assert len(res_event["items"]) == 10  # 首页切片
    assert res_event["items"][0]["title"] == "Paper 0"
    events2: list[dict] = []
    await agent.run("q", top_k=10, offset=20, on_event=events2.append)
    assert s.calls == 1  # 仍从缓存切片
    res2 = next(e for e in events2 if e["event"] == "results")
    assert [i["title"] for i in res2["items"]] == ["Paper 20", "Paper 21", "Paper 22", "Paper 23", "Paper 24"]


@pytest.mark.asyncio
async def test_result_cache_refresh_reruns():
    """refresh=True → 重新检索并重建缓存。"""
    llm = CountingLLM(
        plan_json='{"queries": ["q1"], "sources": ["arxiv"]}',
        rank_json='{"ranking": []}',
    )
    s = FixedListSearcher(count=25)
    agent = SearchAgent(llm=llm, searchers=s)
    await agent.run("q", top_k=10, offset=0)
    assert s.calls == 1
    await agent.run("q", top_k=10, offset=0, refresh=True)
    assert s.calls == 2  # 强制重查


@pytest.mark.asyncio
async def test_result_cache_not_stored_on_direct_mode():
    """直查模式(LLM 规划失败)的结果不写缓存: 瞬时故障的坏结果不得冻结复用。"""
    llm = FailingLLM()  # plan 抛错 → direct
    s = FixedListSearcher(count=25)
    agent = SearchAgent(llm=llm, searchers=s)
    await agent.run("q", top_k=10, offset=0)
    assert s.calls == 1
    await agent.run("q", top_k=10, offset=0)  # 无缓存 → 重新执行管线
    assert s.calls == 2


@pytest.mark.asyncio
async def test_result_cache_extends_beyond():
    """offset 越出缓存长度 → 按需扩展(重新抓取并去重追加)。"""
    llm = CountingLLM(
        plan_json='{"queries": ["q1"], "sources": ["arxiv"]}',
        rank_json='{"ranking": []}',
    )
    s = FixedListSearcher(count=25)
    agent = SearchAgent(llm=llm, searchers=s)
    await agent.run("q", top_k=10, offset=0)
    assert s.calls == 1
    full, _ = await agent.run("q", top_k=10, offset=60)  # 60 > 缓存 25 条
    assert s.calls == 2  # 扩展: searcher 再次调用
    assert full[:25][-1].title == "Paper 24"  # 原始 25 条保持在前


@pytest.mark.asyncio
async def test_plan_cached_across_pages():
    """同 query 分页(offset>0)复用首页规划: 第二次起不再调用 LLM 规划。"""
    llm = CountingPlanLLM(
        plan_json='{"queries": ["q1"], "sources": ["arxiv"]}',
        rank_json='{"ranking": []}',
    )
    agent = SearchAgent(llm=llm, searchers=SingleHitSearcher())
    _, _ = await agent.run("same query", top_k=20, offset=0)
    assert llm.plan_calls == 1
    _, _ = await agent.run("same query", top_k=20, offset=20)  # 翻页: 复用缓存
    assert llm.plan_calls == 1
    _, _ = await agent.run("same query", top_k=20, offset=40)
    assert llm.plan_calls == 1
    # 不同 query 触发重新规划
    _, _ = await agent.run("different query", top_k=20, offset=0)
    assert llm.plan_calls == 2


@pytest.mark.asyncio
async def test_pagination_skips_llm_rank():
    """分页(offset>0)跳过 LLM 排序: 规则加权排序仍生效, rank 不再调用。"""
    llm = CountingLLM(
        plan_json='{"queries": ["q1"], "sources": ["arxiv"]}',
        rank_json='{"ranking": [{"index": 0, "score": 9}]}',
    )
    agent = SearchAgent(llm=llm, searchers=SingleHitSearcher())
    results, total = await agent.run("q", top_k=20, offset=20)
    assert len(results) == 1
    assert total == 100
    assert llm.rank_calls == 0  # 分页不得重复 LLM 排序


# ---------- 代码审查修复: I1 全组合失败发 error / I2 offset 越界短路 / I3 total 估计上界 ----------

@pytest.mark.asyncio
async def test_all_combos_fail_emits_error():
    """全部组合抛异常 → error 事件出现, 结果为空(不静默当"无结果")。"""
    class BoomSearcher:
        SOURCE_NAME = "arxiv"

        async def search(self, query, top_k, start=0):
            raise RuntimeError("source down")

    llm = FakeLLM(
        plan_json='{"queries": ["q1"], "sources": ["arxiv"]}',
        rank_json='{"ranking": []}',
    )
    agent = SearchAgent(llm=llm, searchers=BoomSearcher())
    events = []
    results, total = await agent.run("q", top_k=10, on_event=events.append)
    assert results == []
    assert total == 0
    assert any(
        e["event"] == "error" and e["message"] == "所有检索源均失败，请检查网络或稍后重试"
        for e in events
    )


@pytest.mark.asyncio
async def test_partial_combo_failure():
    """一个组合失败一个成功 → 返回成功组合结果, 无 error(保持单源降级语义)。"""
    class FlakySearcher:
        SOURCE_NAME = "arxiv"

        def __init__(self):
            self.calls: list[str] = []

        async def search(self, query, top_k, start=0):
            self.calls.append(query)
            if query == "bad":
                raise RuntimeError("boom")
            return [SearchResult(source="arxiv", title="Good Hit", authors=[], abstract="a", page_url="ug", year=2024)], 5

    llm = FakeLLM(
        plan_json='{"queries": ["bad", "good"], "sources": ["arxiv"]}',
        rank_json='{"ranking": []}',
    )
    s = FlakySearcher()
    agent = SearchAgent(llm=llm, searchers=s)
    events = []
    results, total = await agent.run("q", top_k=10, on_event=events.append)
    assert [r.title for r in results] == ["Good Hit"]
    assert total == 5
    assert s.calls == ["bad", "good"]
    assert not any(e["event"] == "error" for e in events)
    # 部分成功路径的 results 事件同样携带 total_is_estimate
    assert any(e["event"] == "results" and e.get("total_is_estimate") is True for e in events)


@pytest.mark.asyncio
async def test_offset_beyond_total_short_circuits():
    """offset >= last_total 时短路: 不调用 searcher, 返回空页且 total 保持已知值。"""
    class CountingSearcher:
        SOURCE_NAME = "arxiv"

        def __init__(self):
            self.calls = 0

        async def search(self, query, top_k, start=0):
            self.calls += 1
            return [SearchResult(source="arxiv", title="P", authors=[], abstract="a", page_url="u", year=2024)], 42

    llm = FakeLLM(
        plan_json='{"queries": ["q1"], "sources": ["arxiv"]}',
        rank_json='{"ranking": []}',
    )
    s = CountingSearcher()
    agent = SearchAgent(llm=llm, searchers=s)
    events = []
    results, total = await agent.run("q", top_k=20, offset=50, last_total=42, on_event=events.append)
    assert results == []
    assert total == 42  # 已知 total 不因空翻页而丢失
    assert s.calls == 0  # 短路: 未发起任何请求
    assert any(e["event"] == "results" and e.get("total") == 42 for e in events)


@pytest.mark.asyncio
async def test_total_is_estimate_flag():
    """results 事件携带 total_is_estimate: true, 明确 total 为估计上界。"""
    llm = FakeLLM(
        plan_json='{"queries": ["q1"], "sources": ["arxiv"]}',
        rank_json='{"ranking": []}',
    )
    agent = SearchAgent(llm=llm, searchers=FakeSearchers())
    events = []
    results, total = await agent.run("q", top_k=10, on_event=events.append)
    assert total == 3
    assert any(e["event"] == "results" and e.get("total_is_estimate") is True for e in events)


# ---------- Important 5: aclose 释放连接 ----------

@pytest.mark.asyncio
async def test_agent_aclose_closes_searchers():
    closed: list[bool] = []

    class Closable:
        async def aclose(self):
            closed.append(True)

    agent = SearchAgent(llm=None, searchers=[Closable(), Closable()])
    await agent.aclose()
    assert len(closed) == 2

# ---------- 新增: 年份筛选 year_min/year_max 透传 ----------

@pytest.mark.asyncio
async def test_year_params_passthrough_to_searcher():
    """run(year_min=..., year_max=...) → searcher 收到对应参数。"""
    class YearSearcher:
        SOURCE_NAME = "arxiv"

        def __init__(self):
            self.kwargs: list[dict] = []

        async def search(self, query, top_k, start=0, year_min=None, year_max=None):
            self.kwargs.append({"year_min": year_min, "year_max": year_max})
            return [SearchResult(source="arxiv", title="P", authors=[], abstract="a", page_url="u", year=2024)], 1

    llm = FakeLLM(
        plan_json='{"queries": ["q1"], "sources": ["arxiv"]}',
        rank_json='{"ranking": []}',
    )
    s = YearSearcher()
    agent = SearchAgent(llm=llm, searchers=s)
    await agent.run("q", top_k=10, year_min=2020, year_max=2024)
    assert s.kwargs == [{"year_min": 2020, "year_max": 2024}]


@pytest.mark.asyncio
async def test_year_params_none_when_not_provided():
    """未传年份 → searcher 收到 year_min=None, year_max=None。"""
    class YearSearcher:
        SOURCE_NAME = "arxiv"

        def __init__(self):
            self.kwargs: list[dict] = []

        async def search(self, query, top_k, start=0, year_min=None, year_max=None):
            self.kwargs.append({"year_min": year_min, "year_max": year_max})
            return [SearchResult(source="arxiv", title="P", authors=[], abstract="a", page_url="u", year=2024)], 1

    llm = FakeLLM(
        plan_json='{"queries": ["q1"], "sources": ["arxiv"]}',
        rank_json='{"ranking": []}',
    )
    s = YearSearcher()
    agent = SearchAgent(llm=llm, searchers=s)
    await agent.run("q", top_k=10)
    assert s.kwargs == [{"year_min": None, "year_max": None}]

# ---------- OpenAlex: 多源检索 / 发表信息补全 / 去重合并 ----------

def test_all_sources_include_openalex():
    """ALL_SOURCES 恢复双源: arXiv + OpenAlex(S2 仍不参与默认检索)。"""
    assert ALL_SOURCES == ["arxiv", "openalex"]


class FakeOpenAlexSearcher:
    """具名假 searcher: 匹配靠 SOURCE_NAME="openalex"。"""

    SOURCE_NAME = "openalex"

    def __init__(self):
        self.calls: list[str] = []
        self.enriched = False

    async def search(self, query, top_k, start=0):
        self.calls.append(query)
        return [SearchResult(
            source="openalex", title="OpenAlex Hit", authors=[], abstract="c",
            page_url="uo1", year=2024, venue="CVPR", published=True, ccf_level="A",
        )], 1

    async def enrich_arxiv(self, results):
        self.enriched = True
        return results


class FakeArxivSearcherWithId:
    """返回带 arXiv ID 的预印本结果(供补全测试)。"""

    SOURCE_NAME = "arxiv"

    def __init__(self):
        self.calls: list[str] = []

    async def search(self, query, top_k, start=0):
        self.calls.append(query)
        return [SearchResult(
            source="arxiv", title="Lightweight Attention for Super-Resolution",
            authors=[], abstract="a", page_url="https://arxiv.org/abs/2401.12345",
            pdf_url="https://arxiv.org/pdf/2401.12345", year=2024,
        )], 1


@pytest.mark.asyncio
async def test_agent_multi_source_arxiv_openalex_merged():
    """plan 请求 [arxiv, openalex] → 两个 searcher 都被调用, 结果合并。"""
    llm = FakeLLM(
        plan_json='{"queries": ["q1"], "sources": ["arxiv", "openalex"]}',
        rank_json='{"ranking": []}',
    )
    arxiv = FakeArxivSearcher()
    oa = FakeOpenAlexSearcher()
    agent = SearchAgent(llm=llm, searchers=[arxiv, oa])
    results, total = await agent.run("q", top_k=10)
    assert arxiv.calls == ["q1"]
    assert oa.calls == ["q1"]
    assert {r.source for r in results} == {"arxiv", "openalex"}
    assert total == 1


class EnrichingOpenAlex(FakeOpenAlexSearcher):
    """补全实现: 把 arXiv 结果的 venue/published/ccf_level/openalex_id 填上。"""

    async def enrich_arxiv(self, results):
        self.enriched = True
        for r in results:
            if r.source == "arxiv":
                r.venue = "CVPR 2024"
                r.published = True
                r.ccf_level = "A"
                r.openalex_id = "W2741809807"
        return results


@pytest.mark.asyncio
async def test_agent_enrichment_updates_arxiv_venue():
    """enrich 节点调用 OpenAlex searcher 补全 arXiv 结果(预印本 → 已发表)。"""
    llm = FakeLLM(
        plan_json='{"queries": ["q1"], "sources": ["arxiv", "openalex"]}',
        rank_json='{"ranking": []}',
    )
    oa = EnrichingOpenAlex()
    agent = SearchAgent(llm=llm, searchers=[FakeArxivSearcherWithId(), oa])
    results, total = await agent.run("q", top_k=10)
    assert oa.enriched is True
    assert total == 1
    arxiv_hit = next(r for r in results if r.source == "arxiv")
    assert arxiv_hit.venue == "CVPR 2024"
    assert arxiv_hit.published is True
    assert arxiv_hit.ccf_level == "A"
    assert arxiv_hit.openalex_id == "W2741809807"


@pytest.mark.asyncio
async def test_agent_enrichment_failure_does_not_block():
    """补全节点抛异常 → 保留 arXiv 原始结果, 检索不失败。"""
    class BoomOpenAlex:
        SOURCE_NAME = "openalex"

        async def search(self, query, top_k, start=0):
            return [], 1

        async def enrich_arxiv(self, results):
            raise RuntimeError("openalex down")

    llm = FakeLLM(
        plan_json='{"queries": ["q1"], "sources": ["arxiv", "openalex"]}',
        rank_json='{"ranking": []}',
    )
    agent = SearchAgent(llm=llm, searchers=[FakeArxivSearcherWithId(), BoomOpenAlex()])
    results, total = await agent.run("q", top_k=10)
    assert total == 1
    assert len(results) == 1
    assert results[0].source == "arxiv"
    assert results[0].venue == ""  # 补全失败: 保留预印本原值
    assert results[0].published is False


def test_dedupe_prefers_enriched_arxiv_over_openalex():
    """arXiv 补全版(openalex_id 相同)优先于纯 OpenAlex 重复项(即使 DOI 不同)。"""
    arxiv_enriched = SearchResult(
        source="arxiv", title="Same Paper", authors=[], page_url="ua",
        venue="CVPR 2024", published=True, ccf_level="A",
        openalex_id="W2741809807", year=2024,
    )
    oa_dup = SearchResult(
        source="openalex", title="Same Paper", authors=[], page_url="uo",
        venue="CVPR 2024", published=True, ccf_level="A",
        openalex_id="W2741809807", doi="10.1000/real.doi", year=2024,
    )
    # OpenAlex 在前也不影响: 补全后的 arXiv 版本胜出
    out = SearchAgent._dedupe([oa_dup, arxiv_enriched])
    assert len(out) == 1
    assert out[0].source == "arxiv"
    assert out[0].venue == "CVPR 2024"
    assert out[0].openalex_id == "W2741809807"


def test_dedupe_keeps_distinct_openalex_works():
    """openalex_id 不同的 OpenAlex 结果不被误合并(按 DOI/标题去重)。"""
    a = SearchResult(
        source="openalex", title="Paper X", authors=[], page_url="u1",
        venue="ICCV", published=True, ccf_level="A",
        openalex_id="W1", doi="10.1000/a", year=2024,
    )
    b = SearchResult(
        source="openalex", title="Paper Y", authors=[], page_url="u2",
        venue="ICCV", published=True, ccf_level="A",
        openalex_id="W2", doi="10.1000/b", year=2024,
    )
    out = SearchAgent._dedupe([a, b])
    assert len(out) == 2





# ---------- 新增: 规则加权稳定排序 _apply_priority / 弱相关惩罚 ----------

def _mk_rank_result(title: str, published: bool = False, ccf_level: str | None = None, year: int = 2024) -> SearchResult:
    """构造带发表/CCF 元数据的排序测试结果。"""
    return SearchResult(
        source="arxiv", title=title, authors=[], abstract="a",
        page_url="u", year=year, published=published, ccf_level=ccf_level,
    )


def test_rank_priority_published_first():
    """已发表(published=true)论文整体排在预印本之前(即使预印本 CCF 级别更高)。"""
    preprint_a = _mk_rank_result("Preprint A", published=False, ccf_level="A")
    pub_b = _mk_rank_result("Published B", published=True, ccf_level="B")
    pub_none = _mk_rank_result("Published No-CCF", published=True, ccf_level=None)
    results = [preprint_a, pub_b, pub_none]
    ranked = [preprint_a, pub_none, pub_b]  # LLM 认为 preprint_a 最相关
    out = SearchAgent._apply_priority(results, ranked)
    assert [r.title for r in out] == ["Published B", "Published No-CCF", "Preprint A"]


def test_rank_priority_ccf_level():
    """同 published 下 CCF-A > CCF-B > C > None。"""
    a = _mk_rank_result("CCF-A", published=True, ccf_level="A")
    b = _mk_rank_result("CCF-B", published=True, ccf_level="B")
    c = _mk_rank_result("CCF-C", published=True, ccf_level="C")
    n = _mk_rank_result("No-CCF", published=True, ccf_level=None)
    results = [a, b, c, n]
    ranked = [n, c, b, a]  # LLM 顺序与 CCF 级别相反
    out = SearchAgent._apply_priority(results, ranked)
    assert [r.title for r in out] == ["CCF-A", "CCF-B", "CCF-C", "No-CCF"]


def test_rank_priority_preserves_llm_order_within_group():
    """相同 (published, ccf_level) 组内保持 LLM 相关度顺序(稳定排序)。"""
    x = _mk_rank_result("X", published=True, ccf_level="A")
    y = _mk_rank_result("Y", published=True, ccf_level="A")
    z = _mk_rank_result("Z", published=False, ccf_level=None)
    results = [x, y, z]
    ranked = [z, x, y]  # LLM: Z 最相关, 其次 X, Y
    out = SearchAgent._apply_priority(results, ranked)
    # 已发表 A 组内保持 [X, Y] 的 LLM 顺序; 未发表 Z 排最后
    assert [r.title for r in out] == ["X", "Y", "Z"]


@pytest.mark.asyncio
async def test_rank_weaker_relevance_penalized():
    """搜『图像编辑』: mock LLM 把弱相关(篡改检测类)排最后 → 最终结果弱相关论文仍在最后。"""

    class EditSearcher:
        SOURCE_NAME = "arxiv"

        async def search(self, query, top_k, start=0):
            return [
                _mk_rank_result("AIGC Image Editing with Diffusion Priors", published=True, ccf_level="A", year=2024),
                _mk_rank_result("Image Manipulation Localization via CNN", published=True, ccf_level="A", year=2024),
            ], 2

    llm = FakeLLM(
        plan_json='{"queries": ["image editing diffusion"], "sources": ["arxiv"]}',
        # 弱相关(篡改检测类)论文被 LLM 排到最后
        rank_json='{"ranking": [{"index": 0, "score": 10}, {"index": 1, "score": 1}]}',
    )
    agent = SearchAgent(llm=llm, searchers=EditSearcher())
    results, total = await agent.run("图像编辑", top_k=10)
    assert total == 2
    assert [r.title for r in results] == [
        "AIGC Image Editing with Diffusion Priors",
        "Image Manipulation Localization via CNN",
    ]
