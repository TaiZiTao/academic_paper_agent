# 论文问答相关性判定 + 单篇检索优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 智能问答"更智能": 相关性判定(问题与论文库相关才走 RAG, 不相关走自由对话) + 单篇检索优化(提到论文名时精确匹配采样) + 对比问题支持。

**Architecture:** 在现有 9 节点 LangGraph 图的 intent_router 后加 relevance_check 节点(规则粗判 + LLM 兜底判定), 新增 general_chat_node(自由对话); retrieve_node 前加单篇标题匹配。state 加 relevance/matched_papers 字段。

**Tech Stack:** LangGraph + FastAPI + SQLAlchemy(async) + Vue3(前端无改动) + SSE。

---

## 文件结构

```
app/paper/library_state.py  修改: 加 relevance/matched_papers 字段
app/paper/prompts.py        修改: 加 build_relevance_prompt + build_general_chat_prompt
app/paper/library_nodes.py  修改: relevance_check_node + general_chat_node + retrieve 单篇匹配
app/paper/library_graph.py  修改: 加 relevance_check 节点和边
tests/test_library_nodes.py 修改: relevance/general/单篇匹配测试
tests/test_library_graph.py 修改: 图结构 + relevance 分支测试
```

---


## Task 1: state 字段 + prompts

**Files:**
- Modify: `app/paper/library_state.py`
- Modify: `app/paper/prompts.py`

- [ ] **Step 1: state 加字段**

`app/paper/library_state.py` 的 LibraryQAState 加:

```python
    relevance: str              # "rag" | "general"(相关性判定结果)
    matched_papers: list        # 单篇/对比匹配到的论文
```

- [ ] **Step 2: prompts.py 加 build_relevance_prompt + build_general_chat_prompt**

在 `app/paper/prompts.py` 追加:

```python
def build_relevance_prompt(question: str) -> str:
    """判定问题是否需要检索论文库。只输出 true 或 false。"""
    return (
        "你是问答助手。判断以下问题是否需要检索论文库(论文库包含图像超分辨率/去雾/复原等方向的学术论文)。"
        "需要检索(问论文内容/方法/对比/某篇论文) -> 输出 true; "
        "不需要(常识/闲聊/无关话题/纯技术原理不涉及库内论文) -> 输出 false。"
        "\n问题: " + question + "\n只输出 true 或 false。"
    )


def build_general_chat_prompt(question: str, history: list[dict] | None = None) -> str:
    """通用问答 prompt: 不检索论文, LLM 自由回答。"""
    import json as _json
    history_block = _json.dumps(history or [], ensure_ascii=False)
    return (
        "你是知识问答助手, 擅长图像超分辨率/去雾/复原等方向, 也可回答通用问题。"
        "直接回答用户问题, 不要编造论文库内容。只输出 JSON: {\"content\": \"回答\", \"citations\": []}. "
        "\n\n对话历史:\n" + history_block + "\n\n用户问题: " + question
    )
```

- [ ] **Step 3: 语法验证**

Run: `& "E:/codex/GraphRAG--main/venv/Scripts/python.exe" -c "import ast; ast.parse(open('E:/codex/GraphRAG--main/app/paper/prompts.py', encoding='utf-8').read()); print('OK')"`
Expected: OK

- [ ] **Step 4: Commit**

```bash
git add app/paper/library_state.py app/paper/prompts.py
git commit -m "feat: relevance and general chat prompts, state fields"
```


## Task 2: relevance_check_node + general_chat_node

**Files:**
- Modify: `app/paper/library_nodes.py`
- Test: `tests/test_library_nodes.py`(追加)

- [ ] **Step 1: 写失败测试**

在 `tests/test_library_nodes.py` 追加(先看文件头部 import 风格, 可能需要 import asyncio/types):

```python
from app.paper.library_nodes import relevance_check_node, general_chat_node

def test_relevance_rule_hits_paper_term():
    import types
    p = types.SimpleNamespace(id=1, title="PGDUN: Prompt-Guided Deep Unfolding")
    r = relevance_check_node({"input_text": "PGDUN 的 PGSA 怎么实现", "candidates": [p]})
    assert r["relevance"] == "rag"

def test_relevance_rule_hits_direction():
    r = relevance_check_node({"input_text": "超分的方法有什么问题", "candidates": []})
    assert r["relevance"] == "rag"

def test_relevance_llm_general():
    import asyncio, types
    class FakeLLM:
        async def ainvoke(self, prompt):
            return types.SimpleNamespace(content="false")
    r = asyncio.run(relevance_check_node({"input_text": "什么是深度学习", "candidates": []}, {"configurable": {"llm": FakeLLM()}}))
    assert r["relevance"] == "general"

def test_relevance_llm_rag():
    import asyncio, types
    class FakeLLM:
        async def ainvoke(self, prompt):
            return types.SimpleNamespace(content="true")
    r = asyncio.run(relevance_check_node({"input_text": "某篇论文的创新点", "candidates": []}, {"configurable": {"llm": FakeLLM()}}))
    assert r["relevance"] == "rag"

def test_general_chat_no_llm():
    import asyncio
    r = asyncio.run(general_chat_node({"input_text": "你好"}, None))
    assert r["content"] != ""
```

