# 科研文献搜索下载 Agent 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> 说明: 本项目当前不是 git 仓库(此前 spec 已注明), 各任务的 commit 步骤在 git 未初始化时跳过(或先 `git init` 再执行)。

**Goal:** 在现有 GraphRAG 论文助手平台内实现「文献搜索下载 Agent」: 用户用自然语言搜索 arXiv/Semantic Scholar, 勾选结果后自动下载 PDF 并复用现有管道解析入库; 付费墙论文经西南交通大学 VPN 浏览器会话下载。

**Architecture:** FastAPI 应用内新增 `app/research/` 模块(与 app/paper/ 平级)。四个核心单元: Searcher(多源检索, 统一 SearchResult 结构)、SearchAgent(LangGraph 4 节点编排: 规划→并行检索→去重→相关性排序, LLM 不可用降级直查)、Downloader(四级降级: L1 直链→L2 Unpaywall→L3 VPN 浏览器→L4 手动链接)、BrowserService(Playwright 管理学校 VPN 会话)。ImportService 负责导入任务持久化与串行队列, 下载完成后调用现有 PaperService.create_paper/process_paper 完成解析入库。前端在论文库页新增「文献检索」标签页。

**Tech Stack:** Python 3.10 / FastAPI / LangGraph / httpx / Playwright / SQLAlchemy async / Vue 3 + Element Plus + TypeScript / SSE

---

## 文件结构总览

**后端(新增):**
- `app/models/research.py` — PaperImport ORM 模型
- `app/research/__init__.py` — 模块导出
- `app/research/schemas.py` — SearchResult / ImportItem / ImportTaskOut / BrowserStatus 等 Pydantic 模型
- `app/research/searchers.py` — ArxivSearcher + SemanticScholarSearcher(统一接口 `async search(query, top_k) -> list[SearchResult]`)
- `app/research/agent.py` — SearchAgent(LangGraph StateGraph 4 节点 + 直查降级 + `_ainvoke_with_retry` 助手)
- `app/research/downloader.py` — Downloader(四级降级 + 串行队列限速)
- `app/research/browser.py` — BrowserService(Playwright 生命周期 / VPN 会话 / 状态机)
- `app/research/service.py` — ImportService(导入任务持久化 + 后台队列 + 进度 + 重试 + 解析交接)
- `app/research/router.py` — REST/SSE 端点
- `app/api/dependencies.py` — 增加 `get_research_service` 单例工厂(遵循现有 get_paper_service 模式)

**后端(修改):**
- `app/config/settings.py` + `.env.example` — RESEARCH_* 配置
- `app/models/__init__.py` — 注册 PaperImport
- `main.py` — 注册 research 路由
- `requirements.txt` — 增加 playwright

**前端(新增):**
- `web/src/types/research.ts` — 类型定义
- `web/src/api/research.ts` — API 客户端(SSE + REST)
- `web/src/components/research/ResearchSearchPanel.vue` — 搜索面板(搜索框/Agent 过程流/结果卡片/选择)
- `web/src/components/research/ImportQueueDrawer.vue` — 导入队列抽屉(轮询进度/重试)

**前端(修改):**
- `web/src/views/PaperListView.vue` — 顶部加「论文档案 / 文献检索」标签切换

**测试(新增):**
- `tests/test_research_searchers.py` / `test_research_agent.py` / `test_research_downloader.py` / `test_research_service.py` / `test_research_api.py` / `test_research_browser.py`

**测试命令约定:** 后端测试用 `venv/Scripts/python.exe -m pytest tests/<file> -v`; 前端构建用 `pnpm --dir web build`。所有网络调用在测试中一律 mock(httpx.MockTransport / 假 LLM / 假 Playwright), 测试不依赖真实网络。

---

## Task 1: 配置项(Settings)

**Files:**
- Modify: `app/config/settings.py`(Retrieval 段之后新增 Research 段)
- Modify: `.env.example`(Memory 段后新增)
- Test: `tests/test_research_settings.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_research_settings.py
"""文献检索 Agent 配置项默认值测试。"""

from app.config.settings import Settings


def test_research_settings_defaults():
    s = Settings(_env_file=None)
    assert s.research_top_k == 20
    assert s.research_search_timeout == 15.0
    assert s.research_download_delay == 4.0
    assert s.research_proxy == ""
    assert s.vpn_portal_url == "https://vpn.swjtu.edu.cn"
    assert s.unpaywall_email == ""
```

- [ ] **Step 2: 运行确认失败**

Run: `venv/Scripts/python.exe -m pytest tests/test_research_settings.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'research_top_k'`

- [ ] **Step 3: 实现配置**

在 `app/config/settings.py` 的 `short_memory_size` 之后、`Logging` 段之前插入:

```python
    # ============================================================
    # Research — 文献搜索下载 Agent
    # ============================================================
    research_top_k: int = 20
    research_search_timeout: float = 15.0
    research_download_delay: float = 4.0
    research_proxy: str = ""
    vpn_portal_url: str = "https://vpn.swjtu.edu.cn"
    unpaywall_email: str = ""
```

在 `.env.example` 的 Memory 段后追加:

```
# 文献搜索下载 Agent
RESEARCH_TOP_K=20
RESEARCH_SEARCH_TIMEOUT=15
RESEARCH_DOWNLOAD_DELAY=4
RESEARCH_PROXY=
VPN_PORTAL_URL=https://vpn.swjtu.edu.cn
UNPAYWALL_EMAIL=
```

- [ ] **Step 4: 运行确认通过**

Run: `venv/Scripts/python.exe -m pytest tests/test_research_settings.py -v`
Expected: PASS

- [ ] **Step 5: Commit**(非 git 仓库则跳过)

```bash
git add app/config/settings.py .env.example tests/test_research_settings.py
git commit -m "feat(research): add research agent settings"
```

---

## Task 2: PaperImport 模型与注册

**Files:**
- Create: `app/models/research.py`
- Modify: `app/models/__init__.py`
- Test: `tests/test_research_model.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_research_model.py
"""PaperImport 模型持久化测试。"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.base import Base
from app.models.research import PaperImport


@pytest.mark.asyncio
async def test_paper_import_roundtrip(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'research.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    item = PaperImport(
        title="Test Paper",
        source="arxiv",
        external_id="2401.12345",
        doi="10.1000/xyz",
        pdf_url="https://arxiv.org/pdf/2401.12345",
        page_url="https://arxiv.org/abs/2401.12345",
        status="pending",
        progress=0,
    )
    async with session_factory() as session:
        session.add(item)
        await session.commit()
        await session.refresh(item)
        assert item.id > 0
        fetched = (await session.execute(select(PaperImport).where(PaperImport.id == item.id))).scalar_one()
        assert fetched.title == "Test Paper"
        assert fetched.status == "pending"
        assert fetched.paper_id is None
    await engine.dispose()
```

- [ ] **Step 2: 运行确认失败**

Run: `venv/Scripts/python.exe -m pytest tests/test_research_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.research'`

- [ ] **Step 3: 实现模型**

创建 `app/models/research.py`(仿照 app/models/paper.py 的 Column 风格):

```python
"""文献检索下载 Agent 的 SQLAlchemy 持久化模型。"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.database.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PaperImport(Base):
    """一次「搜索到下载入库」任务。"""

    __tablename__ = "paper_imports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(512), nullable=False, default="")
    source = Column(String(32), nullable=False, default="")  # arxiv | semantic_scholar
    external_id = Column(String(128), nullable=True)  # arxiv_id / S2 paperId
    doi = Column(String(256), nullable=True)
    pdf_url = Column(String(1024), nullable=True)
    page_url = Column(String(1024), nullable=True)
    status = Column(String(32), nullable=False, default="pending", index=True)
    progress = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=False, default="")
    paper_id = Column(Integer, nullable=True)  # 入库成功后关联 papers.id
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
```

在 `app/models/__init__.py` 的 import 区追加:

```python
from app.models.research import PaperImport  # noqa: F401
```

- [ ] **Step 4: 运行确认通过**

Run: `venv/Scripts/python.exe -m pytest tests/test_research_model.py -v`
Expected: PASS

- [ ] **Step 5: 确认 create_all 在应用启动路径可见**

`app/database/database.py` 的 `init_db()` 依赖 import 链注册模型: main.py → app.research.router → app.research.service → app.models.research。Task 8 完成前该链路未建立, 此步仅确认无语法错误:

Run: `venv/Scripts/python.exe -c "import app.models.research"`
Expected: 无输出(成功)

- [ ] **Step 6: Commit**(非 git 仓库则跳过)

```bash
git add app/models/research.py app/models/__init__.py tests/test_research_model.py
git commit -m "feat(research): add PaperImport model"
```

---

## Task 3: 领域 Schemas

**Files:**
- Create: `app/research/schemas.py`
- Test: `tests/test_research_schemas.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_research_schemas.py
"""文献检索 Agent Pydantic 模型测试。"""

import pytest
from pydantic import ValidationError

from app.research.schemas import ImportItem, ImportTaskOut, SearchResult


def test_search_result_defaults():
    r = SearchResult(
        source="arxiv", title="T", authors=["A"], venue="",
        abstract="", page_url="https://arxiv.org/abs/2401.1",
    )
    assert r.year is None
    assert r.doi is None
    assert r.pdf_url is None
    assert r.citations == 0


def test_import_item_requires_title_and_source():
    with pytest.raises(ValidationError):
        ImportItem(source="arxiv")  # 缺 title


def test_import_task_out_status_enum():
    task = ImportTaskOut(
        id=1, title="T", source="arxiv", status="pending",
        progress=0, error_message="", paper_id=None, created_at="2026-08-21T00:00:00",
    )
    assert task.status == "pending"
```

- [ ] **Step 2: 运行确认失败**

Run: `venv/Scripts/python.exe -m pytest tests/test_research_schemas.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.research.schemas'`

- [ ] **Step 3: 实现 schemas**

创建 `app/research/schemas.py`:

