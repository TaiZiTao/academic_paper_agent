# 论文全库问答(方案 B) Implementation Plan



> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.



**Goal:** 把侧边栏"智能问答"升级为论文全库问答: 动态过滤(方向/年份/作者/关键词/语言) + 聚合索引跨论文 RAG, 引用带论文/章节/页码可跳转。



**Architecture:** 复用现有论文 RAG 骨架(run_task 的检索→证据→生成→引用), 新增三层: (1) papers 表加 publication_year + 年份提取; (2) 全库聚合检索(复用单篇索引, 综述型问题按篇采样); (3) 全库问答流式接口, 动态过滤由 LLM 从问题解析。前端 ChatView 换数据源, 复用 PaperCitationList, 移除 WorkflowPanel。



**Tech Stack:** FastAPI + SQLAlchemy(async) + LangChain/LangGraph(现有) + FAISS + rank-bm25 + Vue3/Element Plus + SSE。



---



## 文件结构



```

app/models/paper.py                    修改: Paper 加 publication_year

app/database/database.py              修改: 幂等迁移加列

app/paper/parser.py                   修改: 解析提取 publication_year(元数据→正则→LLM)

app/paper/prompts.py                  修改: 新增 build_filter_extraction_prompt + build_library_qa_prompt

app/paper/aggregate_retriever.py      新建: 全库聚合检索器(逐篇采样)

app/paper/service.py                  修改: process_paper 存年份; 新增 run_library_qa

app/paper/router.py                   修改: 新增 POST /api/v1/papers/qa/stream

web/src/api/paper.ts                  修改: 新增 streamLibraryQA

web/src/views/ChatView.vue            修改: 换数据源, 移除 WorkflowPanel, 加引用面板

web/src/types/chat.ts                 修改: 消息类型加 citations 结构

tests/test_paper_year.py              新建: 年份提取测试

tests/test_paper_filter.py            新建: 动态过滤解析测试

tests/test_aggregate_retriever.py     新建: 聚合索引/采样测试

tests/test_library_qa_api.py          新建: 全库问答 API 测试

```



---



## Task 1: publication_year 字段 + 幂等迁移

**Files:**
- Modify: `app/models/paper.py`
- Modify: `app/database/database.py`
- Test: `tests/test_paper_models.py`

- [ ] **Step 1: 写失败测试**(在 tests/test_paper_models.py 追加)

```python
def test_paper_publication_year_column():
    from app.models.paper import Paper
    cols = {c.name for c in Paper.__table__.columns}
    assert "publication_year" in cols
```

- [ ] **Step 2: 运行确认失败**

Run: `venv\Scripts\python.exe -m pytest tests/test_paper_models.py -q`
Expected: FAIL(`publication_year` not in cols)

- [ ] **Step 3: Paper 模型加字段**

在 `app/models/paper.py` 的 `research_field` 行后加:

```python
    # 发表年份(元数据/首页文本提取, 可空; 全库问答过滤用)
    publication_year = Column(Integer, nullable=True, index=True)
```

- [ ] **Step 4: 幂等迁移**

在 `app/database/database.py` 的 `init_db` 中(在 papers research_field 迁移代码旁)加:

```python
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(papers)"))}
        if "publication_year" not in cols:
            conn.execute(text("ALTER TABLE papers ADD COLUMN publication_year INTEGER"))
```

- [ ] **Step 5: 运行测试确认通过**

Run: `venv\Scripts\python.exe -m pytest tests/test_paper_models.py tests/test_paper_api.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/models/paper.py app/database/database.py tests/test_paper_models.py
git commit -m "feat: add publication_year column with idempotent migration"
```

## Task 2: 年份提取(元数据→正则→LLM)

**Files:**
- Modify: `app/paper/parser.py`
- Modify: `app/paper/service.py`
- Test: `tests/test_paper_year.py`(新建)

- [ ] **Step 1: 写失败测试**

新建 `tests/test_paper_year.py`:

```python
from app.paper.parser import extract_publication_year

def test_meta_year():
    assert extract_publication_year("", "2024") == 2024

def test_regex_copyright():
    assert extract_publication_year("© 2024 IEEE. All rights reserved.", "") == 2024

def test_regex_arxiv():
    assert extract_publication_year("arXiv:2401.12345v2", "") == 2024

def test_no_year():
    assert extract_publication_year("Some text without year", "") is None
```