- [ ] **Step 2: 运行确认失败**

Run: `& "E:/codex/GraphRAG--main/venv/Scripts/python.exe" -m pytest tests/test_library_nodes.py -q`
Expected: FAIL(ImportError)

- [ ] **Step 3: 实现 relevance_check_node**

在 `app/paper/library_nodes.py` 追加:

```python
PAPER_TERMS = (
    "超分辨率", "超分", "去雾", "去噪", "复原", "图像修复", "低光", "光谱",
    "重建", "注意力", "transformer", "轻量", "损失函数", "psnr", "ssim",
)


async def relevance_check_node(state: LibraryQAState, config: RunnableConfig = None) -> dict:
    """判定问题是否需要检索论文库: 规则粗判 + LLM 兜底。
    返回 {"relevance": "rag" | "general"}。
    """
    import re as _re
    text = (state.get("input_text") or "").lower()
    candidates = state.get("candidates", []) or []

    # 规则粗判 1: 论文标题显著词(大写缩写如 PGDUN)
    title_tokens = set()
    for p in candidates:
        t = getattr(p, "title", "") or ""
        for tok in _re.findall(r"[A-Z]{2,}", t):
            if len(tok) >= 3:
                title_tokens.add(tok.lower())
    if any(tok in text for tok in title_tokens):
        return {"relevance": "rag"}

    # 规则粗判 2: 方向词/论文术语
    if any(kw in text for kw in PAPER_TERMS):
        return {"relevance": "rag"}

    # LLM 兜底判定
    llm = _get_from_config(config, "llm")
    if llm is None:
        return {"relevance": "rag"}  # 无 LLM 时保守走 RAG
    from app.paper.prompts import build_relevance_prompt
    try:
        resp = await llm.ainvoke(build_relevance_prompt(state.get("input_text", "")))
        out = (resp.content if hasattr(resp, "content") else str(resp)).strip().lower()
        return {"relevance": "rag" if "true" in out else "general"}
    except Exception as exc:
        logger.warning(f"relevance_check LLM 判定失败, 保守走 RAG: {exc}")
        return {"relevance": "rag"}
```

- [ ] **Step 4: 实现 general_chat_node**

```python
async def general_chat_node(state: LibraryQAState, config: RunnableConfig = None) -> dict:
    """通用问答: 不检索, LLM 自由回答(常识/无关话题)。"""
    llm = _get_from_config(config, "llm")
    default = "我是论文知识问答助手, 可以问我论文库里的内容, 也可以聊通用问题。"
    if llm is None:
        return {"content": default}
    try:
        from app.paper.prompts import build_general_chat_prompt
        prompt = build_general_chat_prompt(state.get("input_text", ""), state.get("history"))
        response = await llm.ainvoke(prompt)
        payload = _json_content(response.content if hasattr(response, "content") else response)
        content = str(payload.get("content", "")).strip()
        return {"content": content or default}
    except Exception as exc:
        logger.warning(f"general_chat_node LLM 调用失败, 使用兜底: {exc}")
        return {"content": default}
```

- [ ] **Step 5: 运行测试确认通过**

Run: `& "E:/codex/GraphRAG--main/venv/Scripts/python.exe" -m pytest tests/test_library_nodes.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/paper/library_nodes.py tests/test_library_nodes.py
git commit -m "feat: relevance check and general chat nodes"
```


## Task 3: retrieve 单篇匹配 + 对比支持

**Files:**
- Modify: `app/paper/library_nodes.py`(retrieve_node)
- Test: `tests/test_library_nodes.py`(追加)

- [ ] **Step 1: 写失败测试**

在 `tests/test_library_nodes.py` 追加:

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `& "E:/codex/GraphRAG--main/venv/Scripts/python.exe" -m pytest tests/test_library_nodes.py -q`
Expected: FAIL(ImportError)

- [ ] **Step 3: 实现 _match_papers_by_title**

在 `app/paper/library_nodes.py` 追加 helper:

```python
def _match_papers_by_title(text: str, papers: list) -> list:
    """问题中的论文标题显著词(大写缩写/关键词)命中候选论文 -> 返回匹配论文; 否则空。"""
    import re as _re
    text_l = text.lower()
    matched = []
    for p in papers:
        title = getattr(p, "title", "") or ""
        # 大写缩写(如 PGDUN/MWAT-SR/Dual-domain)
        tokens = _re.findall(r"[A-Za-z][A-Za-z0-9-]*", title)
        sig = [t for t in tokens if len(t) >= 4 and t.lower() not in ("image", "lightweight", "network", "for", "the", "with", "attention", "transformer")]
        if any(tok.lower() in text_l for tok in sig):
            matched.append(p)
    return matched
```