```python
"""文献检索下载 Agent 的领域对象与 API 共享结构。"""

from typing import Literal

from pydantic import BaseModel, Field


class SearchResult(BaseModel):
    """单个搜索结果(多源统一结构)。"""

    source: Literal["arxiv", "semantic_scholar"]
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str = ""
    abstract: str = ""
    doi: str | None = None
    pdf_url: str | None = None
    page_url: str
    citations: int = 0


class ImportItem(BaseModel):
    """前端勾选后提交的下载入库项。"""

    source: Literal["arxiv", "semantic_scholar"] = "arxiv"
    title: str = Field(min_length=1)
    doi: str | None = None
    pdf_url: str | None = None
    page_url: str | None = None
    external_id: str | None = None


class ImportTaskOut(BaseModel):
    """paper_imports 行对外结构。"""

    id: int
    title: str
    source: str
    status: str
    progress: int
    error_message: str = ""
    paper_id: int | None = None
    created_at: str = ""
    updated_at: str = ""


class BrowserStatus(BaseModel):
    """VPN 浏览器会话状态。"""

    status: Literal["none", "alive", "expired"]
    message: str = ""


class SearchPlan(BaseModel):
    """PlanQuery 节点 LLM 输出。"""

    queries: list[str] = Field(min_length=1)
    sources: list[Literal["arxiv", "semantic_scholar"]] = Field(default_factory=list)


class RankedResult(BaseModel):
    """RelevanceRank 节点 LLM 输出的单个排序项。"""

    index: int
    score: int
```

- [ ] **Step 4: 运行确认通过**

Run: `venv/Scripts/python.exe -m pytest tests/test_research_schemas.py -v`
Expected: PASS

- [ ] **Step 5: Commit**(非 git 仓库则跳过)

```bash
git add app/research/schemas.py tests/test_research_schemas.py
git commit -m "feat(research): add domain schemas"
```

---

## Task 4: Searcher 层(arXiv + Semantic Scholar)

**Files:**
- Create: `app/research/searchers.py`
- Test: `tests/test_research_searchers.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_research_searchers.py
"""多源检索器测试(全部 mock, 不访问真实网络)。"""

import httpx
import pytest

from app.research.searchers import ArxivSearcher, SemanticScholarSearcher

ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2401.12345v1</id>
    <title>Lightweight Attention for Super-Resolution</title>
    <summary>We propose a lightweight attention module.</summary>
    <published>2024-01-10T00:00:00Z</published>
    <author><name>Alice Zhang</name></author>
    <author><name>Bob Wang</name></author>
    <arxiv:doi>10.1000/arxiv.2401.12345</arxiv:doi>
    <arxiv:journal_ref>CVPR 2024</arxiv:journal_ref>
  </entry>
</feed>"""

S2_JSON = {
    "data": [
        {
            "title": "Efficient Super-Resolution Networks",
            "abstract": "An efficient approach.",
            "year": 2023,
            "authors": [{"name": "Carol Li"}],
            "venue": "ICCV",
            "externalIds": {"DOI": "10.1000/xyz", "ArXiv": "2301.99999"},
            "citationCount": 42,
            "openAccessPdf": {"url": "https://arxiv.org/pdf/2301.99999"},
            "url": "https://www.semanticscholar.org/paper/abc",
        }
    ]
}


def client_with(handler):
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport, base_url="https://example.com")


@pytest.mark.asyncio
async def test_arxiv_search_parses_atom():
    def handler(request):
        assert "export.arxiv.org" in str(request.url)
        return httpx.Response(200, text=ARXIV_XML)

    searcher = ArxivSearcher(client=client_with(handler), timeout=5.0)
    results = await searcher.search("lightweight super-resolution attention", top_k=5)
    assert len(results) == 1
    r = results[0]
    assert r.source == "arxiv"
    assert r.title == "Lightweight Attention for Super-Resolution"
    assert r.authors == ["Alice Zhang", "Bob Wang"]
    assert r.year == 2024
    assert r.venue == "CVPR 2024"
    assert r.doi == "10.1000/arxiv.2401.12345"
    assert r.pdf_url == "https://arxiv.org/pdf/2401.12345"
    assert r.page_url == "https://arxiv.org/abs/2401.12345"


@pytest.mark.asyncio
async def test_s2_search_parses_json():
    def handler(request):
        assert "api.semanticscholar.org" in str(request.url)
        return httpx.Response(200, json=S2_JSON)

    searcher = SemanticScholarSearcher(client=client_with(handler), timeout=5.0)
    results = await searcher.search("super-resolution", top_k=5)
    assert len(results) == 1
    r = results[0]
    assert r.source == "semantic_scholar"
    assert r.year == 2023
    assert r.citations == 42
    assert r.pdf_url == "https://arxiv.org/pdf/2301.99999"
    assert r.doi == "10.1000/xyz"


@pytest.mark.asyncio
async def test_arxiv_timeout_raises():
    async def handler(request):
        raise httpx.ConnectTimeout("timeout")

    searcher = ArxivSearcher(client=client_with(handler), timeout=5.0)
    with pytest.raises(httpx.HTTPError):
        await searcher.search("query", top_k=5)


@pytest.mark.asyncio
async def test_s2_empty_results():
    def handler(request):
        return httpx.Response(200, json={"data": []})

    searcher = SemanticScholarSearcher(client=client_with(handler), timeout=5.0)
    assert await searcher.search("nothing", top_k=5) == []
```

- [ ] **Step 2: 运行确认失败**

Run: `venv/Scripts/python.exe -m pytest tests/test_research_searchers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.research.searchers'`

- [ ] **Step 3: 实现 searchers**

创建 `app/research/searchers.py`:

```python
"""文献检索源: arXiv API 与 Semantic Scholar API。

统一输出 `SearchResult`(见 schemas.py)。httpx.AsyncClient 可注入,
测试用 MockTransport 不访问真实网络; 生产默认创建真实 client。
"""

import xml.etree.ElementTree as ET
from typing import Any

import httpx

from app.research.schemas import SearchResult

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"

ARXIV_API = "https://export.arxiv.org/api/query"
S2_API = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_FIELDS = "title,abstract,year,authors,venue,externalIds,citationCount,openAccessPdf,url"


def _make_client(proxy: str, timeout: float) -> httpx.AsyncClient:
    kwargs: dict[str, Any] = {
        "timeout": timeout,
        "headers": {"User-Agent": "research-agent/1.0 (academic search)"},
    }
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.AsyncClient(**kwargs)


class ArxivSearcher:
    """arXiv API 检索(Atom XML)。"""

    def __init__(self, client: httpx.AsyncClient | None = None, timeout: float = 15.0):
        self.client = client or _make_client("", timeout)
        self.timeout = timeout

    async def search(self, query: str, top_k: int = 20) -> list[SearchResult]:
        params = {
            "search_query": f'all:"{query}"',
            "start": 0,
            "max_results": top_k,
            "sortBy": "relevance",
        }
        resp = await self.client.get(ARXIV_API, params=params)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        results: list[SearchResult] = []
        for entry in root.findall(f"{ATOM}entry"):
            abs_url = (entry.findtext(f"{ATOM}id") or "").strip()
            arxiv_id = abs_url.rsplit("/abs/", 1)[-1] if "/abs/" in abs_url else ""
            doi_el = entry.find(f"{ARXIV}doi")
            journal_el = entry.find(f"{ARXIV}journal_ref")
            published = (entry.findtext(f"{ATOM}published") or "").strip()
            year = int(published[:4]) if published[:4].isdigit() else None
            authors = [
                (a.findtext(f"{ATOM}name") or "").strip()
                for a in entry.findall(f"{ATOM}author")
                if a.findtext(f"{ATOM}name")
            ]
            results.append(
                SearchResult(
                    source="arxiv",
                    title=(entry.findtext(f"{ATOM}title") or "").strip().replace("\n", " "),
                    authors=authors,
                    year=year,
                    venue=(journal_el.text or "").strip() if journal_el is not None else "",
                    abstract=(entry.findtext(f"{ATOM}summary") or "").strip(),
                    doi=(doi_el.text or "").strip() if doi_el is not None else None,
                    pdf_url=f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else None,
                    page_url=abs_url or (f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""),
                )
            )
        return results


class SemanticScholarSearcher:
    """Semantic Scholar Graph API 检索(JSON)。"""

    def __init__(self, client: httpx.AsyncClient | None = None, timeout: float = 15.0):
        self.client = client or _make_client("", timeout)
        self.timeout = timeout

    async def search(self, query: str, top_k: int = 20) -> list[SearchResult]:
        params = {"query": query, "limit": top_k, "fields": S2_FIELDS}
        resp = await self.client.get(S2_API, params=params)
        resp.raise_for_status()
        data = (resp.json() or {}).get("data") or []
        results: list[SearchResult] = []
        for item in data:
            ext = item.get("externalIds") or {}
            oa = item.get("openAccessPdf") or {}
            results.append(
                SearchResult(
                    source="semantic_scholar",
                    title=(item.get("title") or "").strip(),
                    authors=[a.get("name", "") for a in (item.get("authors") or []) if a.get("name")],
                    year=item.get("year"),
                    venue=item.get("venue") or "",
                    abstract=item.get("abstract") or "",
                    doi=ext.get("DOI") or None,
                    pdf_url=(oa.get("url") or None) if oa else None,
                    page_url=item.get("url") or "",
                    citations=int(item.get("citationCount") or 0),
                )
            )
        return results
```

- [ ] **Step 4: 运行确认通过**

Run: `venv/Scripts/python.exe -m pytest tests/test_research_searchers.py -v`
Expected: PASS

- [ ] **Step 5: Commit**(非 git 仓库则跳过)

```bash
git add app/research/searchers.py tests/test_research_searchers.py
git commit -m "feat(research): multi-source searchers"
```

---

## Task 5: SearchAgent(LangGraph 4 节点 + 直查降级)