- [ ] **Step 2: 运行确认失败**

Run: `venv\Scripts\python.exe -m pytest tests/test_paper_year.py -q`
Expected: FAIL(ImportError)

- [ ] **Step 3: parser.py 新增提取函数**

在 `app/paper/parser.py` 添加(放在 _TITLE_NOISE 常量附近):

```python
_YEAR_RE = re.compile(r"(?:©|copyright|copyright \(c\))?\s*(20\d{2})", re.I)
_ARXIV_RE = re.compile(r"arxiv:\s*(\d{2})\d{2}[.]", re.I)


def extract_publication_year(first_page_text: str, meta_year: str = "") -> int | None:
    """从元数据/首页文本提取发表年份, 提取不到返回 None(不抛异常)。"""
    for raw in (meta_year, first_page_text):
        if not raw:
            continue
        m = _ARXIV_RE.search(raw)
        if m:
            return 2000 + int(m.group(1))
        found = [int(y) for y in _YEAR_RE.findall(raw) if 1990 <= int(y) <= 2100]
        if found:
            return max(found)
    return None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `venv\Scripts\python.exe -m pytest tests/test_paper_year.py -q`
Expected: PASS

- [ ] **Step 5: parse_pdf 返回年份 + service 持久化**

`app/paper/parser.py` 的 `ParsedPaper`(dataclass)加字段:

```python
@dataclass
class ParsedPaper:
    # ... 现有字段 ...
    publication_year: int | None = None
```

`parse_pdf` 的 `return ParsedPaper(...)` 加:

```python
        publication_year=extract_publication_year(
            pages[0].text if pages else "",
            str(raw_metadata.get("CreationDate") or raw_metadata.get("creationDate") or ""),
        ),
```

`app/paper/service.py` `process_paper` 中(在 `stored.language = parsed.language` 后):

```python
            if getattr(parsed, "publication_year", None):
                stored.publication_year = parsed.publication_year
```

- [ ] **Step 6: 运行相关测试**

Run: `venv\Scripts\python.exe -m pytest tests/test_paper_parser.py tests/test_paper_service.py tests/test_paper_year.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/paper/parser.py app/paper/service.py tests/test_paper_year.py
git commit -m "feat: extract publication year from pdf metadata/text"
```

## Task 3: 动态过滤提取器

**Files:**
- Modify: `app/paper/prompts.py`
- Test: `tests/test_paper_filter.py`(新建)

- [ ] **Step 1: 写失败测试**

新建 `tests/test_paper_filter.py`:

```python
from app.paper.prompts import parse_filter_response

def test_parse_empty():
    f = parse_filter_response("{}")
    assert f["field"] == "" and f["year_min"] is None

def test_parse_full():
    f = parse_filter_response('{"field":"超分辨率","year_min":2024,"year_max":2025,"authors":["张"],"keywords":["轻量"],"language":"en"}')
    assert f["field"] == "超分辨率" and f["year_min"] == 2024
    assert f["authors"] == ["张"] and f["keywords"] == ["轻量"]

def test_parse_natural_text():
    f = parse_filter_response('前后文: {"field": "超分辨率"} 结尾')
    assert f["field"] == "超分辨率"
```

- [ ] **Step 2: 运行确认失败**

Run: `venv\Scripts\python.exe -m pytest tests/test_paper_filter.py -q`
Expected: FAIL(ImportError)

- [ ] **Step 3: prompts.py 新增过滤 prompt + 解析**

在 `app/paper/prompts.py` 添加:

```python
FILTER_SCHEMA = ('{"field": "研究方向或留空", "year_min": 2024, "year_max": 2025, '
                '"authors": ["作者名"], "keywords": ["关键词"], "language": "en|zh|留空"}')

def build_filter_extraction_prompt(question: str) -> str:
    return ("你是论文检索助手。从用户问题中提取检索过滤条件。只输出 JSON: "
            + FILTER_SCHEMA
            + ". 提取不到的字段留空/null。\n用户问题: " + question)