- [ ] **Step 4: retrieve_node 加单篇匹配**

修改 `retrieve_node`:

```python
async def retrieve_node(state: LibraryQAState, config: RunnableConfig = None) -> dict:
    """按方向逐篇采样; 问题提到具体论文时, 只对匹配论文深挖。"""
    from app.paper.aggregate_retriever import AggregateRetriever
    retriever = _get_from_config(config, "retriever")
    papers = state.get("candidates", []) or []
    query = state.get("query") or state.get("input_text", "") or "论文综述"
    if retriever is None or not papers:
        return {"evidence": []}
    matched = _match_papers_by_title(query, papers)
    agg = AggregateRetriever(retriever)
    if matched:
        # 单篇/对比: 只对匹配论文采样, 每篇 top-5 深挖
        evidence = await agg.sample_papers([p.id for p in matched], query, per_paper=5)
    else:
        evidence = await agg.sample_papers([p.id for p in papers], query)
    return {"evidence": evidence, "query": query, "matched_papers": [p.id for p in matched]}
```

- [ ] **Step 5: 运行测试确认通过**

Run: `& "E:/codex/GraphRAG--main/venv/Scripts/python.exe" -m pytest tests/test_library_nodes.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/paper/library_nodes.py tests/test_library_nodes.py
git commit -m "feat: single-paper and comparison retrieval"
```

## Task 4: 图接线(relevance_check 分支)

**Files:**
- Modify: `app/paper/library_graph.py`
- Test: `tests/test_library_graph.py`(追加)

- [ ] **Step 1: 写失败测试**

在 `tests/test_library_graph.py` 追加:

```python
from app.paper.library_graph import build_library_graph

def test_graph_has_relevance_nodes():
    g = build_library_graph()
    nodes = set(g.get_graph().nodes.keys())
    assert "relevance_check" in nodes
    assert "general_chat_node" in nodes
```

- [ ] **Step 2: 运行确认失败**

Run: `& "E:/codex/GraphRAG--main/venv/Scripts/python.exe" -m pytest tests/test_library_graph.py -q`
Expected: FAIL

- [ ] **Step 3: 图加节点和边**

`app/paper/library_graph.py` 修改:
- import 加 relevance_check_node, general_chat_node;
- 注册 relevance_check, general_chat_node 节点;
- intent_router 条件边: qa 目标从 direction_select 改为 relevance_check;
- relevance_check 条件边: rag -> direction_select, general -> general_chat_node;
- general_chat_node -> END;
- 新增:

```python
def _route_after_relevance(state: LibraryQAState) -> str:
    return "direction_select" if state.get("relevance", "rag") == "rag" else "general_chat_node"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `& "E:/codex/GraphRAG--main/venv/Scripts/python.exe" -m pytest tests/test_library_graph.py tests/test_library_nodes.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/paper/library_graph.py tests/test_library_graph.py
git commit -m "feat: wire relevance check into workflow graph"
```

## Task 5: 回归 + 端到端验证

**Files:**
- 无新文件

- [ ] **Step 1: 后端全量测试**

Run: `& "E:/codex/GraphRAG--main/venv/Scripts/python.exe" -m pytest tests -q`
Expected: 287 + 新增全部 PASS

- [ ] **Step 2: 重启服务 + API 端到端**

1. 重启后端(8000)与前端(5173);
2. 验证:
   - "什么是深度学习" -> relevance_check -> general_chat_node(自由回答);
   - "今天天气" -> general_chat_node;
   - "PGDUN 的 PGSA 怎么实现" -> relevance_check(rag) -> 单篇匹配 PGDUN -> 深挖回答;
   - "MWAT-SR 和 Dual-domain 对比" -> 匹配两篇 -> 对比回答;
   - "超分方向有哪些方法" -> rag(方向词命中), 行为不变;
   - 闲聊/清单仍正常。

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore: verify relevance classification end-to-end"
```

---

## Self-Review 记录

- **Spec 覆盖**: §3.1 相关性判定 -> Task2; §3.2 general_chat -> Task2; §3.3/3.4 单篇/对比 -> Task3; §4 状态 -> Task1; §5 图 -> Task4; §8 验证 -> Task5。
- **占位符检查**: 无 TODO/TBD; 每步含完整代码或明确指令。
- **类型一致性**: LibraryQAState 的 relevance/matched_papers 在 Task1 定义, Task2-4 按字段读写一致; relevance_check 返回 {"relevance": "rag"|"general"} 与图条件边映射一致; matched_papers 在 retrieve 写入(论文 id 列表)。