**Files:**
- Create: `app/research/agent.py`
- Create: `app/research/__init__.py`
- Test: `tests/test_research_agent.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_research_agent.py
"""SearchAgent 编排测试(假 LLM + 假 searcher)。"""

import pytest

from app.research.agent import SearchAgent
from app.research.schemas import SearchResult


class FakeLLM:
    """按 prompt 内容返回不同的 JSON。"""

    def __init__(self, plan_json: str, rank_json: str):
        self.plan_json = plan_json
        self.rank_json = rank_json

    async def ainvoke(self, prompt: str):
        if "检索计划" in prompt:
            return type("Msg", (), {"content": self.plan_json})()
        return type("Msg", (), {"content": self.rank_json})()


class FailingLLM(FakeLLM):
    async def ainvoke(self, prompt: str):
        raise RuntimeError("llm down")


def make_results() -> list[SearchResult]:
    return [
        SearchResult(source="arxiv", title="Paper A", authors=[], abstract="about attention", page_url="u1", pdf_url="p1", year=2024),
        SearchResult(source="semantic_scholar", title="Paper A", authors=[], abstract="duplicate", page_url="u2", year=2024),  # 标题重复
        SearchResult(source="arxiv", title="Paper B", authors=[], abstract="about super resolution", page_url="u3"),
    ]


class FakeSearchers:
    async def search(self, query, top_k):
        return make_results()


@pytest.mark.asyncio
async def test_agent_plan_dedupe_rank():
    llm = FakeLLM(
        plan_json='{"queries": ["lightweight super-resolution attention"], "sources": ["arxiv", "semantic_scholar"]}',
        rank_json='{"ranking": [{"index": 1, "score": 9}, {"index": 0, "score": 5}]}',
    )
    agent = SearchAgent(llm=llm, searchers=FakeSearchers())
    events = []
    results = await agent.run("轻量超分注意力", top_k=10, on_event=events.append)
    # 去重后 2 条; rank 输出 index 1(Paper B)、index 0(Paper A)
    assert [r.title for r in results] == ["Paper B", "Paper A"]
    assert any(e["event"] == "plan" for e in events)
    assert any(e["event"] == "results" for e in events)


@pytest.mark.asyncio
async def test_agent_direct_mode_on_llm_failure():
    agent = SearchAgent(llm=FailingLLM(), searchers=FakeSearchers())
    events = []
    results = await agent.run("any query", top_k=10, on_event=events.append)
    # LLM 失败 → 直查: 原样按 searcher 顺序返回(去重后 2 条, Paper A 在前)
    assert len(results) == 2
    assert results[0].title == "Paper A"
    assert any(e["event"] == "plan" and e.get("direct") for e in events)
```

- [ ] **Step 2: 运行确认失败**

Run: `venv/Scripts/python.exe -m pytest tests/test_research_agent.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.research.agent'`

- [ ] **Step 3: 实现 agent**

创建 `app/research/__init__.py`:

```python
"""文献搜索下载 Agent 业务模块。"""

from app.research.agent import SearchAgent

__all__ = ["SearchAgent"]
```

创建 `app/research/agent.py`:

```python
"""检索 Agent: 规划 → 并行检索 → 去重 → 相关性排序。

LangGraph 4 节点编排; LLM 不可用时降级为直查模式(不阻断搜索)。
"""

import asyncio
import json
import re
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from app.research.schemas import SearchResult

ALL_SOURCES = ["arxiv", "semantic_scholar"]


def _ainvoke_with_retry(llm: Any, prompt: str, retries: int = 3) -> str:
    """LLM 调用带重试: 连接错误/超时等瞬时故障最多重试 3 次(退避)。"""
    import asyncio as _asyncio

    async def _call():
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
                    await _asyncio.sleep(1.5 * (attempt + 1))
        raise last_exc if last_exc is not None else RuntimeError("LLM 调用失败")

    return _call()


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
    queries: list[str]
    sources: list[str]
    direct: bool
    results: list[SearchResult]
    events: list[dict]


PLAN_PROMPT = (
    "你是学术文献检索规划器。把用户的科研需求拆成 1-3 组英文检索词(面向 arXiv/Semantic Scholar), "
    "并选择数据源。只输出合法 JSON: "
    '{"queries": ["..."], "sources": ["arxiv", "semantic_scholar"]}'
)

RANK_PROMPT = (
    "你是学术文献相关性排序器。给定用户需求与候选文献, 输出按相关度从高到低的文献序号。"
    "只输出合法 JSON: {\"ranking\": [{\"index\": 0, \"score\": 10}]}"
)


class SearchAgent:
    """检索 Agent(可注入 llm 与 searchers 便于测试)。"""

    def __init__(self, llm: Any, searchers: Any | None = None, proxy: str = "", timeout: float = 15.0):
        self.llm = llm
        if searchers is None:
            from app.research.searchers import ArxivSearcher, SemanticScholarSearcher

            searchers = [ArxivSearcher(timeout=timeout), SemanticScholarSearcher(timeout=timeout)]
        self.searchers = searchers
        self.proxy = proxy
        self.timeout = timeout
        self._graph = self._build_graph()

    # ---------- 节点 ----------

    async def _plan_query(self, state: ResearchState) -> ResearchState:
        query = state["query"]
        try:
            raw = await _ainvoke_with_retry(self.llm, PLAN_PROMPT + f"\n用户需求: {query}")
            payload = _extract_json(raw)
            queries = [str(q).strip() for q in (payload.get("queries") or [query]) if str(q).strip()]
            sources = [s for s in (payload.get("sources") or ALL_SOURCES) if s in ALL_SOURCES] or ALL_SOURCES
            return {**state, "queries": queries, "sources": sources, "direct": False}
        except Exception:
            return {**state, "queries": [query], "sources": ALL_SOURCES, "direct": True}

    async def _parallel_search(self, state: ResearchState) -> ResearchState:
        top_k = state.get("top_k", 20)
        per_source = max(1, top_k // max(1, len(state["sources"]) * len(state["queries"])))
        sources: list[Any] = []
        for name in state["sources"]:
            match = next(
                (s for s in self.searchers if name in s.__class__.__name__.lower()),
                None,
            )
            if match:
                sources.append(match)
        if not sources:
            sources = list(self.searchers)

        async def fetch(source, q):
            try:
                return await asyncio.wait_for(source.search(q, top_k=per_source), timeout=self.timeout)
            except Exception:
                return []

        tasks = [fetch(s, q) for s in sources for q in state["queries"]]
        batches = await asyncio.gather(*tasks)
        merged: list[SearchResult] = []
        for batch in batches:
            merged.extend(batch)
        return {**state, "results": merged}

    @staticmethod
    def _dedupe(results: list[SearchResult]) -> list[SearchResult]:
        def key(r: SearchResult) -> str:
            if r.doi:
                return f"doi:{r.doi.lower()}"
            title = re.sub(r"[^a-z0-9]", "", r.title.lower())
            return f"title:{title}"

        seen: set[str] = set()
        out: list[SearchResult] = []
        for r in results:
            k = key(r)
            if k in seen:
                continue
            seen.add(k)
            out.append(r)
        return out

    async def _relevance_rank(self, state: ResearchState) -> ResearchState:
        results = state.get("results") or []
        if not results or state.get("direct"):
            return {**state, "results": results}
        try:
            listing = "\n".join(
                f"{i}. {r.title} ({r.year or '?'}) - {r.abstract[:120]}" for i, r in enumerate(results)
            )
            raw = await _ainvoke_with_retry(
                self.llm,
                RANK_PROMPT + f"\n用户需求: {state['query']}\n候选文献:\n{listing}",
            )
            payload = _extract_json(raw)
            ranking = payload.get("ranking") or []
            order = [int(item["index"]) for item in ranking if 0 <= int(item.get("index", -1)) < len(results)]
            ranked = [results[i] for i in order]
            for r in results:  # 补漏: 未被 LLM 覆盖的结果按原序追加
                if r not in ranked:
                    ranked.append(r)
            return {**state, "results": ranked}
        except Exception:
            return {**state, "results": results}

    # ---------- 图构建 ----------

    def _build_graph(self):
        g = StateGraph(ResearchState)
        g.add_node("plan", self._plan_query)
        g.add_node("search", self._parallel_search)
        g.add_node("dedupe", lambda s: {**s, "results": self._dedupe(s.get("results") or [])})
        g.add_node("rank", self._relevance_rank)
        g.set_entry_point("plan")
        g.add_edge("plan", "search")
        g.add_edge("search", "dedupe")
        g.add_edge("dedupe", "rank")
        g.add_edge("rank", "END")
        return g.compile()

    async def run(self, query: str, top_k: int = 20, on_event=None) -> list[SearchResult]:
        state: ResearchState = {"query": query, "top_k": top_k}
        final = await self._graph.ainvoke(state)
        results = final.get("results") or []
        if on_event:
            on_event({"event": "plan", "queries": final.get("queries") or [query], "sources": final.get("sources") or ALL_SOURCES, "direct": bool(final.get("direct"))})
            on_event({"event": "results", "total": len(results)})
        return results
```

- [ ] **Step 4: 运行确认通过**

Run: `venv/Scripts/python.exe -m pytest tests/test_research_agent.py -v`
Expected: PASS

- [ ] **Step 5: Commit**(非 git 仓库则跳过)

```bash
git add app/research/agent.py app/research/__init__.py tests/test_research_agent.py
git commit -m "feat(research): search agent graph"
```

---

## Task 6: Downloader(四级降级 + 限速)