def parse_filter_response(text: str) -> dict:
    import json, re
    m = re.search(r"\{[^{}]*\}", text or "")
    if not m:
        return {"field": "", "year_min": None, "year_max": None, "authors": [], "keywords": [], "language": ""}
    try:
        data = json.loads(m.group(0))
    except Exception:
        return {"field": "", "year_min": None, "year_max": None, "authors": [], "keywords": [], "language": ""}
    def _norm(v):
        return v if isinstance(v, int) else None
    return {
        "field": str(data.get("field") or "").strip(),
        "year_min": _norm(data.get("year_min")),
        "year_max": _norm(data.get("year_max")),
        "authors": [str(a) for a in (data.get("authors") or []) if a],
        "keywords": [str(k) for k in (data.get("keywords") or []) if k],
        "language": str(data.get("language") or "").strip(),
    }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `venv\Scripts\python.exe -m pytest tests/test_paper_filter.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/paper/prompts.py tests/test_paper_filter.py
git commit -m "feat: dynamic filter extraction prompt and parser"
```

## Task 4: 聚合检索器(逐篇采样)

**Files:**
- Create: `app/paper/aggregate_retriever.py`
- Test: `tests/test_aggregate_retriever.py`(新建)

- [ ] **Step 1: 写失败测试**

新建 `tests/test_aggregate_retriever.py`:

```python
from app.paper.aggregate_retriever import build_evidence_plan

def test_plan_small():
    assert build_evidence_plan(paper_count=3, max_chars=20000, avg_chunk_chars=800) == 3

def test_plan_many():
    plan = build_evidence_plan(paper_count=30, max_chars=20000, avg_chunk_chars=800)
    assert 1 <= plan <= 3

def test_plan_never_zero():
    assert build_evidence_plan(paper_count=100, max_chars=20000, avg_chunk_chars=800) >= 1

def test_plan_zero_papers():
    assert build_evidence_plan(0) == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `venv\Scripts\python.exe -m pytest tests/test_aggregate_retriever.py -q`
Expected: FAIL(ImportError)

- [ ] **Step 3: 实现聚合检索器**

新建 `app/paper/aggregate_retriever.py`:

```python
"""全库聚合检索器: 复用单篇索引, 综述型问题按篇采样保证每篇都有发言权。"""

import logging

from app.paper.retriever import PaperRetriever
from app.paper.schemas import PaperChunkData

logger = logging.getLogger(__name__)


