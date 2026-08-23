"""检索 Agent: 规划 → 并行检索 → 去重 → 相关性排序。

LangGraph 4 节点编排; LLM 不可用时降级为直查模式(不阻断搜索)。
"""

import asyncio
import json
import re
import time
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.research.schemas import SearchResult

# 双源: arXiv(预印本) + OpenAlex(正式发表元数据, 补全 arXiv 结果缺失的 venue)
# Semantic Scholar 保留在 searchers 列表但不在默认源中(按需接入)
ALL_SOURCES = ["arxiv", "openalex"]


async def _ainvoke_with_retry(llm: Any, prompt: str, retries: int = 3) -> str:
    """LLM 调用带重试: 连接错误/超时等瞬时故障最多重试 3 次(退避)。"""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            result = await llm.ainvoke(prompt)
            raw = result.content if hasattr(result, "content") else result
            if isinstance(raw, list):
                raw = "".join(str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in raw)
            return str(raw or "")
        except Exception as exc:
            last_exc = exc
            if attempt < retries - 1:
                await asyncio.sleep(1.5 * (attempt + 1))
    raise last_exc if last_exc is not None else RuntimeError("LLM 调用失败")


def _extract_json(text: str) -> dict:
    """从 LLM 输出提取第一个 JSON 对象(容忍代码围栏)。"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("LLM 输出中未找到 JSON")
    return json.loads(match.group(0))


class ResearchState(TypedDict, total=False):
    query: str
    top_k: int
    fetch_depth: int  # 每组合抓取条数(缓存构建时 > top_k), 缺省用 top_k
    offset: int
    last_total: int | None
    year_min: int | None
    year_max: int | None
    queries: list[str]
    sources: list[str]
    direct: bool
    results: list[SearchResult]
    total: int
    failed_combos: int


PLAN_PROMPT = (
    "你是学术文献检索规划器。把用户的科研需求拆成 1-3 组英文检索词(面向 arXiv/OpenAlex 等学术源), "
    "并选择数据源。只输出合法 JSON: "
    '{"queries": ["..."], "sources": ["arxiv", "openalex"]}'
)

GRADING_PROMPT = (
    "你是学术文献相关性筛选器。给定用户需求与候选论文, 逐条判定相关级别:\n"
    "- perfect: 完全符合(主题直接相关, 满足需求的明显条件)\n"
    "- partial: 部分相关(与主题沾边但不完全匹配)\n"
    "- irrelevant: 无关(即使被检索到也与需求无关, 如『图像编辑』查询下的图像篡改检测/篡改定位)\n"
    '只输出合法 JSON: {"results": [{"index": 0, "level": "perfect"}]}'
)

# total 仅为估计上界(各组合 max); arXiv 分词检索后 totalResults 达几十万,
# 显示时钳制到该值, 避免"共约 43 万条"式无意义数字(翻页由前端按已加载结果钳制)
TOTAL_DISPLAY_CAP = 10000

# LLM 逐条分级(WisPaper 式): 相关集 ≤ RANK_GRADE_MAX 时逐条判定 perfect/partial/irrelevant,
# 硬过滤 irrelevant(不足 RANK_MIN_KEEP 时保留, 防空结果); 分批调用控制 token/延迟。
RANK_GRADE_BATCH = 20   # 每批 LLM 判定的论文数
RANK_GRADE_MAX = 60     # 超过该条数跳过 LLM 分级
RANK_MIN_KEEP = 20      # 过滤后至少保留条数(不足则不过滤)

# 结果集缓存(Google Scholar 式分页): 首次搜索深度抓取构建有序结果集并缓存,
# 翻页直接按 offset 切片返回 —— 同页内容稳定、响应秒开; 缓存不足时按需扩展;
# 前端「搜索/搜索新结果」带 refresh=true 强制重查。
RESULT_CACHE_TTL = 1800.0   # 30 分钟
RESULT_CACHE_MAX = 16       # 最多缓存查询数(超出淘汰最旧)
# 构建缓存时每个 (源×查询) 组合的抓取深度 = top_k × 该倍数。
# 深度 5(每组合 100 条)确保 OpenAlex 相关度窗口能覆盖经典论文
# (实测 EDSR 在 super resolution 相关度第 93 位, 深度 3 的 60 条窗口会漏掉)。
CACHE_PREFETCH_MULT = 5

# 规划缓存 TTL/容量(实例级 dict, 见 __init__): 分页(offset>0)会以同 query 反复
# 请求, 每次重跑 LLM 规划是翻页卡顿主因之一; TTL 内复用首页规划, 避免每页重复
# LLM 调用(LLM 恢复后 TTL 过期即重新规划)。
_PLAN_CACHE_TTL = 300.0
_PLAN_CACHE_MAX = 64


class SearchAgent:
    """检索 Agent(可注入 llm 与 searchers 便于测试)。"""

    def __init__(self, llm: Any, searchers: Any | None = None, proxy: str = "", timeout: float = 15.0):
        self.llm = llm
        if searchers is None:
            from app.research.searchers import ArxivSearcher, OpenAlexSearcher, SemanticScholarSearcher

            searchers = [
                ArxivSearcher(timeout=timeout, proxy=proxy),
                OpenAlexSearcher(timeout=timeout, proxy=proxy),
                SemanticScholarSearcher(timeout=timeout, proxy=proxy),
            ]
        if not isinstance(searchers, (list, tuple)):
            searchers = [searchers]  # 单个 searcher 实例也归一化为列表
        self.searchers = searchers
        self.proxy = proxy
        self.timeout = timeout
        self._graph = self._build_graph()
        # 实例级规划缓存: (query, year_min, year_max) -> (fetched_at, (queries, sources), direct)
        self._plan_cache: dict[tuple[str, int | None, int | None], tuple[float, tuple[list[str], list[str]], bool]] = {}
        # 实例级结果集缓存: (query, year_min, year_max) ->
        #   (fetched_at, 有序全量列表, total估计, queries, sources, direct)
        self._result_cache: dict[tuple[str, int | None, int | None], tuple[float, list[SearchResult], int, list[str], list[str], bool]] = {}

    # ---------- 节点 ----------

    async def _plan_query(self, state: ResearchState) -> ResearchState:
        query = state["query"]
        cache_key = (query, state.get("year_min"), state.get("year_max"))
        hit = self._plan_cache.get(cache_key)
        if hit is not None and time.monotonic() - hit[0] < _PLAN_CACHE_TTL:
            queries, sources = hit[1]
            return {**state, "queries": queries, "sources": sources, "direct": hit[2]}
        try:
            raw = await _ainvoke_with_retry(self.llm, PLAN_PROMPT + f"\n用户需求: {query}")
            payload = _extract_json(raw)
            filtered = [str(q).strip() for q in (payload.get("queries") or [query]) if str(q).strip()]
            queries = filtered or [query]
            sources = [s for s in (payload.get("sources") or ALL_SOURCES) if s in ALL_SOURCES] or ALL_SOURCES
            self._plan_cache[cache_key] = (time.monotonic(), (queries, sources), False)
            if len(self._plan_cache) > _PLAN_CACHE_MAX:
                self._plan_cache.clear()  # 简单淘汰: 查询空间小, 重建代价可忽略
            return {**state, "queries": queries, "sources": sources, "direct": False}
        except Exception:
            # 直查模式也缓存: LLM 故障期间翻页避免每页重试 3 次的超时惩罚
            self._plan_cache[cache_key] = (time.monotonic(), ([query], ALL_SOURCES), True)
            if len(self._plan_cache) > _PLAN_CACHE_MAX:
                self._plan_cache.clear()
            return {**state, "queries": [query], "sources": ALL_SOURCES, "direct": True}

    async def _parallel_search(self, state: ResearchState) -> ResearchState:
        # top_k 语义: 每页条数(不再按 源数×查询数 平均抽样), offset 来自 state;
        # fetch_depth 用于缓存构建时加深抓取(> top_k), 缺省回落 top_k
        per_page = state.get("fetch_depth") or state.get("top_k", 20)
        offset = state.get("offset", 0)
        year_min = state.get("year_min")
        year_max = state.get("year_max")
        # I2: offset 越界短路 — 已知上次 total 且 offset >= total 时不再发请求(防空翻页/API 400)
        last_total = state.get("last_total")
        if offset > 0 and last_total is not None and offset >= last_total:
            return {**state, "results": [], "total": last_total, "failed_combos": 0}
        sources: list[Any] = []
        for name in state["sources"]:
            match = next(
                (s for s in self.searchers if getattr(s, "SOURCE_NAME", None) == name),
                None,
            )
            if match:
                sources.append(match)
        if not sources:
            sources = list(self.searchers)

        async def fetch(source, q):
            try:
                # 年份参数仅在给出时透传: 兼容未实现年份过滤的 searcher(如 S2)
                kwargs = {}
                if year_min is not None:
                    kwargs["year_min"] = year_min
                if year_max is not None:
                    kwargs["year_max"] = year_max
                items, combo_total = await asyncio.wait_for(
                    source.search(q, top_k=per_page, start=offset, **kwargs), timeout=self.timeout
                )
                return items, combo_total, False
            except Exception:
                return [], 0, True  # I1: 单组合失败记录失败数, 不拖垮整批

        tasks = [fetch(s, q) for s in sources for q in state["queries"]]
        batches = await asyncio.gather(*tasks)
        merged: list[SearchResult] = []
        totals: list[int] = []
        failed_combos = 0
        for items, combo_total, is_failed in batches:
            if is_failed:
                failed_combos += 1
                continue
            merged.extend(items)
            totals.append(combo_total)
        total = max(totals, default=0)
        return {
            **state,
            "results": merged,
            "total": min(total, TOTAL_DISPLAY_CAP),
            "failed_combos": failed_combos,
        }

    @staticmethod
    def _dedupe_key(r: SearchResult) -> str:
        """去重 key: DOI 优先, 无 DOI 用归一化标题。"""
        if r.doi:
            return f"doi:{r.doi.lower()}"
        title = re.sub(r"[^a-z0-9]", "", r.title.lower())
        return f"title:{title}"

    @staticmethod
    def _extend_dedupe(base: list[SearchResult], added: list[SearchResult]) -> list[SearchResult]:
        """把新增批次去重追加到已缓存列表(按 DOI/标题去重, 保持 base 顺序)。"""
        seen = {SearchAgent._dedupe_key(r) for r in base}
        out = list(base)
        for r in added:
            k = SearchAgent._dedupe_key(r)
            if k in seen:
                continue
            seen.add(k)
            out.append(r)
        return out

    @staticmethod
    def _richer(a: SearchResult, b: SearchResult) -> SearchResult:
        """两个重复版本选信息更全者: 有被引量(已补全)优先; 都有则 arXiv(带 PDF 直链)优先。

        未补全的 arXiv 预印本(citations=0, venue 空)信息残缺, 若它压过 OpenAlex
        正式版会导致经典论文被误判为预印本而沉底, 故优先保留被引量非零的版本。
        """
        if a.citations > 0 and b.citations <= 0:
            return a
        if b.citations > 0 and a.citations <= 0:
            return b
        if a.source == "arxiv" and b.source != "arxiv":
            return a
        if b.source == "arxiv" and a.source != "arxiv":
            return b
        return b  # 其余: OpenAlex 元数据更全

    @staticmethod
    def _dedupe(results: list[SearchResult]) -> list[SearchResult]:
        # 第一遍: 按 openalex_id 合并(arXiv 的 DOI 常为空而 OpenAlex 的 DOI 非空,
        # 直接走 DOI 去重会漏重); 冲突时保留信息更全的版本
        by_openalex: dict[str, SearchResult] = {}
        rest: list[SearchResult] = []
        for r in results:
            if r.openalex_id:
                cur = by_openalex.get(r.openalex_id)
                if cur is None:
                    by_openalex[r.openalex_id] = r
                else:
                    by_openalex[r.openalex_id] = SearchAgent._richer(cur, r)
            else:
                rest.append(r)
        merged = list(by_openalex.values()) + rest
        # 第二遍: 按归一化标题去重(不用 DOI)——arXiv 版常无 DOI 而 OpenAlex 版有,
        # 用 DOI 作 key 会漏合并这对双版本(缓存里出现两条同论文); 冲突保留更全版本
        by_key: dict[str, SearchResult] = {}
        for r in merged:
            k = f"title:{re.sub(r'[^a-z0-9]', '', r.title.lower())}"
            cur = by_key.get(k)
            if cur is None:
                by_key[k] = r
            else:
                by_key[k] = SearchAgent._richer(cur, r)
        return list(by_key.values())

    @staticmethod
    def _phrase_relevance_filter(results: list[SearchResult], queries: list[str]) -> list[SearchResult]:
        """短语相关度过滤: 只保留标题/摘要包含任一规划检索词(短语)的结果。

        深窗口(100/组合)抓取会带进 OpenAlex 松散匹配的无关高被引论文(如
        "Generalization in Deep Learning" 出现在 super resolution 查询里);
        按规划检索词短语做子串匹配可去除这类噪声。全部不命中时保留原集(防空结果)。
        """
        phrases = [q.strip().lower() for q in queries if q.strip()]
        if not phrases:
            return results

        def _norm(s: str) -> str:
            return (s or "").replace("-", " ").lower()

        def hit(r: SearchResult) -> bool:
            title = _norm(r.title)
            abstract = _norm(r.abstract)
            return any(p in title or p in abstract for p in phrases)

        filtered = [r for r in results if hit(r)]
        return filtered if filtered else results

    @staticmethod
    def _apply_priority(results: list[SearchResult], ranked: list[SearchResult]) -> list[SearchResult]:
        """规则加权稳定排序(Google Scholar 式): 已发表优先 → CCF 级别(A>B>C>None) → 被引量降序。

        纯函数: 以 LLM 排序后的 ranked 为输入, 按 (published, ccf_level, -citations) 分组重排;
        同组内被引量高的经典论文在前(与 Google Scholar 的体验一致), 同被引量保持
        ranked 中的相对顺序(稳定排序)。ranked 未覆盖的结果按 results 原序补齐。
        """
        ccf_rank = {"A": 0, "B": 1, "C": 2}

        def key(r: SearchResult) -> tuple[int, int, int]:
            # 被引量降序: 未收录被引(0)排同组最后
            return (0 if r.published else 1, ccf_rank.get(r.ccf_level, 3), -(r.citations or 0))

        seen: set[int] = {id(r) for r in ranked}
        ordered = list(ranked)
        for r in results:
            if id(r) not in seen:
                ordered.append(r)
                seen.add(id(r))
        return sorted(ordered, key=key)

    async def _enrich(self, state: ResearchState) -> ResearchState:
        """补全节点: 用 OpenAlex 反查 arXiv 结果, 补全正式发表 venue/published/ccf_level。

        批量反查一次请求; 任何失败都不阻断(保留 arXiv 原始 venue)。
        """
        results = state.get("results") or []
        if not any(r.source == "arxiv" for r in results):
            return {**state, "results": results}
        oa = next(
            (s for s in self.searchers if getattr(s, "SOURCE_NAME", None) == "openalex"),
            None,
        )
        if oa is None:
            return {**state, "results": results}
        try:
            enriched = await asyncio.wait_for(oa.enrich_arxiv(results), timeout=self.timeout)
            return {**state, "results": enriched}
        except Exception:
            return {**state, "results": results}  # 补全失败不阻断检索

    async def _grade_batches(self, results: list[SearchResult], query: str) -> dict[int, str]:
        """分批调用 LLM 逐条判定相关级别, 返回 {index: level}。"""
        levels: dict[int, str] = {}
        for start in range(0, len(results), RANK_GRADE_BATCH):
            batch = results[start : start + RANK_GRADE_BATCH]
            listing = "\n".join(
                f"{start + i}. {r.title} ({r.year or '?'}) - {r.abstract[:100]}" for i, r in enumerate(batch)
            )
            raw = await _ainvoke_with_retry(
                self.llm,
                GRADING_PROMPT + f"\n用户需求: {query}\n候选文献:\n{listing}",
            )
            payload = _extract_json(raw)
            for item in payload.get("results") or []:
                try:
                    idx = int(item.get("index", -1))
                except (TypeError, ValueError):
                    continue
                lvl = str(item.get("level", "")).strip().lower()
                if start <= idx < start + len(batch) and lvl in ("perfect", "partial", "irrelevant"):
                    levels[idx] = lvl
        return levels

    async def _relevance_grade(self, state: ResearchState) -> ResearchState:
        """WisPaper 式逐条分级 + 硬过滤无关, 再按 已发表/CCF/被引量 规则排序。

        - 直查/翻页/大结果集(>RANK_GRADE_MAX)跳过 LLM 分级(省 token 且防超时);
        - 分级失败或过滤后不足 RANK_MIN_KEEP 条 → 不过滤, 防空结果;
        - 排序始终遵循用户规则: 已发表优先 → CCF A>B>C → 组内被引量降序。
        """
        results = state.get("results") or []
        if not results:
            return {**state, "results": results}
        if state.get("direct") or state.get("offset", 0) > 0 or len(results) > RANK_GRADE_MAX:
            return {**state, "results": self._apply_priority(results, results)}
        try:
            levels = await self._grade_batches(results, state["query"])
        except Exception:
            levels = {}  # 分级失败: 不过滤
        if levels:
            kept = [r for i, r in enumerate(results) if levels.get(i) != "irrelevant"]
            if len(kept) >= RANK_MIN_KEEP:
                results = kept
        return {**state, "results": self._apply_priority(results, results)}

    # ---------- 图构建 ----------

    def _build_graph(self):
        g = StateGraph(ResearchState)
        g.add_node("plan", self._plan_query)
        g.add_node("search", self._parallel_search)
        g.add_node("enrich", self._enrich)
        g.add_node("dedupe", lambda s: {**s, "results": self._dedupe(s.get("results") or [])})
        g.add_node("rank", self._relevance_grade)
        g.add_edge(START, "plan")
        g.add_edge("plan", "search")
        g.add_edge("search", "enrich")  # 去重前补全: 让 dedupe 能按 openalex_id 合并双源重复
        g.add_edge("enrich", "dedupe")
        g.add_edge("dedupe", "rank")
        g.add_edge("rank", END)
        return g.compile()

    async def run(
        self,
        query: str,
        top_k: int = 20,
        offset: int = 0,
        last_total: int | None = None,
        year_min: int | None = None,
        year_max: int | None = None,
        on_event=None,
        refresh: bool = False,
    ) -> tuple[list[SearchResult], int]:
        """检索并返回 (全量有序结果列表, total)。

        Google Scholar 式分页:
        - 首次搜索(或 refresh=True)深度抓取(每组合 top_k×CACHE_PREFETCH_MULT)构建有序
          结果集并缓存, results 事件携带首页切片(≤top_k 条);
        - 之后同一查询翻页直接按 offset 切片返回 —— 同页内容稳定、响应秒开;
        - 切片越界(offset >= 缓存长度)时按需扩展: 抓取下一窗口并去重追加到缓存;
        - 冷启动翻页(缓存不存在且 offset>0)退化为直接抓取该页, 不写缓存。

        total 为各 (源×查询) 组合 total 的最大值, 是「估计上界」, results 事件以
        total_is_estimate=true 显式标记。全部组合失败时发 error 事件并返回空列表。
        """
        key = (query, year_min, year_max)
        cached = self._result_cache.get(key)
        cache_valid = cached is not None and time.monotonic() - cached[0] < RESULT_CACHE_TTL

        if cache_valid and not refresh and offset < len(cached[1]):
            # 缓存命中: 直接切片, 不重算
            full_list, total = cached[1], cached[2]
            page = full_list[offset : offset + top_k]
            if on_event:
                on_event({"event": "plan", "queries": cached[3], "sources": cached[4], "direct": cached[5]})
                on_event({"event": "results", "items": [r.model_dump() for r in page], "total": total, "offset": offset, "total_is_estimate": True})
            return full_list, total

        if cache_valid and not refresh:
            # 缓存命中但 offset 越界: 按需扩展(抓取下一窗口, 去重追加)
            state: ResearchState = {
                "query": query,
                "top_k": top_k,
                "offset": offset,
                "last_total": last_total,
                "year_min": year_min,
                "year_max": year_max,
            }
            final = await self._graph.ainvoke(state)
            new_results = final.get("results") or []
            new_total = final.get("total", len(new_results))
            new_results = self._phrase_relevance_filter(new_results, cached[3])
            extended = self._extend_dedupe(cached[1], new_results)
            self._result_cache[key] = (time.monotonic(), extended, new_total, cached[3], cached[4], cached[5])
            page = extended[offset : offset + top_k]
            if on_event:
                on_event({"event": "plan", "queries": cached[3], "sources": cached[4], "direct": cached[5]})
                on_event({"event": "results", "items": [r.model_dump() for r in page], "total": new_total, "offset": offset, "total_is_estimate": True})
            return extended, new_total

        # 构建/刷新缓存(offset==0)或冷启动翻页(offset>0): 跑完整管线
        fetch_depth = top_k * CACHE_PREFETCH_MULT if offset == 0 else top_k
        state: ResearchState = {
            "query": query,
            "top_k": top_k,
            "fetch_depth": fetch_depth,
            "offset": offset,
            "last_total": last_total,
            "year_min": year_min,
            "year_max": year_max,
        }
        final = await self._graph.ainvoke(state)
        results = final.get("results") or []
        total = final.get("total", len(results))
        queries = final.get("queries") or [query]
        sources = final.get("sources") or ALL_SOURCES
        failed = final.get("failed_combos", 0)
        if on_event:
            on_event({"event": "plan", "queries": queries, "sources": sources, "direct": bool(final.get("direct"))})
        if failed and failed >= len(queries) * len(sources):
            # I1: 全部组合失败 — 发 error 事件, 不静默返回"没有结果"
            if on_event:
                on_event({"event": "error", "message": "所有检索源均失败，请检查网络或稍后重试"})
            return [], 0
        # 短语相关度过滤: 去除深窗口带进的无关高被引噪声
        results = self._phrase_relevance_filter(results, queries)
        direct = bool(final.get("direct"))
        if offset == 0 and not direct:
            # 只缓存正常规划的结果; 直查模式(LLM 规划失败)结果是降级产出,
            # 缓存会把瞬时故障冻结 30 分钟(坏结果反复命中), 故跳过
            self._result_cache[key] = (time.monotonic(), results, total, queries, sources, direct)
            if len(self._result_cache) > RESULT_CACHE_MAX:
                self._result_cache.pop(next(iter(self._result_cache)))  # 淘汰最旧
        page = results[:top_k]
        if on_event:
            on_event({"event": "results", "items": [r.model_dump() for r in page], "total": total, "offset": offset, "total_is_estimate": True})
        return results, total

    async def aclose(self) -> None:
        """关闭所有自带连接池的 searcher(注入的 searcher 由调用方管理生命周期)。"""
        for s in self.searchers:
            close = getattr(s, "aclose", None)
            if close is not None:
                await close()