**Files:**
- Create: `app/research/downloader.py`
- Test: `tests/test_research_downloader.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_research_downloader.py
"""下载器四级降级测试(全部 mock)。"""

from pathlib import Path

import httpx
import pytest

from app.research.downloader import Downloader
from app.research.schemas import ImportItem


class FakeBrowser:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    async def download_pdf(self, page_url: str, dest_dir: Path):
        self.calls.append((page_url, dest_dir))
        if not self.ok:
            return None
        target = dest_dir / "vpn.pdf"
        target.write_bytes(b"%PDF-vpn")
        return target


def make_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def pdf_bytes() -> bytes:
    return b"%PDF-1.4 fake"


@pytest.mark.asyncio
async def test_l1_direct_pdf():
    def handler(request):
        assert "files.example.com" in str(request.url)
        return httpx.Response(200, content=pdf_bytes())

    d = Downloader(client=make_client(handler), unpaywall_email="")
    item = ImportItem(source="arxiv", title="T", pdf_url="https://files.example.com/p.pdf", page_url="https://arxiv.org/abs/1")
    out = await d.download(item, Path("tmp"))
    assert out.ok and out.level == "L1"
    assert Path(out.path).read_bytes() == pdf_bytes()


@pytest.mark.asyncio
async def test_l2_unpaywall_fallback():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:  # L1 的 pdf_url 失败
            return httpx.Response(404)
        if "api.unpaywall.org" in str(request.url):  # L2 命中
            return httpx.Response(200, json={"best_oa_location": {"url_for_pdf": "https://oa.example.com/p.pdf"}})
        return httpx.Response(200, content=pdf_bytes())

    d = Downloader(client=make_client(handler), unpaywall_email="me@example.com")
    item = ImportItem(source="arxiv", title="T", doi="10.1000/xyz", pdf_url="https://bad.example.com/p.pdf", page_url="https://x.org")
    out = await d.download(item, Path("tmp"))
    assert out.ok and out.level == "L2"


@pytest.mark.asyncio
async def test_l3_vpn_browser_fallback():
    def handler(request):
        if "api.unpaywall.org" in str(request.url):
            return httpx.Response(200, json={"best_oa_location": None})
        return httpx.Response(404)

    browser = FakeBrowser(ok=True)
    d = Downloader(client=make_client(handler), unpaywall_email="me@example.com", browser=browser)
    item = ImportItem(source="arxiv", title="T", doi="10.1000/xyz", page_url="https://ieeexplore.ieee.org/document/1")
    out = await d.download(item, Path("tmp"))
    assert out.ok and out.level == "L3"
    assert Path(out.path).read_bytes() == b"%PDF-vpn"


@pytest.mark.asyncio
async def test_l4_all_fail():
    def handler(request):
        return httpx.Response(404)

    d = Downloader(client=make_client(handler), unpaywall_email="")
    item = ImportItem(source="arxiv", title="T", page_url="https://publisher.example.com/p")
    out = await d.download(item, Path("tmp"))
    assert not out.ok and out.level == "L4"
```

- [ ] **Step 2: 运行确认失败**

Run: `venv/Scripts/python.exe -m pytest tests/test_research_downloader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.research.downloader'`

- [ ] **Step 3: 实现 downloader**

创建 `app/research/downloader.py`:

```python
"""下载器: 四级降级策略。

L1 arXiv/S2 开放 PDF 直链 → L2 Unpaywall OA 镜像 → L3 VPN 浏览器(Playwright)
→ L4 失败(返回页面链接由前端引导手动下载)。
"""

import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.research.schemas import ImportItem

UNPAYWALL_API = "https://api.unpaywall.org/v2"


@dataclass
class DownloadResult:
    ok: bool
    level: str
    path: str | None = None
    message: str = ""


@dataclass
class Downloader:
    client: httpx.AsyncClient
    unpaywall_email: str = ""
    browser: object | None = None
    delay: float = 0.0

    async def download(self, item: ImportItem, dest_dir: Path) -> DownloadResult:
        dest_dir.mkdir(parents=True, exist_ok=True)
        if item.pdf_url:
            try:
                path = await self._fetch_pdf(item.pdf_url, dest_dir)
                if path is not None:
                    return DownloadResult(ok=True, level="L1", path=str(path))
            except Exception:
                pass
        if item.doi and self.unpaywall_email:
            try:
                path = await self._unpaywall(item.doi, dest_dir)
                if path is not None:
                    return DownloadResult(ok=True, level="L2", path=str(path))
            except Exception:
                pass
        if self.browser is not None and item.page_url:
            try:
                path = await self.browser.download_pdf(item.page_url, dest_dir)
                if path is not None:
                    return DownloadResult(ok=True, level="L3", path=str(path))
            except Exception:
                pass
        return DownloadResult(
            ok=False, level="L4",
            message=f"自动下载失败, 请手动访问: {item.page_url or ''} (DOI: {item.doi or '无'})",
        )

    async def _fetch_pdf(self, url: str, dest_dir: Path) -> Path | None:
        async with self.client.stream("GET", url) as resp:
            resp.raise_for_status()
            filename = _safe_filename(url)
            target = dest_dir / filename
            with open(target, "wb") as f:
                async for chunk in resp.aiter_bytes():
                    f.write(chunk)
        return target if target.exists() and target.stat().st_size > 0 else None

    async def _unpaywall(self, doi: str, dest_dir: Path) -> Path | None:
        resp = await self.client.get(
            f"{UNPAYWALL_API}/{doi}",
            params={"email": self.unpaywall_email},
        )
        resp.raise_for_status()
        payload = resp.json()
        loc = payload.get("best_oa_location") or {}
        pdf_url = loc.get("url_for_pdf") or loc.get("url")
        if not pdf_url:
            return None
        return await self._fetch_pdf(pdf_url, dest_dir)


def _safe_filename(url: str) -> str:
    name = url.rsplit("/", 1)[-1].split("?")[0]
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:180]
    return name or "paper.pdf"
```

- [ ] **Step 4: 运行确认通过**

Run: `venv/Scripts/python.exe -m pytest tests/test_research_downloader.py -v`
Expected: PASS

- [ ] **Step 5: Commit**(非 git 仓库则跳过)

```bash
git add app/research/downloader.py tests/test_research_downloader.py
git commit -m "feat(research): four-level downloader"
```

---

## Task 7: BrowserService(Playwright VPN 会话)

**Files:**
- Create: `app/research/browser.py`
- Test: `tests/test_research_browser.py`

- [ ] **Step 1: 写失败测试(仅状态机, 不启动真实浏览器)**

```python
# tests/test_research_browser.py
"""BrowserService 状态机测试(Playwright 未安装/未启动时返回 none)。"""

import pytest

from app.research.browser import BrowserService


@pytest.mark.asyncio
async def test_status_none_when_playwright_missing(monkeypatch, tmp_path):
    def raise_import():
        raise ImportError("no playwright")

    monkeypatch.setattr("app.research.browser.import_playwright", raise_import)
    service = BrowserService(profile_dir=tmp_path / "profile", vpn_portal_url="https://vpn.swjtu.edu.cn")
    status = await service.status()
    assert status.status == "none"
    assert "Playwright" in status.message


@pytest.mark.asyncio
async def test_close_without_start_is_noop(tmp_path):
    service = BrowserService(profile_dir=tmp_path / "profile", vpn_portal_url="https://vpn.swjtu.edu.cn")
    await service.close()  # 未启动时不抛异常
    assert True
```

- [ ] **Step 2: 运行确认失败**

Run: `venv/Scripts/python.exe -m pytest tests/test_research_browser.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.research.browser'`

- [ ] **Step 3: 实现 browser**

创建 `app/research/browser.py`:

```python
"""Playwright 浏览器服务: 管理学校 VPN 会话与付费墙 PDF 下载。

设计: 首次登录由用户在「有头」浏览器手动完成(不存储密码), 会话持久化在
profile 目录; 之后复用会话自动导航论文页并触发下载。
Playwright 未安装时所有操作优雅降级为 `none` 状态。
"""

import asyncio
from pathlib import Path

from loguru import logger

from app.research.schemas import BrowserStatus

_SESSION_MARK = ".research_session_ok"


def import_playwright():
    """延迟导入, 便于测试 monkeypatch。"""
    return __import__("playwright.async_api", fromlist=["async_playwright"])


class BrowserService:
    def __init__(self, profile_dir: Path, vpn_portal_url: str, timeout: float = 60.0):
        self.profile_dir = profile_dir
        self.vpn_portal_url = vpn_portal_url
        self.timeout = timeout
        self._pw = None
        self._browser = None
        self._context = None

    # ---------- 状态 ----------

    async def status(self) -> BrowserStatus:
        try:
            import_playwright()
        except ImportError:
            return BrowserStatus(status="none", message="Playwright 未安装, 请执行 pip install playwright && playwright install chromium")
        if self._browser is not None and not self._browser.is_connected():
            self._browser = None
        if self._browser is not None:
            return BrowserStatus(status="alive", message="浏览器会话已连接")
        if (self.profile_dir / _SESSION_MARK).exists():
            return BrowserStatus(status="expired", message="会话已保存但浏览器未启动, 可调用 login 复用或重新登录")
        return BrowserStatus(status="none", message="尚未建立 VPN 会话")

    # ---------- 生命周期 ----------

    async def _ensure_browser(self, headless: bool):
        if self._browser is not None and self._browser.is_connected():
            return
        pw_mod = import_playwright()
        self._pw = await pw_mod.async_playwright().start()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._context = await self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=headless,
            accept_downloads=True,
            viewport={"width": 1280, "height": 900},
        )
        self._browser = self._context.browser

    async def login(self) -> BrowserStatus:
        """打开有头浏览器跳转 VPN 门户, 等待用户手动登录并保存会话。"""
        try:
            await self._ensure_browser(headless=False)
            page = self._context.pages[0] if self._context.pages else await self._context.new_page()
            await page.goto(self.vpn_portal_url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
            for _ in range(int(self.timeout * 4)):  # timeout 秒内每秒探测
                await asyncio.sleep(1)
                if len(self._context.pages) == 0:
                    break
                page = self._context.pages[0]
                try:
                    url = page.url
                except Exception:
                    break
                if url and self.vpn_portal_url not in url:
                    (self.profile_dir / _SESSION_MARK).write_text("ok", encoding="utf-8")
                    logger.info(f"VPN 会话已建立: {url}")
                    return BrowserStatus(status="alive", message="VPN 登录完成, 会话已保存")
            return BrowserStatus(status="alive", message="已打开 VPN 门户, 请在浏览器中完成登录")
        except Exception as exc:
            logger.exception("VPN 登录失败")
            return BrowserStatus(status="none", message=f"VPN 登录失败: {exc}")

    async def verify(self) -> BrowserStatus:
        if self._browser is None:
            return await self.status()
        try:
            page = self._context.pages[0] if self._context.pages else await self._context.new_page()
            await page.goto(self.vpn_portal_url, wait_until="domcontentloaded", timeout=15_000)
            return BrowserStatus(status="alive", message="VPN 会话有效")
        except Exception as exc:
            return BrowserStatus(status="expired", message=f"VPN 会话已失效: {exc}")

    async def close(self) -> None:
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                pass
        self._context = None
        self._browser = None
        self._pw = None

    # ---------- 下载 ----------

    async def download_pdf(self, page_url: str, dest_dir: Path) -> Path | None:
        """在 VPN 会话中打开论文页并触发 PDF 下载。"""
        await self._ensure_browser(headless=True)
        dest_dir.mkdir(parents=True, exist_ok=True)
        page = await self._context.new_page()
        try:
            await page.goto(page_url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
            async with page.expect_download(timeout=30_000) as dl_info:
                for selector in (
                    "a[href*='.pdf']",
                    "button:has-text('Download')",
                    "a:has-text('Download PDF')",
                    "a:has-text('PDF')",
                ):
                    locator = page.locator(selector).first
                    if await locator.count() and await locator.is_visible():
                        await locator.click()
                        break
            download = await dl_info.value
            target = dest_dir / (download.suggested_filename or "download.pdf")
            await download.save_as(str(target))
            return target if target.exists() and target.stat().st_size > 0 else None
        except Exception as exc:
            logger.warning(f"VPN 下载失败 {page_url}: {exc}")
            return None
        finally:
            await page.close()
```

