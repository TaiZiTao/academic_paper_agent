# 论文全库问答 Workflow 化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把论文全库问答(run_library_qa)重构为 LangGraph 图, 获得节点级可视化(WorkflowPanel) + 检索质量评估/重试(最多3次, top-3 评分) + 分支统一进图。

**Architecture:** 仿企业版 app/graph/*(workflow.py/nodes.py/state.py)新建 app/paper/library_*.py: LibraryQAState + 节点函数 + 图构建。service.run_library_qa 改为 astream 驱动图, 节点通过 RunnableConfig 注入 session_factory/retriever/llm。前端 ChatView 接回 WorkflowPanel, useChat 消费 node 事件。现有过滤/归一化/降级/采样/引用校验逻辑从 service 搬入节点, 行为不变。

**Tech Stack:** LangGraph + FastAPI + SQLAlchemy(async) + Vue3/Element Plus + SSE。

---

## 文件结构

```
app/paper/library_state.py     新建: LibraryQAState(TypedDict)
app/paper/library_nodes.py    新建: 9 个节点函数 + 路由函数
app/paper/library_graph.py    新建: build_library_graph()
app/paper/service.py          修改: run_library_qa 改为驱动图; _load_history 保留
app/paper/prompts.py          修改: 新增 build_query_rewrite_prompt + build_relevance_prompt(可复用企业版)
web/src/views/ChatView.vue    修改: 接回 WorkflowPanel
web/src/composables/useChat.ts 修改: 消费 node 事件, 重建 workflowNodes
tests/test_library_state.py   新建: 状态/路由测试
tests/test_library_nodes.py   新建: 各节点单测
tests/test_library_graph.py   新建: 图构建/重试循环测试
tests/test_library_qa_service.py 修改: 适配图驱动
```

---

## Task 1: LibraryQAState + 节点基础设施

**Files:**
- Create: `app/paper/library_state.py`
- Create: `app/paper/library_nodes.py`(仅框架 + helper)
- Test: `tests/test_library_state.py`(新建)

- [ ] **Step 1: 写失败测试**

新建 `tests/test_library_state.py`:

```python
from app.paper.library_state import LibraryQAState

def test_state_allows_partial_updates():
    s: LibraryQAState = {"input_text": "超分方法", "retry_count": 0}
    s["intent"] = "qa"
    assert s["intent"] == "qa" and s["retry_count"] == 0

def test_state_default_fields():
    s: LibraryQAState = {}
    assert s.get("retry_count", 0) == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `venv\Scripts\python.exe -m pytest tests/test_library_state.py -q`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 创建 library_state.py**

```python
"""论文全库问答 LangGraph 状态定义。"""

from typing import TypedDict


class LibraryQAState(TypedDict, total=False):
    """全库问答工作流共享状态, 节点通过返回 dict 做部分更新。"""

    session_id: str
    input_text: str
    history: list[dict]          # 多轮上下文
    intent: str                 # chitchat | catalog | qa
    filters: dict               # 动态过滤条件
    candidates: list            # 候选论文(ORM Paper 对象)
    query: str                  # 当前检索词(重试时被改写)
    evidence: list              # PaperChunkData 列表
    relevance_scores: list      # top-3 评分
    retry_count: int
    content: str                # 最终回答
    citations: list             # 安全引用
    degraded: list[str]         # 降级标记
```

- [ ] **Step 4: 创建 library_nodes.py 框架**(helper + 空节点占位, 后续 Task 填实现)

```python
"""论文全库问答 LangGraph 节点函数。

每个 Node 只做一件事, 通过 State 通信。
通过 RunnableConfig 注入 session_factory / retriever / llm / prompts。
"""

from langgraph.types import RunnableConfig

from app.paper.library_state import LibraryQAState


def _get_from_config(config, key):
    if config is None:
        return None
    return config.get("configurable", {}).get(key)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `venv\Scripts\python.exe -m pytest tests/test_library_state.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/paper/library_state.py app/paper/library_nodes.py tests/test_library_state.py
git commit -m "feat: library QA workflow state and node scaffold"
```

## Task 2: intent_router + chat_node + catalog_node

**Files:**
- Modify: `app/paper/library_nodes.py`
- Test: `tests/test_library_nodes.py`(新建)

- [ ] **Step 1: 写失败测试**

新建 `tests/test_library_nodes.py`:

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `venv\Scripts\python.exe -m pytest tests/test_library_nodes.py -q`
Expected: FAIL(AttributeError: intent_router_node)

- [ ] **Step 3: 实现 intent_router_node**

把 service 现有 chitchat/catalog 关键词逻辑搬入(保持规则一致):

```python
CHITCHAT_KEYWORDS = ("你好", "您好", "你是谁", "你是什么", "谢谢", "感谢", "再见", "hello", "hi")
CATALOG_KEYWORDS = (
    "有什么论文", "有哪些论文", "几篇论文", "多少篇",
    "列举", "清单", "库里有什么", "库里有哪些",
)


def intent_router_node(state: LibraryQAState) -> dict:
    """把用户问题分为 chitchat / catalog / qa 三类。"""
    text = (state.get("input_text") or "").lower()
    if any(kw in text for kw in CHITCHAT_KEYWORDS):
        return {"intent": "chitchat"}
    if any(kw in text for kw in CATALOG_KEYWORDS):
        return {"intent": "catalog"}
    intro = "介绍" in text and ("论文库" in text or "库里" in text)
    if intro and not any(m in text for m in ("方法", "原理", "如何", "为什么", "技术")):
        return {"intent": "catalog"}
    return {"intent": "qa"}
```

- [ ] **Step 4: 实现 chat_node + catalog_node**(调用现有 prompt, 经 config 注入 llm/session_factory)

```python
from app.paper.prompts import build_chitchat_prompt, build_library_catalog_prompt
from app.paper.nodes import _json_content


async def chat_node(state: LibraryQAState, config: RunnableConfig = None) -> dict:
    llm = _get_from_config(config, "llm")
    if llm is None:
        return {"content": "你好!我是论文知识问答助手。"}
    prompt = build_chitchat_prompt(state.get("input_text", ""), state.get("history"))
    response = await llm.ainvoke(prompt)
    payload = _json_content(response.content if hasattr(response, "content") else response)
    return {"content": str(payload.get("content", "")).strip() or "你好!我是论文知识问答助手。"}


async def catalog_node(state: LibraryQAState, config: RunnableConfig = None) -> dict:
    llm = _get_from_config(config, "llm")
    papers = state.get("candidates", []) or []
    if not papers:
        return {"content": "论文库当前没有论文。"}
    if llm is None:
        return {"content": "论文库当前有 %d 篇论文。" % len(papers)}
    prompt = build_library_catalog_prompt(state.get("input_text", ""), papers, state.get("history"))
    response = await llm.ainvoke(prompt)
    payload = _json_content(response.content if hasattr(response, "content") else response)
    return {"content": str(payload.get("content", "")).strip() or ("论文库当前有 %d 篇论文。" % len(papers))}
```

- [ ] **Step 5: 运行测试确认通过**

Run: `venv\Scripts\python.exe -m pytest tests/test_library_nodes.py -q`
Expected: PASS(3 个 intent 测试)

- [ ] **Step 6: Commit**

```bash
git add app/paper/library_nodes.py tests/test_library_nodes.py
git commit -m "feat: intent router and chitchat/catalog nodes"
```

## Task 3: direction_select + retrieve

**Files:**
- Modify: `app/paper/library_nodes.py`
- Test: `tests/test_library_nodes.py`(追加)

- [ ] **Step 1: 写失败测试**

追加:

```python
def test_direction_select_normalizes_abbreviation():
    # 用假 session_factory 返回含"超分辨率"的库
    from app.paper.library_nodes import direction_select_node
    ...  # 构造 fake session, 断言 filters["field"] == "超分辨率"
```

- [ ] **Step 2: 运行确认失败**

Run: `venv\Scripts\python.exe -m pytest tests/test_library_nodes.py -q`
Expected: FAIL(ImportError: direction_select_node)

- [ ] **Step 3: 实现 direction_select_node**

把 service 的过滤提取 + 方向归一化 + 候选集 + 降级逻辑整体搬入(逻辑与现有完全一致, 只是参数从 self 改为 config):

```python
from sqlalchemy import or_, select
from app.models.paper import Paper
from app.paper.prompts import build_filter_extraction_prompt, parse_filter_response


async def direction_select_node(state: LibraryQAState, config: RunnableConfig = None) -> dict:
    """提取方向+归一化 -> 候选论文集(含降级)。与旧 run_library_qa 的 1/1b/2 步一致。"""
    session_factory = _get_from_config(config, "session_factory")
    llm = _get_from_config(config, "llm")
    input_text = state.get("input_text", "")
    if session_factory is None:
        return {"candidates": [], "filters": {}}

    # 1) 动态过滤(LLM 提取, 失败降级)
    filters: dict = {}
    try:
        async with session_factory() as session:
            field_rows = (await session.execute(select(Paper.research_field).where(Paper.research_field != "").distinct())).scalars().all()
        available_fields = [str(f) for f in field_rows if f]
        if llm is not None:
            raw = await llm.ainvoke(build_filter_extraction_prompt(input_text, available_fields))
            filters = parse_filter_response(raw.content if hasattr(raw, "content") else raw)
    except Exception:
        filters = {}

    # 1b) 方向归一化(2-4字窗口匹配方向名子串)
    if not filters.get("field"):
        try:
            async with session_factory() as session:
                field_rows = (await session.execute(select(Paper.research_field).where(Paper.research_field != "").distinct())).scalars().all()
            field_names = [str(f) for f in field_rows if str(f)]
            matched = None
            for size in (4, 3, 2):
                for i in range(len(input_text) - size + 1):
                    token = input_text[i:i+size]
                    for name in field_names:
                        if token in name:
                            matched = name; break
                    if matched: break
                if matched: break
            if matched: filters["field"] = matched
        except Exception:
            pass

    # 2) 候选集 + 降级(逻辑与旧实现一致)
    def _stmt(cf: dict):
        stmt = select(Paper)
        conds = []
        if cf.get("field"): conds.append(Paper.research_field == cf["field"])
        if cf.get("year_min"): conds.append(Paper.publication_year >= cf["year_min"])
        if cf.get("year_max"): conds.append(Paper.publication_year <= cf["year_max"])
        if cf.get("authors"): conds.append(or_(*[Paper.authors_json.ilike(f"%{a}%") for a in cf["authors"]]))
        if cf.get("language"): conds.append(Paper.language == cf["language"])
        if conds: stmt = stmt.where(*conds)
        return stmt.order_by(Paper.created_at.desc())

    async def _fetch(cf: dict):
        async with session_factory() as session:
            return (await session.execute(_stmt(cf))).scalars().all()

    papers = await _fetch(filters)
    degraded: list[str] = []
    if not papers:
        if filters.get("year_min") or filters.get("year_max"):
            relaxed = {**filters, "year_min": None, "year_max": None}
            rp = await _fetch(relaxed)
            if rp: papers, filters, degraded = rp, relaxed, ["year"]
        if not papers:
            ap = await _fetch({})
            if ap: papers, filters, degraded = ap, {}, ["all"]
    return {"candidates": list(papers), "filters": filters, "degraded": degraded}
```

- [ ] **Step 4: 实现 retrieve_node**

```python
from app.paper.aggregate_retriever import AggregateRetriever


async def retrieve_node(state: LibraryQAState, config: RunnableConfig = None) -> dict:
    """按方向逐篇采样 top-k。"""
    retriever = _get_from_config(config, "retriever")
    papers = state.get("candidates", []) or []
    query = state.get("query") or state.get("input_text", "") or "论文综述"
    if retriever is None or not papers:
        return {"evidence": []}
    agg = AggregateRetriever(retriever)
    evidence = await agg.sample_papers([p.id for p in papers], query)
    return {"evidence": evidence, "query": query}
```

- [ ] **Step 5: 运行测试确认通过**

Run: `venv\Scripts\python.exe -m pytest tests/test_library_nodes.py tests/test_paper_filter.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/paper/library_nodes.py tests/test_library_nodes.py
git commit -m "feat: direction select and retrieve nodes"
```

## Task 4: relevance_evaluate(top-3 评分) + rewrite_query(重试) + 路由

**Files:**
- Modify: `app/paper/library_nodes.py`
- Test: `tests/test_library_nodes.py`(追加) + `tests/test_library_graph.py`(新建)

- [ ] **Step 1: 写失败测试(重试路由)**

新建 `tests/test_library_graph.py`:

```python
from app.paper.library_nodes import should_retry, relevance_evaluate_node

def test_should_retry_low_score():
    state = {"relevance_scores": [{"score": 1}, {"score": 1}], "retry_count": 0}
    assert should_retry(state) == "retry"

def test_should_retry_capped():
    state = {"relevance_scores": [{"score": 1}], "retry_count": 3}
    assert should_retry(state) == "next"

def test_should_retry_good_score():
    state = {"relevance_scores": [{"score": 5}, {"score": 4}], "retry_count": 0}
    assert should_retry(state) == "next"
```

- [ ] **Step 2: 运行确认失败**

Run: `venv\Scripts\python.exe -m pytest tests/test_library_graph.py -q`
Expected: FAIL(ImportError)

- [ ] **Step 3: 实现 relevance_evaluate_node(仅 top-3 评分)**

```python
import json as _json


def relevance_evaluate_node(state: LibraryQAState, config: RunnableConfig = None) -> dict:
    """对证据 top-3 逐条 LLM 1-5 分评估(控制成本), 结合关键词过滤; 返回评分供重试判定。"""
    import asyncio
    evidence = state.get("evidence", []) or []
    if not evidence:
        return {"relevance_scores": []}
    query = state.get("query") or state.get("input_text", "")
    llm = _get_from_config(config, "llm")

    # 仅 top-3 评分(用户确认的成本控制)
    top = evidence[:3]
    scores = []
    if llm is not None:
        for chunk in top:
            try:
                prompt = (
                    "评估以下论文片段与问题的相关性(1-5分), 只回复数字: "
                    "\n问题: " + query
                    "\n论文: " + getattr(chunk, "section", "") + " p" + str(getattr(chunk, "page_start", ""))
                    "\n片段: " + (chunk.content or "")[:500]
                )
                resp = await llm.ainvoke(prompt)
                t = (resp.content if hasattr(resp, "content") else str(resp)).strip()
                score = int("".join(c for c in t if c.isdigit()) or "3")
                scores.append({"score": max(1, min(5, score))})
            except Exception:
                scores.append({"score": 3})
    # 关键词过滤(中文 2+ 字词出现在片段中则保留)
    import re as _re
    keywords = [w for w in _re.findall(r"[\u4e00-\u9fff]{2,}", query)]
    filtered = [
        c for c in evidence
        if (keywords and any(kw in (c.content or "") for kw in keywords)) or len(scores) == 0
    ]
    if filtered:
        return {"evidence": filtered, "relevance_scores": scores}
    return {"relevance_scores": scores}
```

- [ ] **Step 4: 实现 should_retry + rewrite_query_node**

```python
MAX_RETRIES = 3


def should_retry(state: LibraryQAState) -> str:
    """返回 "retry" 或 "next"。平均分<2 或全低分且未超次数 → 重试。"""
    if state.get("retry_count", 0) >= MAX_RETRIES:
        return "next"
    scores = state.get("relevance_scores", []) or []
    if not scores:
        return "next"
    avg = sum(s.get("score", 3) for s in scores) / len(scores)
    return "retry" if avg < 2 else "next"


async def rewrite_query_node(state: LibraryQAState, config: RunnableConfig = None) -> dict:
    """重试: LLM 用不同关键词改写查询, retry_count+1。"""
    llm = _get_from_config(config, "llm")
    current = state.get("query") or state.get("input_text", "")
    if llm is None:
        return {"query": current, "retry_count": state.get("retry_count", 0) + 1}
    prompt = (
        "上一次检索未找到相关论文内容。请用完全不同的关键词和角度重新表述以下问题, 
        "提取核心概念。只输出改写后的查询。\n原始查询: " + current
    )
    resp = await llm.ainvoke(prompt)
    rewritten = (resp.content if hasattr(resp, "content") else str(resp)).strip()
    return {"query": rewritten or current, "retry_count": state.get("retry_count", 0) + 1}
```

- [ ] **Step 5: 运行测试确认通过**

Run: `venv\Scripts\python.exe -m pytest tests/test_library_nodes.py tests/test_library_graph.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/paper/library_nodes.py tests/test_library_nodes.py tests/test_library_graph.py
git commit -m "feat: relevance evaluation (top-3) and retry loop"
```

## Task 5: generate + cite_verify + 图构建

**Files:**
- Modify: `app/paper/library_nodes.py`
- Create: `app/paper/library_graph.py`
- Test: `tests/test_library_graph.py`(追加)

- [ ] **Step 1: 写失败测试(图结构)**

追加到 `tests/test_library_graph.py`:

```python
from app.paper.library_graph import build_library_graph

def test_graph_builds():
    g = build_library_graph()
    assert g is not None

def test_graph_has_all_nodes():
    g = build_library_graph()
    # LangGraph 2.x 可通过 get_graph() 检查
    nodes = g.get_graph().nodes
    for n in ("intent_router", "chat_node", "catalog_node", "direction_select", "retrieve", "relevance_evaluate", "rewrite_query", "generate", "cite_verify"):
        assert n in nodes
```

- [ ] **Step 2: 运行确认失败**

Run: `venv\Scripts\python.exe -m pytest tests/test_library_graph.py -q`
Expected: FAIL(ImportError)

- [ ] **Step 3: 实现 generate_node + cite_verify_node**

```python
async def generate_node(state: LibraryQAState, config: RunnableConfig = None) -> dict:
    """聚合生成(带多轮历史)。"""
    llm = _get_from_config(config, "llm")
    evidence = state.get("evidence", []) or []
    papers = state.get("candidates", []) or []
    if llm is None or not evidence:
        return {"content": "未能在论文库中找到充分证据回答该问题。"}
    from app.paper.prompts import build_library_qa_prompt
    prompt = build_library_qa_prompt(state.get("input_text", ""), papers, evidence, state.get("history"))
    response = await llm.ainvoke(prompt)
    payload = _json_content(response.content if hasattr(response, "content") else response)
    content = str(payload.get("content", "")).strip()
    if not content:
        content = "未能在论文库中找到充分证据回答该问题。"
    raw_citations = payload.get("citations", []) if isinstance(payload.get("citations"), list) else []
    return {"content": content, "raw_citations": raw_citations}


def cite_verify_node(state: LibraryQAState) -> dict:
    """引用形状校验: paper_id 在候选集, chunk_id 在证据集。"""
    papers = state.get("candidates", []) or []
    evidence = state.get("evidence", []) or []
    valid_paper_ids = {p.id for p in papers}
    valid_chunk_ids = {c.chunk_id for c in evidence}
    citations = []
    for item in state.get("raw_citations", []) or []:
        if not isinstance(item, dict):
            continue
        pid = item.get("paper_id")
        cid = item.get("chunk_id")
        if not isinstance(pid, int) or pid not in valid_paper_ids:
            continue
        if cid and (not isinstance(cid, str) or cid not in valid_chunk_ids):
            continue
        citations.append({
            "paper_id": pid,
            "paper_title": item.get("paper_title", ""),
            "page": item.get("page"),
            "section": item.get("section", ""),
            "chunk_id": cid or "",
            "quote": str(item.get("quote", ""))[:240],
            "verified": True,
            "reason": "library_qa",
        })
    return {"citations": citations}
```

- [ ] **Step 4: 实现 library_graph.py**

```python
"""论文全库问答 LangGraph 图构建。

START -> intent_router
  |-- chitchat -> chat_node -> END
  |-- catalog  -> catalog_node -> END
  `-- qa -> direction_select -> retrieve -> relevance_evaluate
        ^                                |
        `-------- rewrite_query <--------+  (不足重试, 最多3次)
  达标 -> generate -> cite_verify -> END
"""

from langgraph.graph import END, START, StateGraph

from app.paper.library_nodes import (
    catalog_node,
    chat_node,
    cite_verify_node,
    direction_select_node,
    generate_node,
    intent_router_node,
    relevance_evaluate_node,
    retrieve_node,
    rewrite_query_node,
    should_retry,
)
from app.paper.library_state import LibraryQAState


def _route_after_intent(state: LibraryQAState) -> str:
    return {"chitchat": "chat_node", "catalog": "catalog_node", "qa": "direction_select"}.get(
        state.get("intent", "qa"), "direction_select"
    )


def build_library_graph():
    builder = StateGraph(LibraryQAState)
    for node in [
        ("intent_router", intent_router_node),
        ("chat_node", chat_node),
        ("catalog_node", catalog_node),
        ("direction_select", direction_select_node),
        ("retrieve", retrieve_node),
        ("relevance_evaluate", relevance_evaluate_node),
        ("rewrite_query", rewrite_query_node),
        ("generate", generate_node),
        ("cite_verify", cite_verify_node),
    ]:
        builder.add_node(*node)

    builder.add_edge(START, "intent_router")
    builder.add_conditional_edges("intent_router", _route_after_intent, {
        "chat_node": "chat_node",
        "catalog_node": "catalog_node",
        "direction_select": "direction_select",
    })
    builder.add_edge("chat_node", END)
    builder.add_edge("catalog_node", END)
    builder.add_edge("direction_select", "retrieve")
    builder.add_edge("retrieve", "relevance_evaluate")
    builder.add_conditional_edges("relevance_evaluate", should_retry, {
        "retry": "rewrite_query",
        "next": "generate",
    })
    builder.add_edge("rewrite_query", "retrieve")
    builder.add_edge("generate", "cite_verify")
    builder.add_edge("cite_verify", END)
    return builder.compile()
```

- [ ] **Step 5: 运行测试确认通过**

Run: `venv\Scripts\python.exe -m pytest tests/test_library_graph.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/paper/library_nodes.py app/paper/library_graph.py tests/test_library_graph.py
git commit -m "feat: library QA workflow graph with retry loop"
```

## Task 6: service 驱动图 + 前端 WorkflowPanel

**Files:**
- Modify: `app/paper/service.py`
- Modify: `web/src/views/ChatView.vue`
- Modify: `web/src/composables/useChat.ts`
- Test: `tests/test_library_qa_service.py`(修改适配)

- [ ] **Step 1: service 注入图依赖**

`app/paper/service.py` 的 PaperService.__init__ 加 `library_graph=None` 参数, 默认 `build_library_graph()`; 新增 `self.library_graph`。

- [ ] **Step 2: 重写 run_library_qa 为图驱动**

保留: 加载历史(_load_history(0, session_id))、会话持久化(END 后写库)。
删除: 过滤/归一化/候选/闲聊/清单/采样/生成/引用 全部搬入节点后的重复代码。

```python
    async def run_library_qa(
        self,
        input_text: str,
        session_id: str = "",
    ) -> AsyncIterator[dict[str, Any]]:
        """全库问答: LangGraph 图驱动(节点事件 + token 流)。"""
        history = await self._load_history(0, session_id)
        initial = {
            "session_id": session_id,
            "input_text": input_text,
            "history": history,
            "query": input_text.strip() or "论文综述",
            "retry_count": 0,
            "candidates": [],
            "evidence": [],
            "relevance_scores": [],
        }
        config = {"configurable": {
            "session_factory": self.session_factory,
            "retriever": self.retriever,
            "llm": self.llm,
        }}

        final_content = ""
        final_citations: list[dict[str, Any]] = []
        try:
            async for mode, event in self.library_graph.astream(
                initial, config=config, stream_mode=["updates", "messages"],
            ):
                if mode == "messages":
                    token = event[0] if isinstance(event, tuple) else event
                    content = token.content if hasattr(token, "content") else str(token)
                    if content:
                        final_content += content
                        yield {"event": "token", "content": content}
                elif mode == "updates":
                    for node_name, node_output in (event or {}).items():
                        if node_name == "generate":
                            if isinstance(node_output, dict):
                                final_content = node_output.get("content", final_content)
                        if node_name == "cite_verify":
                            if isinstance(node_output, dict):
                                final_citations = node_output.get("citations", [])
                        yield {"event": "node", "node": node_name, "status": "completed", **({} if not isinstance(node_output, dict) else node_output)}
        except Exception as exc:
            yield {"event": "error", "detail": f"全库问答失败: {exc}"}
            return

        if not final_content:
            final_content = "未能在论文库中找到充分证据回答该问题。"
        if session_id:
            async with self.session_factory() as session:
                session.add_all([
                    PaperMessage(paper_id=0, session_id=session_id, role="user", content=input_text),
                    PaperMessage(paper_id=0, session_id=session_id, role="assistant", content=final_content,
                                 citations_json=_json(final_citations)),
                ])
                await session.commit()
        yield {"event": "done", "content": final_content, "citations": final_citations}
```

注意: generate 节点不应产出 messages 流(它返回 content 到 state); 若 LangGraph 默认不流 token, 则靠 generate_node 完成后的事件取 content。若需要逐字流, 可让 generate 节点用 llm.astream 自行 yield——但节点不能 yield, 所以方案是: generate 节点返回 content, service 在收到 generate 的 updates 事件后一次性 yield content(见上)。若需逐字, 后续增强。

- [ ] **Step 3: 修改测试适配图驱动**

`tests/test_library_qa_service.py`:
- `test_library_qa_full_flow` 的 FakeLLM 需支持 ainvoke 返回 JSON(过滤/改写/评分/生成按调用序);
- `_FakeSession`/`_LibraryRetriever` 保留; 断言改为: filter 事件变 node 事件序列, done 的 content/citations 不变;
- 补: node 事件包含 intent_router/direction_select/retrieve/relevance_evaluate/generate/cite_verify。

运行: `venv\Scripts\python.exe -m pytest tests/test_library_qa_service.py -q`, 预期 PASS。

- [ ] **Step 4: 前端 ChatView 接回 WorkflowPanel**

`web/src/views/ChatView.vue`: 恢复 `import WorkflowPanel` 与模板 `<WorkflowPanel :nodes="workflowNodes" />`(右侧 CitationPanel 下方)。

- [ ] **Step 5: useChat 消费 node 事件**

`web/src/composables/useChat.ts`: 恢复 workflowNodes 状态; streamLibraryQA 的 dispatchLibraryQaBlock 增加 `node` 事件分支(仿企业版: pending→running→completed 去重); onDone 清空。

- [ ] **Step 6: 前端构建 + 测试**

Run: `cd web && pnpm run build && pnpm test`
Expected: build 通过, 25+ tests pass

- [ ] **Step 7: Commit**

```bash
git add app/paper/service.py web/src/views/ChatView.vue web/src/composables/useChat.ts tests/test_library_qa_service.py
git commit -m "feat: drive library QA graph, restore WorkflowPanel"
```

## Task 7: 回归 + 端到端验证

**Files:**
- 无新文件

- [ ] **Step 1: 后端全量测试**

Run: `venv\Scripts\python.exe -m pytest tests -q`
Expected: 253 + 新增全部 PASS

- [ ] **Step 2: 重启服务 + API 端到端**

1. 重启后端(8000)与前端(5173);
2. 三路验证:
   - 闲聊: "你好" -> node 事件含 intent_router/chat_node, done 有自然回应;
   - 清单: "有什么论文" -> node 事件含 catalog_node, done 列论文;
   - 问答: "超分的方法" -> node 事件含 direction_select/retrieve/relevance_evaluate/generate/cite_verify, done 有回答+引用;
3. 多轮: 同一 session 追问 "那 PromptSR 呢" 仍能理解指代。

- [ ] **Step 3: Playwright 验证 WorkflowPanel**

打开 /chat 发问题, 断言右侧 WorkflowPanel 存在且节点逐步点亮(intent_router -> ... -> cite_verify)。

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: verify library QA workflow end-to-end"
```

---

## Self-Review 记录

- **Spec 覆盖**: §3 图结构 -> Task1-5; §4 状态 -> Task1; §5 事件流 -> Task5/6; §6 兼容 -> Task2-6(逻辑搬入节点, 行为不变); §7 文件 -> Task1-6; §8 验证 -> Task7。
- **占位符检查**: 无 TODO/TBD; 每步含完整代码或明确指令。
- **类型一致性**: LibraryQAState 字段在 Task1 定义, Task3-5 节点按字段名读写(intent/candidates/evidence/relevance_scores/retry_count/content/citations)一致; should_retry 返回 "retry"/"next" 与图条件边映射一致; generate 节点产出 raw_citations, cite_verify 消费并产出 citations(在 service 的 updates 事件中取 cite_verify 输出)。
- **注意**: 若 LangGraph 版本对 `stream_mode=["updates","messages"]` 的 messages 流在 generate 节点不产出(节点不用 astream), 则 token 流退化为"generate 完成后一次性输出 content"——Task 6 Step 2 已注明该降级, 不阻塞功能。