def build_evidence_plan(paper_count: int, max_chars: int = 20000, avg_chunk_chars: int = 800) -> int:
    """按候选论文数计算每篇取几个片段: 论文少取3个, 论文多自动降配, 每篇保底1个。"""
    if paper_count <= 0:
        return 0
    return max(1, min(3, max_chars // max(1, paper_count * avg_chunk_chars)))


class AggregateRetriever:
    """对候选论文列表逐篇采样 top-k, 合并证据。"""

    def __init__(self, retriever: PaperRetriever):
        self.retriever = retriever

    async def sample_papers(
        self,
        paper_ids: list[int],
        query: str,
        per_paper: int | None = None,
    ) -> list[PaperChunkData]:
        if not paper_ids:
            return []
        per = per_paper or build_evidence_plan(len(paper_ids))
        results: list[PaperChunkData] = []
        for pid in paper_ids:
            try:
                hits = await self.retriever.search(pid, query, k=per)
                results.extend(h.chunk for h in hits)
            except Exception as exc:
                logger.warning("采样论文 %s 失败: %s", pid, exc)
        return results
```

- [ ] **Step 4: 运行测试确认通过**

Run: `venv\Scripts\python.exe -m pytest tests/test_aggregate_retriever.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/paper/aggregate_retriever.py tests/test_aggregate_retriever.py
git commit -m "feat: aggregate retriever with per-paper sampling plan"
```

## Task 5: 全库问答服务(run_library_qa)

**Files:**
- Modify: `app/paper/service.py`
- Modify: `app/paper/prompts.py`
- Test: `tests/test_library_qa_api.py`(新建)

- [ ] **Step 1: 写失败测试(API 冒烟)**

新建 `tests/test_library_qa_api.py`:

```python
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)

def test_library_qa_route_registered():
    # 只验证路由存在(不触发真实 LLM): 空 body 应返回 422(校验失败)而非 404
    resp = client.post("/api/v1/papers/qa/stream", json={})
    assert resp.status_code != 404
```

- [ ] **Step 2: 运行确认失败**

Run: `venv\Scripts\python.exe -m pytest tests/test_library_qa_api.py -q`
Expected: FAIL(assert 404 != 404 is False → 断言失败)

- [ ] **Step 3: service.py 新增 run_library_qa**

在 `app/paper/service.py` 添加方法(放在 run_task 之后):

```python
    async def run_library_qa(
        self,
        input_text: str,
        session_id: str = "",
    ) -> AsyncIterator[dict[str, Any]]:
        """全库问答: 动态过滤 → 按篇采样 → 聚合生成。"""
        from sqlalchemy import or_

        from app.paper.aggregate_retriever import AggregateRetriever
        from app.paper.prompts import (
            build_filter_extraction_prompt,
            build_library_qa_prompt,
            parse_filter_response,
        )

        # 1) 动态过滤(LLM 提取, 失败降级为不过滤)
        filters: dict = {}
        try:
            raw = await self._ainvoke_with_retry(build_filter_extraction_prompt(input_text))
            filters = parse_filter_response(raw)
        except Exception:
            filters = {}

        # 2) 候选论文集
        async with self.session_factory() as session:
            stmt = select(Paper)
            conds = []
            if filters.get("field"):
                conds.append(Paper.research_field == filters["field"])
            if filters.get("year_min"):
                conds.append(Paper.publication_year >= filters["year_min"])
            if filters.get("year_max"):
                conds.append(Paper.publication_year <= filters["year_max"])
            if filters.get("authors"):
                conds.append(or_(*[Paper.authors_json.ilike(f"%{a}%") for a in filters["authors"]]))
            if filters.get("keywords"):
                conds.append(or_(*[Paper.title.ilike(f"%{k}%") | Paper.keywords_json.ilike(f"%{k}%") for k in filters["keywords"]]))
            if filters.get("language"):
                conds.append(Paper.language == filters["language"])
            if conds:
                stmt = stmt.where(*conds)
            stmt = stmt.order_by(Paper.created_at.desc())
            papers = (await session.execute(stmt)).scalars().all()

        if not papers:
            yield {"event": "error", "detail": "未找到符合条件的论文, 请尝试放宽过滤条件"}
            return

        yield {"event": "filter", "filters": filters, "candidates": len(papers)}

        # 3) 按篇采样(综述/单点统一走逐篇采样, 保证覆盖全部候选)
        agg = AggregateRetriever(self.retriever)
        query_text = input_text.strip() or "论文综述"
        evidence = await agg.sample_papers([p.id for p in papers], query_text)
        if not evidence:
            yield {"event": "error", "detail": "检索不到相关证据"}
            return

        # 4) 聚合生成
        prompt = build_library_qa_prompt(input_text, papers, evidence)
        response = await self._ainvoke_with_retry(prompt)
        payload = _json_content(response.content if hasattr(response, "content") else response)
        content = str(payload.get("content", "")).strip()
        citations: list[dict[str, Any]] = []
        for item in payload.get("citations", []):
            if not isinstance(item, dict):
                continue
            citations.append({
                "paper_id": item.get("paper_id"),
                "paper_title": item.get("paper_title", ""),
                "page": item.get("page"),
                "section": item.get("section", ""),
                "chunk_id": item.get("chunk_id", ""),
                "quote": str(item.get("quote", ""))[:240],
                "verified": True,
                "reason": "library_qa",
            })

        # 5) 会话持久化(paper_id=0 表示全库会话)
        if session_id:
            async with self.session_factory() as session:
                session.add_all([
                    PaperMessage(paper_id=0, session_id=session_id, role="user", content=input_text),
                    PaperMessage(paper_id=0, session_id=session_id, role="assistant", content=content,
                                 citations_json=_json(citations)),
                ])
                await session.commit()

        for offset in range(0, len(content), 80):
            yield {"event": "token", "content": content[offset : offset + 80]}
        yield {"event": "done", "content": content, "citations": citations}
```

- [ ] **Step 4: prompts.py 新增全库问答 prompt**

```python
def build_library_qa_prompt(question: str, papers, evidence) -> str:
    paper_titles = {p.id: p.title for p in papers}
    paper_lines = "\n".join(f"- {p.title}" for p in papers)
    evidence_block = "\n\n".join(
        f"[{chunk.section}; p{chunk.page_start}; {paper_titles.get(chunk.paper_id, chunk.paper_id)}]\n{chunk.content}"
        for chunk in evidence
    )
    return (
        "你是严谨的科研论文综述助手。基于以下多篇论文的证据回答用户问题。"
        "回答要覆盖尽量多的论文, 引用时给出论文标题+章节+页码。"
        "若证据不足请说明。只输出 JSON: {\"content\": \"回答\", "
        "\"citations\": [{\"paper_id\": 1, \"paper_title\": \"标题\", \"page\": 1, "
        "\"section\": \"章节\", \"chunk_id\": \"ID\", \"quote\": \"原文短句\"}]}. "
        "\n\n论文清单:\n" + paper_lines + "\n\n证据:\n" + evidence_block
    )
```

注意: `PaperChunkData` 需要含 `paper_id` 字段(查看 `app/paper/schemas.py` 确认; 若已含则直接可用, 若不含则 evidence 组装时从 chunk_id 解析 paper_id)。

- [ ] **Step 5: 运行测试确认通过**

Run: `venv\Scripts\python.exe -m pytest tests/test_library_qa_api.py -q`
Expected: PASS(路由存在, 空 body 返回 422 而非 404)

- [ ] **Step 6: Commit**

```bash
git add app/paper/service.py app/paper/prompts.py tests/test_library_qa_api.py
git commit -m "feat: library-wide QA with dynamic filter and per-paper sampling"
```

## Task 6: 路由 + 前端

**Files:**
- Modify: `app/paper/router.py`
- Modify: `web/src/api/paper.ts`
- Modify: `web/src/types/chat.ts`
- Modify: `web/src/views/ChatView.vue`

- [ ] **Step 1: 路由新增流式端点**

在 `app/paper/router.py` 添加(注意顶部确认 import: `from fastapi.responses import StreamingResponse` 若未导入则补充):

```python
@router.post("/qa/stream")
async def library_qa_stream(
    request: dict,
    service: PaperService = Depends(get_paper_service),
):
    input_text = str(request.get("input_text", "")).strip()
    if not input_text:
        raise HTTPException(status_code=400, detail="问题不能为空")
    session_id = str(request.get("session_id", "") or "")

    async def stream():
        try:
            async for event in service.run_library_qa(input_text, session_id):
                yield _sse(event)
        except Exception as exc:
            yield _sse({"event": "error", "detail": str(exc)})

    return StreamingResponse(stream(), media_type="text/event-stream")
```

- [ ] **Step 2: 前端 API 新增 streamLibraryQA**

在 `web/src/api/paper.ts` 添加(复用 dispatchSseBlock 风格或独立实现):

```typescript
export function streamLibraryQA(
  inputText: string,
  sessionId: string,
  callbacks: {
    onToken: (t: string) => void;
    onDone: (d: { content: string; citations: PaperCitation[] }) => void;
    onError: (e: string) => void;
  },
  signal?: AbortSignal,
): void {
  fetch(`/api/v1/papers/qa/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ input_text: inputText, session_id: sessionId }),
    signal,
  })
    .then(async (res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const reader = res.body?.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      const pump = () => {
        reader?.read().then(({ done, value }) => {
          if (done) return;
          buffer += decoder.decode(value, { stream: true });
          const blocks = buffer.split("\n\n");
          buffer = blocks.pop() || "";
          for (const block of blocks) {
            const ev = /^event: (.+)$/m.exec(block)?.[1];
            const data = /^data: (.+)$/m.exec(block)?.[1];
            if (!data) continue;
            const payload = JSON.parse(data);
            if (ev === "token") callbacks.onToken(payload.content || "");
            else if (ev === "done") callbacks.onDone(payload);
            else if (ev === "error") callbacks.onError(payload.detail || "问答失败");
          }
          pump();
        });
      };
      pump();
    })
    .catch((e) => callbacks.onError(String(e.message || "问答失败")));
}
```

- [ ] **Step 3: 消息类型加 citations**

`web/src/types/chat.ts` 的 `ChatMessage` 加字段:

```typescript
citations?: import("@/types/paper").PaperCitation[];
```

- [ ] **Step 4: ChatView 换数据源**

`web/src/views/ChatView.vue`:
1. 移除 `import WorkflowPanel from "@/components/chat/WorkflowPanel.vue"` 与模板中的 `<WorkflowPanel :nodes="workflowNodes" />`;
2. 移除 `workflowNodes` 相关引用(useChat 里保留不影响编译, 但模板必须移除);
3. 发送消息改用 `streamLibraryQA`(替换原企业问答的 fetch/SSE 调用), 将 token 拼入 assistant 消息, done 时写入 citations;
4. 右侧引用面板继续用 `CitationPanel`, 数据源改为消息的 `citations`(PaperCitation[] 结构);
5. 页面标题保持"智能问答"。

- [ ] **Step 5: 构建验证**

Run: `cd web && pnpm run build`
Expected: vue-tsc + vite build 通过(无 WorkflowPanel 残留引用错误)

- [ ] **Step 6: 前端测试**

Run: `cd web && pnpm test`
Expected: 25+ tests pass

- [ ] **Step 7: Commit**

```bash
git add app/paper/router.py web/src/api/paper.ts web/src/types/chat.ts web/src/views/ChatView.vue
git commit -m "feat: wire library QA stream endpoint and frontend"
```

## Task 7: 存量论文年份回填 + 全量测试 + 端到端验证

**Files:**
- Create: `scripts/backfill_years.py`

- [ ] **Step 1: 写回填脚本**

```python
"""为存量论文回填 publication_year: 用现有 PDF 首页文本重新提取(不重跑精读流程)。"""

import asyncio
from pathlib import Path

from app.config.settings import settings
from app.database import async_session, init_db
from app.models.paper import Paper
from app.paper.parser import extract_publication_year

async def main():
    await init_db()
    async with async_session() as session:
        from sqlalchemy import select
        papers = (await session.execute(select(Paper))).scalars().all()
        for p in papers:
            if p.publication_year:
                continue
            pdf = Path(settings.data_dir) / "papers" / "files" / p.stored_filename
            if not pdf.exists():
                continue
            try:
                import pdfplumber
                with pdfplumber.open(pdf) as doc:
                    first = doc.pages[0].extract_text() or ""
                year = extract_publication_year(first, "")
                if year:
                    p.publication_year = year
                    print(p.id, p.title[:40], "->", year)
            except Exception as exc:
                print(p.id, "skip:", exc)
        await session.commit()

asyncio.run(main())
```

- [ ] **Step 2: 运行回填**

Run: `venv\Scripts\python.exe -u scripts/backfill_years.py`
Expected: 打印各论文提取的年份(提取不到的跳过, 后续可手动补)

- [ ] **Step 3: 后端全量测试**

Run: `venv\Scripts\python.exe -m pytest tests -q`
Expected: 原有 209 + 新增测试全部 PASS

- [ ] **Step 4: 重启服务 + Playwright 验证**

1. 重启后端 uvicorn(8000)与前端 vite(5173);
2. Playwright 打开 /chat: 发送"近两年超分辨率论文提出了哪些问题", 验证:
   - 回答覆盖多篇论文, 引用带论文标题+页码;
   - 无 WorkflowPanel, 有引用来源面板;
3. 点引用 → 跳转 /papers/{id} 对应位置。

- [ ] **Step 5: Commit**

```bash
git add scripts/backfill_years.py
git commit -m "chore: backfill publication years for existing papers"
```

---

## Self-Review 记录

- **Spec 覆盖**: §3.1 年份字段→Task1; §8 年份提取→Task2; §4 动态过滤→Task3; §3.2/§5.1 聚合与采样→Task4; §6 API→Task5/6; §7 前端→Task6; §9 验证/§10 回填→Task7。
- **占位符检查**: 无 TODO/TBD; 每步含完整代码; Task5/6 的"注意"行是明确的实现指引(import 检查、paper_id 字段确认), 非占位。
- **类型一致性**: PaperMessage(paper_id=0) 全库会话约定在 Task5 定义; PaperCitation 引用 web/src/types/paper.ts 现有类型(与单篇问答同一结构); build_library_qa_prompt 三参数签名在 Task5 内一致; AggregateRetriever 依赖 PaperRetriever.search 现有签名(paper_id, query, k)。