- [ ] **Step 4: 运行确认通过**

Run: `venv/Scripts/python.exe -m pytest tests/test_research_browser.py -v`
Expected: PASS(状态机测试不启动真实浏览器)

- [ ] **Step 5: 安装 Playwright(环境任务, 一次性)**

Run:
```bash
venv/Scripts/python.exe -m pip install playwright
venv/Scripts/python.exe -m playwright install chromium
```
Expected: pip 安装成功; chromium 下载完成。若网络受限失败, 不阻断开发(功能降级为 none 状态), 后续再补装。

- [ ] **Step 6: Commit**(非 git 仓库则跳过)

```bash
git add app/research/browser.py tests/test_research_browser.py
git commit -m "feat(research): browser service for VPN downloads"
```

---

## Task 8: ImportService(持久化 + 串行队列 + 解析交接)

**Files:**
- Create: `app/research/service.py`
- Test: `tests/test_research_service.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_research_service.py
"""ImportService 队列与入库交接测试。"""

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.base import Base
from app.models.research import PaperImport
from app.research.downloader import DownloadResult
from app.research.service import ImportService
from app.research.schemas import ImportItem


class FakeDownloader:
    def __init__(self, ok=True, level="L1"):
        self.ok, self.level = ok, level
        self.calls = []

    async def download(self, item, dest_dir):
        self.calls.append(item)
        if not self.ok:
            return DownloadResult(ok=False, level="L4", message="手动下载")
        target = Path(dest_dir) / "paper.pdf"
        target.write_bytes(b"%PDF-fake")
        return DownloadResult(ok=True, level=self.level, path=str(target))


class FakePaperService:
    def __init__(self):
        self.created = []

    async def create_paper(self, filename, content):
        self.created.append((filename, content))
        return type("P", (), {"id": len(self.created)})()

    async def process_paper(self, paper_id):
        return None


@pytest.mark.asyncio
async def test_create_imports_and_run_queue(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'svc.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    service = ImportService(
        session_factory=session_factory,
        downloader=FakeDownloader(ok=True),
        paper_service=FakePaperService(),
        files_dir=tmp_path / "files",
        delay=0.0,
    )
    tasks = await service.create_imports([ImportItem(source="arxiv", title="Paper X", pdf_url="https://x/p.pdf")])
    assert len(tasks) == 1
    assert tasks[0]["status"] == "pending"

    await service.run_pending()

    async with session_factory() as session:
        row = (await session.execute(select(PaperImport).where(PaperImport.id == tasks[0]["id"]))).scalar_one()
        assert row.status == "done"
        assert row.paper_id == 1
        assert row.progress == 100


@pytest.mark.asyncio
async def test_failed_import_keeps_error(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'svc2.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    service = ImportService(
        session_factory=session_factory,
        downloader=FakeDownloader(ok=False),
        paper_service=FakePaperService(),
        files_dir=tmp_path / "files",
        delay=0.0,
    )
    tasks = await service.create_imports([ImportItem(source="arxiv", title="Paper Y", page_url="https://y/p")])
    await service.run_pending()

    async with session_factory() as session:
        row = (await session.execute(select(PaperImport).where(PaperImport.id == tasks[0]["id"]))).scalar_one()
        assert row.status == "failed"
        assert row.error_message != ""


@pytest.mark.asyncio
async def test_retry_resets_status(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'svc3.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    downloader = FakeDownloader(ok=False)
    service = ImportService(
        session_factory=session_factory,
        downloader=downloader,
        paper_service=FakePaperService(),
        files_dir=tmp_path / "files",
        delay=0.0,
    )
    tasks = await service.create_imports([ImportItem(source="arxiv", title="Paper Z", page_url="https://z/p")])
    await service.run_pending()
    downloader.ok = True
    await service.retry(tasks[0]["id"])
    await service.run_pending()

    async with session_factory() as session:
        row = (await session.execute(select(PaperImport).where(PaperImport.id == tasks[0]["id"]))).scalar_one()
        assert row.status == "done"
        assert row.paper_id == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `venv/Scripts/python.exe -m pytest tests/test_research_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.research.service'`

- [ ] **Step 3: 实现 service**

创建 `app/research/service.py`:

```python
"""导入任务服务: paper_imports 持久化 + 串行下载队列 + 解析交接。

队列策略: 串行处理所有 pending 任务, 每项之间按 delay 限速;
下载成功后调用现有 PaperService.create_paper + process_paper 完成入库。
"""

import asyncio
import re
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.research import PaperImport
from app.research.schemas import ImportItem, ImportTaskOut


def _task_out(row: PaperImport) -> ImportTaskOut:
    return ImportTaskOut(
        id=row.id,
        title=row.title,
        source=row.source,
        status=row.status,
        progress=row.progress,
        error_message=row.error_message,
        paper_id=row.paper_id,
        created_at=row.created_at.isoformat() if row.created_at else "",
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
    )


def _safe_filename(title: str, source: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", title)[:80] or "paper"
    return f"{source}_{name}.pdf"


class ImportService:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        downloader,
        paper_service,
        files_dir: Path,
        delay: float = 4.0,
    ):
        self.session_factory = session_factory
        self.downloader = downloader
        self.paper_service = paper_service
        self.files_dir = files_dir
        self.delay = delay
        self._queue_lock = asyncio.Lock()

    async def create_imports(self, items: list[ImportItem]) -> list[ImportTaskOut]:
        async with self.session_factory() as session:
            rows = [
                PaperImport(
                    title=item.title,
                    source=item.source,
                    external_id=item.external_id,
                    doi=item.doi,
                    pdf_url=item.pdf_url,
                    page_url=item.page_url,
                    status="pending",
                    progress=0,
                )
                for item in items
            ]
            session.add_all(rows)
            await session.commit()
            for row in rows:
                await session.refresh(row)
            return [_task_out(r) for r in rows]

    async def list_imports(self) -> list[ImportTaskOut]:
        async with self.session_factory() as session:
            rows = (await session.execute(select(PaperImport).order_by(PaperImport.created_at.desc()))).scalars().all()
            return [_task_out(r) for r in rows]

    async def get_import(self, import_id: int) -> ImportTaskOut | None:
        async with self.session_factory() as session:
            row = await session.get(PaperImport, import_id)
            return _task_out(row) if row else None

    async def _update(self, import_id: int, **fields) -> None:
        async with self.session_factory() as session:
            row = await session.get(PaperImport, import_id)
            if row is None:
                return
            for key, value in fields.items():
                setattr(row, key, value)
            await session.commit()

    async def retry(self, import_id: int) -> ImportTaskOut | None:
        await self._update(import_id, status="pending", progress=0, error_message="")
        return await self.get_import(import_id)

    async def run_pending(self) -> None:
        """串行处理所有 pending 任务(并发安全)。"""
        if self._queue_lock.locked():
            logger.info("导入队列已在运行, 跳过本次触发")
            return
        async with self._queue_lock:
            while True:
                async with self.session_factory() as session:
                    row = (
                        await session.execute(
                            select(PaperImport)
                            .where(PaperImport.status == "pending")
                            .order_by(PaperImport.created_at.asc())
                            .limit(1),
                        )
                    ).scalar_one_or_none()
                if row is None:
                    break
                import_id = row.id
                item = ImportItem(
                    source=row.source or "arxiv",
                    title=row.title,
                    doi=row.doi,
                    pdf_url=row.pdf_url,
                    page_url=row.page_url,
                    external_id=row.external_id,
                )
                await self._process_one(import_id, item)
                if self.delay > 0:
                    await asyncio.sleep(self.delay)

    async def _process_one(self, import_id: int, item: ImportItem) -> None:
        await self._update(import_id, status="downloading", progress=10, error_message="")
        try:
            result = await self.downloader.download(item, self.files_dir)
            if not result.ok:
                await self._update(import_id, status="failed", progress=10, error_message=result.message)
                return
            await self._update(import_id, status="parsing", progress=50, error_message="")
            with open(result.path, "rb") as f:
                content = f.read()
            filename = _safe_filename(item.title, item.source)
            paper = await self.paper_service.create_paper(filename, content)
            await self.paper_service.process_paper(paper.id)
            await self._update(import_id, status="done", progress=100, paper_id=paper.id)
            logger.info(f"导入完成: {item.title} -> paper#{paper.id}")
        except Exception as exc:
            logger.exception(f"导入失败 import#{import_id}: {exc}")
            await self._update(import_id, status="failed", progress=10, error_message=str(exc))
```

- [ ] **Step 4: 运行确认通过**

Run: `venv/Scripts/python.exe -m pytest tests/test_research_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**(非 git 仓库则跳过)

```bash
git add app/research/service.py tests/test_research_service.py
git commit -m "feat(research): import service queue"
```

---

## Task 9: 依赖装配与 API 路由

**Files:**
- Modify: `app/api/dependencies.py`(新增 get_research_service)
- Create: `app/research/router.py`
- Modify: `main.py`(注册 research 路由)
- Test: `tests/test_research_api.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_research_api.py
"""research 路由 API 测试(依赖覆盖, 不访问真实网络/DB)。"""

import httpx
import pytest
from fastapi import FastAPI

from app.research.router import router as research_router


class FakeResearchService:
    def __init__(self):
        self.search_called = False

    async def search(self, query, top_k, on_event):
        self.search_called = True
        on_event({"event": "plan", "queries": [query], "sources": ["arxiv"], "direct": False})
        on_event({"event": "results", "total": 1})

    async def create_imports(self, items):
        return [{"id": 1, "title": items[0].title, "status": "pending", "progress": 0}]

    async def list_imports(self):
        return []

    async def get_import(self, import_id):
        return None

    async def retry(self, import_id):
        return None

    async def browser_status(self):
        return {"status": "none", "message": ""}


def make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(research_router, prefix="/api/v1")
    from app.research.router import get_research_service

    service = FakeResearchService()

    async def override():
        return service

    app.dependency_overrides[get_research_service] = override
    return app, service


@pytest.mark.asyncio
async def test_search_sse_stream():
    app, service = make_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("POST", "/api/v1/research/search", json={"query": "超分", "top_k": 5}) as resp:
            assert resp.status_code == 200
            body = ""
            async for chunk in resp.aiter_text():
                body += chunk
    assert "event: plan" in body
    assert service.search_called


@pytest.mark.asyncio
async def test_create_imports():
    app, _ = make_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/research/imports",
            json={"items": [{"source": "arxiv", "title": "Paper A", "pdf_url": "https://x/p.pdf"}]},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["items"][0]["status"] == "pending"


@pytest.mark.asyncio
async def test_browser_status():
    app, _ = make_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/research/browser/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "none"
```

- [ ] **Step 2: 运行确认失败**

Run: `venv/Scripts/python.exe -m pytest tests/test_research_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.research.router'`

- [ ] **Step 3: 实现依赖装配**

在 `app/api/dependencies.py` 末尾追加(遵循延迟导入风格, import 放在函数内):

```python
# ============================================================
# Research Service 单例
# ============================================================

_research_service = None


def get_research_service():
    """创建文献检索 Agent 服务单例(共享 LLM / PaperService / 浏览器)。"""
    global _research_service
    if _research_service is not None:
        return _research_service
    from pathlib import Path

    import httpx

    from app.research.agent import SearchAgent
    from app.research.browser import BrowserService
    from app.research.downloader import Downloader
    from app.research.searchers import ArxivSearcher, SemanticScholarSearcher, _make_client
    from app.research.service import ImportService

    client = _make_client(settings.research_proxy, settings.research_search_timeout)
    searchers = [
        ArxivSearcher(client=httpx.AsyncClient(timeout=settings.research_search_timeout), timeout=settings.research_search_timeout),
        SemanticScholarSearcher(client=httpx.AsyncClient(timeout=settings.research_search_timeout), timeout=settings.research_search_timeout),
    ]
    downloader = Downloader(
        client=httpx.AsyncClient(timeout=60.0),
        unpaywall_email=settings.unpaywall_email,
        browser=None,
        delay=settings.research_download_delay,
    )
    browser = BrowserService(
        profile_dir=Path(settings.data_dir) / "browser_profile",
        vpn_portal_url=settings.vpn_portal_url,
    )
    downloader.browser = browser

    class ResearchServiceFacade:
        """把 agent/downloader/browser/imports 组装成路由可用的一层。"""

        def __init__(self):
            self.agent = SearchAgent(llm=_get_llm(), proxy=settings.research_proxy, timeout=settings.research_search_timeout)
            self.imports = ImportService(
                session_factory=async_session,
                downloader=downloader,
                paper_service=get_paper_service(),
                files_dir=Path(settings.data_dir) / "papers" / "files",
                delay=settings.research_download_delay,
            )
            self.browser = browser

        async def search(self, query, top_k, on_event):
            return await self.agent.run(query, top_k=top_k, on_event=on_event)

        async def create_imports(self, items):
            tasks = await self.imports.create_imports(items)
            await self.imports.run_pending()
            return tasks

        async def list_imports(self):
            return await self.imports.list_imports()

        async def get_import(self, import_id):
            return await self.imports.get_import(import_id)

        async def retry(self, import_id):
            task = await self.imports.retry(import_id)
            if task:
                await self.imports.run_pending()
            return task

        async def browser_status(self):
            return await self.browser.status()

        async def browser_login(self):
            return await self.browser.login()

        async def browser_verify(self):
            return await self.browser.verify()

        async def browser_close(self):
            await self.browser.close()
            return {"status": "closed"}

    _research_service = ResearchServiceFacade()
    return _research_service
```

注意: searchers 列表应直接复用上面创建的 client: `ArxivSearcher(client=client, timeout=...)` 与 `SemanticScholarSearcher(client=client, timeout=...)`, 避免额外创建 client。

- [ ] **Step 4: 实现 router**

创建 `app/research/router.py`:

```python
"""文献检索 Agent REST 与 SSE 路由。"""

import asyncio
import json
from collections import deque

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.research.schemas import ImportItem


router = APIRouter(prefix="/research", tags=["文献检索"])


def get_research_service():
    """占位依赖: 由 app.api.dependencies 提供真实实现(避免循环 import)。"""
    from app.api.dependencies import get_research_service as _real
    return _real()


def _sse(event: dict) -> str:
    event_type = event.get("event", "message")
    payload = {key: value for key, value in event.items() if key != "event"}
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/search")
async def search(body: dict, service=Depends(get_research_service)):
    query = str(body.get("query", "")).strip()
    if not query:
        raise HTTPException(status_code=400, detail="检索词不能为空")
    top_k = max(1, min(int(body.get("top_k", 20)), 50))

    events: deque = deque()

    async def _run():
        try:
            await service.search(query, top_k=top_k, on_event=events.append)
            events.append({"event": "done"})
        except Exception as exc:
            events.append({"event": "error", "message": str(exc)})
            events.append({"event": "done"})

    task = asyncio.create_task(_run())

    async def stream():
        while True:
            if events:
                yield _sse(events.popleft())
                if not events and task.done():
                    break
            elif task.done():
                if not events:
                    yield _sse({"event": "done"})
                break
            else:
                await asyncio.sleep(0.05)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.post("/imports", status_code=202)
async def create_imports(body: dict, service=Depends(get_research_service)):
    raw_items = body.get("items") or []
    if not raw_items:
        raise HTTPException(status_code=400, detail="至少选择一篇论文")
    items = [ImportItem(**item) for item in raw_items]
    tasks = await service.create_imports(items)
    return {"items": tasks}


@router.get("/imports")
async def list_imports(service=Depends(get_research_service)):
    return {"items": await service.list_imports()}


@router.get("/imports/{import_id}")
async def get_import(import_id: int, service=Depends(get_research_service)):
    task = await service.get_import(import_id)
    if task is None:
        raise HTTPException(status_code=404, detail="导入任务不存在")
    return task


@router.post("/imports/{import_id}/retry")
async def retry_import(import_id: int, service=Depends(get_research_service)):
    task = await service.retry(import_id)
    if task is None:
        raise HTTPException(status_code=404, detail="导入任务不存在")
    return task


@router.get("/browser/status")
async def browser_status(service=Depends(get_research_service)):
    return await service.browser_status()


@router.post("/browser/login")
async def browser_login(service=Depends(get_research_service)):
    return await service.browser_login()


@router.post("/browser/verify")
async def browser_verify(service=Depends(get_research_service)):
    return await service.browser_verify()


@router.post("/browser/close")
async def browser_close(service=Depends(get_research_service)):
    return await service.browser_close()
```

注意: /search 端点按「事件队列 + 后台任务」实现流式。若执行中发现事件顺序问题, 简化为: 内部先收集 results, 再一次性 yield plan → results → done(功能正确优先, 细粒度推送为增强项)。

- [ ] **Step 5: 注册路由到 main.py**

在 `main.py` 的 import 区(paper_router 之后)追加:

```python
from app.research.router import router as research_router
```

在注册区追加:

```python
app.include_router(research_router, prefix="/api/v1")
```

- [ ] **Step 6: 运行确认通过**

Run: `venv/Scripts/python.exe -m pytest tests/test_research_api.py -v`
Expected: PASS

- [ ] **Step 7: 回归验证**

Run: `venv/Scripts/python.exe -m pytest tests -x -q`
Expected: 现有 139+ 用例 + 新增用例全部通过(如有失败先修)

- [ ] **Step 8: Commit**(非 git 仓库则跳过)

```bash
git add app/api/dependencies.py app/research/router.py main.py tests/test_research_api.py
git commit -m "feat(research): api router and dependency wiring"
```

---

## Task 10: 前端类型与 API 客户端

**Files:**
- Create: `web/src/types/research.ts`
- Create: `web/src/api/research.ts`

- [ ] **Step 1: 创建类型定义**

创建 `web/src/types/research.ts`:

```typescript
/** 文献检索 Agent 类型定义 */

export type SearchSource = "arxiv" | "semantic_scholar";

export interface SearchResult {
  source: SearchSource;
  title: string;
  authors: string[];
  year: number | null;
  venue: string;
  abstract: string;
  doi: string | null;
  pdf_url: string | null;
  page_url: string;
  citations: number;
}

export type ImportStatus = "pending" | "downloading" | "parsing" | "done" | "failed";

export interface ImportTask {
  id: number;
  title: string;
  source: string;
  status: ImportStatus;
  progress: number;
  error_message: string;
  paper_id: number | null;
  created_at: string;
  updated_at: string;
}

export type BrowserStatusType = "none" | "alive" | "expired";

export interface BrowserStatus {
  status: BrowserStatusType;
  message: string;
}

export interface ResearchPlanEvent {
  event: "plan";
  queries: string[];
  sources: string[];
  direct?: boolean;
}

export interface ResearchResultsEvent {
  event: "results";
  total: number;
}

export interface ResearchErrorEvent {
  event: "error";
  message: string;
}

export type ResearchProgressEvent =
  | ResearchPlanEvent
  | ResearchResultsEvent
  | ResearchErrorEvent
  | { event: "done" };
```

- [ ] **Step 2: 创建 API 客户端**

创建 `web/src/api/research.ts`(仿照 api/paper.ts 的 http 与 SSE 风格):

```typescript
import http from "./index";
import type {
  BrowserStatus,
  ImportTask,
  ResearchProgressEvent,
  SearchResult,
} from "@/types/research";

export interface ResearchSearchCallbacks {
  onPlan?: (queries: string[], sources: string[], direct?: boolean) => void;
  onResults?: (results: SearchResult[], total: number) => void;
  onDone?: () => void;
  onError?: (message: string) => void;
}

export async function searchResearch(
  query: string,
  topK: number,
  callbacks: ResearchSearchCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`/api/v1/research/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, top_k: topK }),
    signal,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `HTTP ${response.status}`);
  }
  if (!response.body) throw new Error("服务端没有返回流式内容");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      const lines = part.split("\n");
      const eventLine = lines.find((l) => l.startsWith("event:"));
      const dataLine = lines.find((l) => l.startsWith("data:"));
      if (!eventLine || !dataLine) continue;
      const eventName = eventLine.slice(6).trim() as ResearchProgressEvent["event"];
      const data = JSON.parse(dataLine.slice(5).trim() || "{}");
      if (eventName === "plan") {
        callbacks.onPlan?.(data.queries || [], data.sources || [], data.direct);
      } else if (eventName === "results") {
        callbacks.onResults?.(data.items || [], data.total || 0);
      } else if (eventName === "error") {
        callbacks.onError?.(data.message || "搜索失败");
      } else if (eventName === "done") {
        callbacks.onDone?.();
      }
    }
  }
  callbacks.onDone?.();
}

export async function createImports(items: Array<{
  source: string;
  title: string;
  doi?: string | null;
  pdf_url?: string | null;
  page_url?: string | null;
  external_id?: string | null;
}>): Promise<ImportTask[]> {
  const response = await http.post<{ items: ImportTask[] }>("/research/imports", { items });
  return response.data.items;
}

export async function listImports(): Promise<ImportTask[]> {
  const response = await http.get<{ items: ImportTask[] }>("/research/imports");
  return response.data.items;
}

export async function retryImport(importId: number): Promise<ImportTask> {
  const response = await http.post<ImportTask>(`/research/imports/${importId}/retry`);
  return response.data;
}

export async function getBrowserStatus(): Promise<BrowserStatus> {
  const response = await http.get<BrowserStatus>("/research/browser/status");
  return response.data;
}

export async function browserLogin(): Promise<BrowserStatus> {
  const response = await http.post<BrowserStatus>("/research/browser/login");
  return response.data;
}

export async function browserVerify(): Promise<BrowserStatus> {
  const response = await http.post<BrowserStatus>("/research/browser/verify");
  return response.data;
}

export async function browserClose(): Promise<{ status: string }> {
  const response = await http.post<{ status: string }>("/research/browser/close");
  return response.data;
}
```

- [ ] **Step 3: 构建验证**

Run: `pnpm --dir web build`
Expected: 构建成功, 无 TypeScript 报错

- [ ] **Step 4: Commit**(非 git 仓库则跳过)

```bash
git add web/src/types/research.ts web/src/api/research.ts
git commit -m "feat(research): frontend types and api client"
```

---

## Task 11: ResearchSearchPanel 组件

**Files:**
- Create: `web/src/components/research/ResearchSearchPanel.vue`
- Create: `web/src/components/research/ImportQueueDrawer.vue`

- [ ] **Step 1: 创建 ResearchSearchPanel.vue(脚本部分)**

```vue
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { Download, Search, SwitchButton } from "@element-plus/icons-vue";
import {
  browserLogin,
  createImports,
  getBrowserStatus,
  listImports,
  retryImport,
  searchResearch,
} from "@/api/research";
import type { BrowserStatus, ImportTask, SearchResult } from "@/types/research";
import ImportQueueDrawer from "./ImportQueueDrawer.vue";

const query = ref("");
const searching = ref(false);
const results = ref<SearchResult[]>([]);
const selected = ref<Set<number>>(new Set());
const processLines = ref<string[]>([]);
const queueVisible = ref(false);
const imports = ref<ImportTask[]>([]);
const vpnDialogVisible = ref(false);
const browserStatus = ref<BrowserStatus>({ status: "none", message: "" });
let pollTimer: number | undefined;
let abortCtrl: AbortController | null = null;

function pushProcess(line: string) {
  processLines.value.push(line);
}

async function doSearch() {
  const q = query.value.trim();
  if (!q) {
    ElMessage.warning("请输入检索需求");
    return;
  }
  searching.value = true;
  results.value = [];
  selected.value = new Set();
  processLines.value = [];
  abortCtrl = new AbortController();
  try {
    await searchResearch(
      q,
      20,
      {
        onPlan: (queries, sources, direct) => {
          pushProcess(`规划: ${queries.join(" | ")} × [${sources.join(", ")}]${direct ? " (直查模式)" : ""}`);
        },
        onResults: (items) => {
          results.value = items;
          pushProcess(`检索完成, 共 ${items.length} 条`);
        },
        onError: (message) => ElMessage.error(message),
      },
      abortCtrl.signal,
    );
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") return;
    ElMessage.error(error instanceof Error ? error.message : "搜索失败");
  } finally {
    searching.value = false;
  }
}

function toggleSelect(index: number) {
  const next = new Set(selected.value);
  if (next.has(index)) next.delete(index);
  else next.add(index);
  selected.value = next;
}

async function downloadSelected() {
  if (selected.value.size === 0) {
    ElMessage.warning("请先勾选论文");
    return;
  }
  const items = [...selected.value].map((i) => ({
    source: results.value[i].source,
    title: results.value[i].title,
    doi: results.value[i].doi,
    pdf_url: results.value[i].pdf_url,
    page_url: results.value[i].page_url,
    external_id: null,
  }));
  try {
    await createImports(items);
    ElMessage.success(`已提交 ${items.length} 篇下载任务`);
    selected.value = new Set();
    await refreshImports();
    queueVisible.value = true;
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "提交失败");
  }
}

async function refreshImports() {
  try {
    imports.value = await listImports();
  } catch {
    /* 轮询失败静默, 下次重试 */
  }
}

async function handleRetry(importId: number) {
  try {
    await retryImport(importId);
    ElMessage.success("已重新排队");
    await refreshImports();
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "重试失败");
  }
}

async function openVpnLogin() {
  try {
    const status = await browserLogin();
    browserStatus.value = status;
    vpnDialogVisible.value = false;
    ElMessage.success(status.message || "VPN 登录流程已启动");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "VPN 登录失败");
  }
}

onMounted(async () => {
  browserStatus.value = await getBrowserStatus();
  if (browserStatus.value.status === "none" || browserStatus.value.status === "expired") {
    vpnDialogVisible.value = true;
  }
  pollTimer = window.setInterval(refreshImports, 3000);
  refreshImports();
});

onBeforeUnmount(() => {
  abortCtrl?.abort();
  if (pollTimer) window.clearInterval(pollTimer);
});
</script>

<template>
  <div class="research-panel">
    <div class="search-row">
      <el-input
        v-model="query"
        placeholder="输入科研需求，如：轻量级图像超分辨率的注意力机制"
        clearable
        @keyup.enter="doSearch"
      />
      <el-button type="primary" :loading="searching" @click="doSearch">
        <el-icon><Search /></el-icon> 搜索
      </el-button>
    </div>

    <div class="process-strip" v-if="processLines.length">
      <div v-for="(line, i) in processLines" :key="i" class="process-line">· {{ line }}</div>
    </div>

    <div class="result-list" v-loading="searching">
      <article v-for="(r, i) in results" :key="i" class="result-card" :class="{ picked: selected.has(i) }">
        <label class="pick">
          <input type="checkbox" :checked="selected.has(i)" @change="toggleSelect(i)" />
        </label>
        <div class="result-main">
          <h3>{{ r.title }}</h3>
          <p class="result-authors">{{ r.authors.join(" · ") || "作者未知" }}</p>
          <p class="result-abstract">{{ r.abstract }}</p>
          <div class="result-meta">
            <span>{{ r.year || "—" }}</span>
            <span>{{ r.venue || "未收录" }}</span>
            <span v-if="r.citations">被引 {{ r.citations }}</span>
            <el-tag v-if="r.pdf_url" size="small" type="success">开放获取</el-tag>
            <el-tag v-else size="small" type="warning">VPN 下载</el-tag>
            <a v-if="r.page_url" :href="r.page_url" target="_blank" rel="noopener">页面链接</a>
          </div>
        </div>
      </article>
      <el-empty v-if="!searching && results.length === 0" description="输入需求开始检索" />
    </div>

    <div class="action-bar" v-if="results.length">
      <span>已选 {{ selected.size }} 篇</span>
      <el-button type="primary" :disabled="selected.size === 0" @click="downloadSelected">
        <el-icon><Download /></el-icon> 下载并入库
      </el-button>
      <el-button @click="queueVisible = true">导入队列</el-button>
    </div>

    <ImportQueueDrawer v-model="queueVisible" :imports="imports" @retry="handleRetry" />

    <el-dialog v-model="vpnDialogVisible" title="登录西南交通大学 VPN" width="440px">
      <p>下载付费墙论文需要学校 VPN 会话。将在本机打开浏览器，请在弹出的窗口中完成 VPN 登录（仅首次需要）。</p>
      <template #footer>
        <el-button @click="vpnDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="openVpnLogin"><el-icon><SwitchButton /></el-icon> 打开浏览器登录</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.research-panel { padding: 8px 0 30px; }
.search-row { display: flex; gap: 12px; margin-bottom: 14px; }
.search-row :deep(.el-input) { flex: 1; }
.search-row :deep(.el-button--primary) { background: #b66042; border-color: #b66042; }
.process-strip { margin-bottom: 14px; padding: 10px 14px; border-left: 3px solid #193944; background: rgba(255,255,255,.55); color: #51646a; font-size: 12px; }
.process-line { line-height: 1.9; }
.result-list { display: grid; gap: 12px; min-height: 160px; }
.result-card { display: flex; gap: 12px; padding: 16px 18px; border: 1px solid #d5d0c4; background: rgba(255,253,247,.86); transition: .18s; }
.result-card.picked { border-color: #b66042; background: #fff6ec; }
.pick input { width: 16px; height: 16px; accent-color: #b66042; }
.result-main { flex: 1; min-width: 0; }
.result-card h3 { margin: 0 0 6px; font: 17px/1.45 Georgia, "Noto Serif SC", serif; font-weight: 600; color: #1d333c; }
.result-authors { color: #b66042; font: 11px Georgia, serif; }
.result-abstract { margin: 10px 0; color: #69777a; font-size: 12px; line-height: 1.7; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.result-meta { display: flex; gap: 10px; align-items: center; color: #929a98; font: 10px ui-monospace, monospace; flex-wrap: wrap; }
.result-meta a { color: #193944; text-decoration: underline; }
.action-bar { position: sticky; bottom: 0; display: flex; gap: 12px; align-items: center; margin-top: 18px; padding: 12px 16px; background: rgba(241,238,229,.92); backdrop-filter: blur(6px); border-top: 1px solid #d5d0c4; }
.action-bar span { color: #51646a; font-size: 12px; margin-right: auto; }
.action-bar :deep(.el-button--primary) { background: #b66042; border-color: #b66042; }
</style>
```

- [ ] **Step 2: 创建 ImportQueueDrawer.vue**

```vue
<script setup lang="ts">
import { RefreshRight } from "@element-plus/icons-vue";
import type { ImportTask } from "@/types/research";

defineProps<{ imports: ImportTask[] }>();
const emit = defineEmits<{ (e: "retry", id: number): void }>();

const statusMeta: Record<ImportTask["status"], { label: string; tone: string }> = {
  pending: { label: "排队中", tone: "neutral" },
  downloading: { label: "下载中", tone: "working" },
  parsing: { label: "解析中", tone: "working" },
  done: { label: "已入库", tone: "ready" },
  failed: { label: "失败", tone: "failed" },
};
</script>

<template>
  <el-drawer v-model="visible" title="导入队列" size="420px">
    <div class="import-list">
      <div v-for="task in imports" :key="task.id" class="import-item">
        <div class="import-top">
          <span class="import-title">{{ task.title }}</span>
          <span :class="['import-pill', statusMeta[task.status].tone]">{{ statusMeta[task.status].label }}</span>
        </div>
        <el-progress :percentage="task.progress" :stroke-width="6" :show-text="false" />
        <div class="import-foot">
          <span v-if="task.error_message" class="import-error">{{ task.error_message }}</span>
          <span v-else-if="task.paper_id">paper #{{ task.paper_id }}</span>
          <el-button v-if="task.status === 'failed'" text circle size="small" @click="emit('retry', task.id)">
            <el-icon><RefreshRight /></el-icon>
          </el-button>
        </div>
      </div>
      <el-empty v-if="imports.length === 0" description="暂无导入任务" />
    </div>
  </el-drawer>
</template>

<script lang="ts">
import { defineComponent } from "vue";
export default defineComponent({
  props: {
    modelValue: { type: Boolean, default: false },
  },
  emits: ["update:modelValue"],
});
</script>

<style scoped>
.import-list { display: grid; gap: 14px; }
.import-item { padding: 12px 14px; border: 1px solid #e1ddd2; border-radius: 8px; }
.import-top { display: flex; justify-content: space-between; gap: 10px; margin-bottom: 8px; }
.import-title { font-size: 13px; line-height: 1.5; color: #1d333c; }
.import-pill { font-size: 10px; white-space: nowrap; }
.import-pill.working { color: #d79245; }
.import-pill.ready { color: #3d8e75; }
.import-pill.failed { color: #bc513f; }
.import-foot { display: flex; justify-content: space-between; align-items: center; margin-top: 6px; font-size: 11px; color: #929a98; }
.import-error { color: #ad4f3c; font-size: 11px; }
</style>
```

注意: `el-drawer v-model="visible"` 需要响应式 prop 桥接。上面用第二段 `<script lang="ts">` 定义 modelValue prop, 并在 setup 中 `const visible = computed(...)` 桥接; 执行时若 TS 报错, 改用 `v-model:modelValue` 与直接读取 `modelValue` 的写法(以 Vue 3.5 兼容为准)。

- [ ] **Step 3: 构建验证**

Run: `pnpm --dir web build`
Expected: 构建成功, 无 TypeScript 报错

- [ ] **Step 4: Commit**(非 git 仓库则跳过)

```bash
git add web/src/components/research/
git commit -m "feat(research): search panel and import queue drawer"
```

---

## Task 12: 论文库页集成(文献检索标签)

**Files:**
- Modify: `web/src/views/PaperListView.vue`

- [ ] **Step 1: 修改脚本区**

在 `<script setup>` 的 import 区追加:

```typescript
import ResearchSearchPanel from "@/components/research/ResearchSearchPanel.vue";
const activeTab = ref<"library" | "search">("library");
```

(注意: 文件已 import ref, 无需重复; 若未 import 则补 `import { ref } from "vue";`。)

- [ ] **Step 2: 修改模板区 — 加标签栏**

在 `<header class="hero-band">...</header>` 之后、`<section class="drop-zone">` 之前插入:

```html
<div class="library-tabs">
  <button type="button" :class="{ active: activeTab === 'library' }" @click="activeTab = 'library'">论文档案</button>
  <button type="button" :class="{ active: activeTab === 'search' }" @click="activeTab = 'search'">文献检索</button>
</div>
```

- [ ] **Step 3: 修改模板区 — 包裹库内容并插入面板**

把 `<section class="drop-zone">` 到 `</section>`(paper-grid 结束)整段用 `<div v-show="activeTab === 'library'">` 包裹; 紧接其后插入:

```html
<ResearchSearchPanel v-if="activeTab === 'search'" />
```

- [ ] **Step 4: 修改样式区**

在 `<style scoped>` 中追加:

```css
.library-tabs { display: flex; gap: 8px; margin: 20px 0 6px; }
.library-tabs button { padding: 7px 18px; border: 1px solid #c9c4b8; border-radius: 999px; background: rgba(255,255,255,.6); color: #51646a; font-size: 13px; cursor: pointer; transition: .18s; }
.library-tabs button:hover { border-color: #b66042; color: #b66042; }
.library-tabs button.active { background: #193944; border-color: #193944; color: #f2eee3; }
```

- [ ] **Step 5: 构建验证**

Run: `pnpm --dir web build`
Expected: 构建成功

- [ ] **Step 6: 手动验证清单**

1. 启动后端: `venv/Scripts/python.exe -m uvicorn main:app --reload --port 8000`;
2. 启动前端: `cd web && pnpm dev`;
3. 打开论文库页 → 「文献检索」标签;
4. 输入「lightweight super-resolution attention」→ 搜索 → 结果卡片出现, 开放获取徽标正确;
5. 勾选 1-2 篇 → 下载并入库 → 导入队列抽屉出现, 状态流转 pending→downloading→parsing→done;
6. 刷新论文库「论文档案」标签 → 新论文出现且可打开精读工作台;
7. 付费墙论文(VPN 下载徽标)→ 首次弹 VPN 登录 → 浏览器打开 vpn.swjtu.edu.cn → 手动登录 → 后续自动下载。

- [ ] **Step 7: Commit**(非 git 仓库则跳过)

```bash
git add web/src/views/PaperListView.vue
git commit -m "feat(research): integrate search tab into paper library"
```

---

## Task 13: 端到端验证与回归

**Files:** 无新增

- [ ] **Step 1: 后端全量回归**

Run: `venv/Scripts/python.exe -m pytest tests -q`
Expected: 全部通过(原有 139+ 与新用例)

- [ ] **Step 2: 前端构建**

Run: `pnpm --dir web build`
Expected: 构建成功

- [ ] **Step 3: 真实网络冒烟(可选, 需网络)**

Run: `venv/Scripts/python.exe -c "import asyncio; from app.research.searchers import ArxivSearcher; asyncio.run(ArxivSearcher().search('lightweight super-resolution', top_k=3))"`
Expected: 打印 3 条 arXiv 结果(网络不可用时跳过此步)

- [ ] **Step 4: 依赖清单更新**

修改 `requirements.txt`, 在 Utilities 段追加:

```
playwright>=1.44.0
```

- [ ] **Step 5: Commit**(非 git 仓库则跳过)

```bash
git add requirements.txt
git commit -m "chore: add playwright dependency"
```

---

## 自审记录(writing-plans 要求)

- **Spec 覆盖**: 配置(Task1)✓ 模型(Task2)✓ schemas(Task3)✓ Searcher(Task4)✓ Agent(Task5)✓ Downloader L1-L4(Task6)✓ BrowserService/VPN(Task7)✓ ImportService 队列(Task8)✓ API(Task9)✓ 前端类型/API(Task10)✓ 前端面板(Task11)✓ 论文库集成(Task12)✓ 回归(Task13)✓。spec 第 7 节全部配置项、第 6 节全部端点、第 9 节错误处理(LLM 降级/单源降级/下载降级/VPN 过期提示/限速/解析失败复用)均有对应实现。
- **占位符扫描**: 无 TBD/TODO; 每步含完整代码与命令。
- **类型一致性**: SearchResult/ImportItem/ImportTaskOut/BrowserStatus 在 schemas.py、agent.py、router.py、前端 research.ts 中字段一致(source/title/year/venue/abstract/doi/pdf_url/page_url/citations; id/title/source/status/progress/error_message/paper_id/created_at/updated_at)。SearchAgent.run(query, top_k, on_event) 与 router 调用一致; ImportService 方法名(create_imports/list_imports/get_import/retry/run_pending) 与 facade 及 router 一致; BrowserService.status/login/verify/close/download_pdf 一致。
