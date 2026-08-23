# Single Paper Deep Reading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an end-to-end single-paper deep-reading workspace to GraphRAG with PDF parsing, page-aware retrieval, Chinese reports, grounded citations, follow-up tasks, persistence, and PDF page navigation.

**Architecture:** Keep the existing knowledge-base workflow unchanged and add an independent `app/paper` vertical module. Store paper metadata, page-aware chunks, tasks, artifacts, and conversations in SQLite; use a per-paper FAISS+BM25 index and a six-node LangGraph report workflow. Add a Vue 3 paper list and three-column reading workspace that consume REST and SSE APIs.

**Tech Stack:** Python 3.10+, FastAPI, SQLAlchemy asyncio, LangGraph, pdfplumber, FAISS, rank-bm25, OpenAI-compatible LLM/Embedding APIs, Vue 3, TypeScript, Element Plus, SSE.

---

## File map

- `app/models/paper.py`: SQLAlchemy paper, section, chunk, task, artifact, and message tables.
- `app/paper/schemas.py`: typed domain and API payloads, including structured citations.
- `app/paper/parser.py`: page extraction, scan rejection, metadata and section heuristics.
- `app/paper/chunker.py`: page-aware, overlap-safe chunk generation.
- `app/paper/retriever.py`: per-paper FAISS+BM25 indexing, persistence, search, and deletion.
- `app/paper/prompts.py`: report and on-demand task prompts.
- `app/paper/citations.py`: citation validation and unsupported-claim downgrade.
- `app/paper/state.py`, `nodes.py`, `graph.py`: six-node report workflow.
- `app/paper/service.py`: upload pipeline, task execution, persistence, status transitions, and cleanup.
- `app/paper/router.py`: paper REST/SSE/PDF endpoints.
- `app/api/dependencies.py`, `main.py`, `app/models/__init__.py`: dependency assembly and route/model registration.
- `web/src/types/paper.ts`, `web/src/api/paper.ts`: frontend contract.
- `web/src/views/PaperListView.vue`: upload, progress, list, retry, delete.
- `web/src/views/PaperWorkspaceView.vue`: three-column reading workspace.
- `web/src/components/paper/*`: sections, report/tasks, citations, PDF viewer.
- `web/src/router/index.ts`, `web/src/router/menu.ts`, `web/src/layouts/components/AppSidebar.vue`: navigation.
- `tests/test_paper_*.py`: backend unit/integration coverage.

### Task 1: Persist the paper domain

**Files:**
- Create: `app/models/paper.py`
- Modify: `app/models/__init__.py`
- Test: `tests/test_paper_models.py`

- [ ] **Step 1: Write the failing database test**

```python
async def test_paper_domain_persists_sections_chunks_tasks_and_artifacts(tmp_path):
    engine, session_factory = await make_test_database(tmp_path)
    async with session_factory() as session:
        paper = Paper(original_filename="sample.pdf", stored_filename="p.pdf", status="parsing")
        session.add(paper)
        await session.flush()
        session.add_all([
            PaperSection(paper_id=paper.id, title="Methods", ordinal=1, page_start=2, page_end=4),
            PaperChunk(paper_id=paper.id, chunk_id="p1-c1", section="Methods", page_start=2,
                       page_end=2, ordinal=0, content="method evidence"),
            PaperTask(paper_id=paper.id, task_type="report", status="pending", input_json="{}"),
        ])
        await session.commit()
    async with session_factory() as session:
        loaded = await session.get(Paper, paper.id)
        assert loaded.status == "parsing"
    await engine.dispose()
```

- [ ] **Step 2: Run the test and confirm it fails because the models do not exist**

Run: `venv\Scripts\python.exe -m pytest tests/test_paper_models.py -q`

- [ ] **Step 3: Implement six ORM entities and register them**

Use integer foreign keys with `ondelete="CASCADE"`, indexed `paper_id`, UTC timestamps, JSON stored in `Text`, and these stable state values: paper `uploaded|parsing|indexing|reporting|ready|failed`; task `pending|running|completed|failed`.

```python
class Paper(Base):
    __tablename__ = "papers"
    id = Column(Integer, primary_key=True, autoincrement=True)
    original_filename = Column(String(256), nullable=False)
    stored_filename = Column(String(256), nullable=False)
    title = Column(String(512), nullable=False, default="")
    authors_json = Column(Text, nullable=False, default="[]")
    abstract = Column(Text, nullable=False, default="")
    language = Column(String(16), nullable=False, default="unknown")
    page_count = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False, default="uploaded")
    error_code = Column(String(64), nullable=False, default="")
    error_message = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
```

Define `PaperSection`, `PaperChunk`, `PaperTask`, `PaperArtifact`, and `PaperMessage` with the fields specified in the design. Import all six classes from `app/models/__init__.py`.

- [ ] **Step 4: Run the model test and the existing database tests**

Run: `venv\Scripts\python.exe -m pytest tests/test_paper_models.py tests/test_database.py -q`

### Task 2: Extract pages, sections, and page-aware chunks

**Files:**
- Create: `app/paper/__init__.py`
- Create: `app/paper/schemas.py`
- Create: `app/paper/parser.py`
- Create: `app/paper/chunker.py`
- Test: `tests/test_paper_parser.py`

- [ ] **Step 1: Write failing tests for text PDFs, scan rejection, and page boundaries**

```python
def test_chunker_preserves_page_truth():
    pages = [PaperPage(page=1, text="A" * 120), PaperPage(page=2, text="B" * 120)]
    chunks = chunk_pages(pages, paper_id=7, chunk_size=100, overlap=20)
    assert chunks[0].page_start == 1
    assert chunks[-1].page_end == 2
    assert all(c.paper_id == 7 and c.content for c in chunks)

def test_parser_rejects_pdf_without_extractable_text(monkeypatch, tmp_path):
    monkeypatch.setattr("pdfplumber.open", fake_empty_pdf)
    with pytest.raises(UnsupportedScanError):
        parse_pdf(tmp_path / "scan.pdf")
```

- [ ] **Step 2: Run the parser tests and verify the expected missing-module failure**

Run: `venv\Scripts\python.exe -m pytest tests/test_paper_parser.py -q`

- [ ] **Step 3: Implement page-first parsing and deterministic chunking**

```python
@dataclass(frozen=True)
class PaperPage:
    page: int
    text: str

def parse_pdf(path: Path) -> ParsedPaper:
    with pdfplumber.open(path) as pdf:
        pages = [PaperPage(i, normalize_text(page.extract_text() or ""))
                 for i, page in enumerate(pdf.pages, 1)]
    if sum(len(p.text.strip()) for p in pages) < 80:
        raise UnsupportedScanError("PDF 中没有足够的可提取文本")
    return ParsedPaper(pages=pages, page_count=len(pages), language=detect_language(pages),
                       metadata=infer_metadata(pages), sections=infer_sections(pages))
```

Chunk each page independently first, carry `page_start/page_end`, then add bounded overlap without losing page ownership. Detect common Chinese/English section headings and map each chunk to the nearest preceding heading.

- [ ] **Step 4: Run parser tests and existing parser regression tests**

Run: `venv\Scripts\python.exe -m pytest tests/test_paper_parser.py tests/test_parser.py -q`

### Task 3: Build an isolated per-paper hybrid index

**Files:**
- Create: `app/paper/retriever.py`
- Test: `tests/test_paper_retriever.py`

- [ ] **Step 1: Write failing search and cleanup tests**

```python
async def test_search_never_returns_another_papers_chunk(fake_embedding, tmp_path):
    store = PaperRetriever(fake_embedding, tmp_path)
    await store.build(1, [chunk(1, "alpha method")])
    await store.build(2, [chunk(2, "alpha unrelated")])
    results = await store.search(1, "alpha", k=5)
    assert results and {item.chunk.paper_id for item in results} == {1}

async def test_delete_removes_all_index_files(fake_embedding, tmp_path):
    store = PaperRetriever(fake_embedding, tmp_path)
    await store.build(1, [chunk(1, "evidence")])
    store.delete(1)
    assert not (tmp_path / "1").exists()
```

- [ ] **Step 2: Run and verify the missing retriever failure**

Run: `venv\Scripts\python.exe -m pytest tests/test_paper_retriever.py -q`

- [ ] **Step 3: Implement build, load, hybrid search, section filter, and delete**

Store each paper under `data/papers/index/{paper_id}/` with `faiss.index` and `chunks.pkl`. Normalize FAISS and BM25 scores before weighted fusion. Search accepts `paper_id`, `query`, `k`, and optional `section`; it must open only that paper directory.

```python
async def search(self, paper_id: int, query: str, k: int = 8,
                 section: str | None = None) -> list[PaperSearchResult]:
    index = self._load(paper_id)
    candidates = index.hybrid_search(query, k * 3)
    filtered = [r for r in candidates if section is None or r.chunk.section == section]
    return filtered[:k]
```

- [ ] **Step 4: Run retrieval tests**

Run: `venv\Scripts\python.exe -m pytest tests/test_paper_retriever.py tests/test_rag.py -q`

### Task 4: Validate structured citations

**Files:**
- Create: `app/paper/citations.py`
- Test: `tests/test_paper_citations.py`

- [ ] **Step 1: Write failing tests for valid, foreign, invented-page, and missing-quote citations**

```python
def test_valid_citation_requires_matching_quote_and_page():
    validator = CitationValidator([chunk(1, "The accuracy is 95%.", page=6)])
    result = validator.validate(Citation(paper_id=1, page=6, chunk_id="c1",
                                         quote="accuracy is 95%"), paper_id=1)
    assert result.valid is True

def test_invalid_page_is_downgraded_not_fabricated():
    validator = CitationValidator([chunk(1, "evidence", page=2)])
    result = validator.validate(Citation(paper_id=1, page=99, chunk_id="c1", quote="evidence"), 1)
    assert result.valid is False
    assert result.citation.page is None
```

- [ ] **Step 2: Run and confirm expected failure**

Run: `venv\Scripts\python.exe -m pytest tests/test_paper_citations.py -q`

- [ ] **Step 3: Implement normalized quote matching and safe downgrade**

Normalize Unicode whitespace and punctuation for comparison. A citation is valid only when chunk, paper, page range, and quote all match. Return a reason code and a citation with `page=None` when the page cannot be proven.

- [ ] **Step 4: Run citation tests**

Run: `venv\Scripts\python.exe -m pytest tests/test_paper_citations.py -q`

### Task 5: Generate and persist the automatic deep-reading report

**Files:**
- Create: `app/paper/state.py`
- Create: `app/paper/prompts.py`
- Create: `app/paper/nodes.py`
- Create: `app/paper/graph.py`
- Create: `app/paper/service.py`
- Test: `tests/test_paper_graph.py`
- Test: `tests/test_paper_service.py`

- [ ] **Step 1: Write failing workflow tests with a deterministic fake LLM**

```python
async def test_report_contains_required_sections_and_verified_citations(fake_llm, paper_context):
    graph = build_paper_graph()
    result = await graph.ainvoke(paper_context, {"configurable": {"llm": fake_llm}})
    assert set(result["report"]) >= {"background", "method", "experiments", "results",
                                     "innovations", "limitations", "future_questions", "terms"}
    assert all(c["verified"] for c in result["citations"])
```

- [ ] **Step 2: Run and verify the missing workflow failure**

Run: `venv\Scripts\python.exe -m pytest tests/test_paper_graph.py tests/test_paper_service.py -q`

- [ ] **Step 3: Implement the six-node graph and JSON-only prompts**

Create nodes named `metadata_extract`, `section_analyze`, `contribution_extract`, `report_synthesize`, `citation_verify`, and `artifact_persist`. LLM prompts must request JSON and refer to evidence by supplied chunk IDs, never ask the model to invent page numbers. `citation_verify` retries synthesis once with validation errors, then changes unsupported claims to `原文未提供充分证据`.

```python
builder = StateGraph(PaperReadingState)
builder.add_node("metadata_extract", metadata_extract_node)
builder.add_node("section_analyze", section_analyze_node)
builder.add_node("contribution_extract", contribution_extract_node)
builder.add_node("report_synthesize", report_synthesize_node)
builder.add_node("citation_verify", citation_verify_node)
builder.add_node("artifact_persist", artifact_persist_node)
builder.add_edge(START, "metadata_extract")
builder.add_edge("metadata_extract", "section_analyze")
builder.add_edge("section_analyze", "contribution_extract")
builder.add_edge("contribution_extract", "report_synthesize")
builder.add_edge("report_synthesize", "citation_verify")
builder.add_conditional_edges("citation_verify", route_after_verify,
                              {"retry": "report_synthesize", "persist": "artifact_persist"})
builder.add_edge("artifact_persist", END)
```

- [ ] **Step 4: Implement transactional upload status transitions**

`PaperService.create_paper()` saves the PDF, creates the row, parses pages and sections, persists chunks, builds the index, runs the graph, stores the report artifact, and emits progress events. It sets `ready` only after the report artifact exists and citations have been checked. It maps no-text PDFs to `unsupported_scan`, index failures to `index_failed`, and LLM failures to a retryable failed task without deleting parsing/index data.

- [ ] **Step 5: Run graph and service tests**

Run: `venv\Scripts\python.exe -m pytest tests/test_paper_graph.py tests/test_paper_service.py -q`

### Task 6: Add paper REST, SSE, and PDF endpoints

**Files:**
- Create: `app/paper/router.py`
- Modify: `app/api/dependencies.py`
- Modify: `main.py`
- Test: `tests/test_paper_api.py`

- [ ] **Step 1: Write failing API tests**

```python
def test_upload_rejects_non_pdf(client):
    response = client.post("/api/v1/papers", files={"file": ("x.txt", b"x", "text/plain")})
    assert response.status_code == 400

def test_pdf_endpoint_supports_inline_view(client, seeded_paper):
    response = client.get(f"/api/v1/papers/{seeded_paper.id}/pdf")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
```

- [ ] **Step 2: Run and confirm 404/missing-router failures**

Run: `venv\Scripts\python.exe -m pytest tests/test_paper_api.py -q`

- [ ] **Step 3: Implement endpoints and dependency assembly**

Add:

- `POST /api/v1/papers` multipart upload returning the created paper and streaming URL.
- `GET /api/v1/papers` list.
- `GET /api/v1/papers/{paper_id}` detail, sections, latest report, and artifacts.
- `GET /api/v1/papers/{paper_id}/events` upload/report progress stream.
- `POST /api/v1/papers/{paper_id}/tasks/stream` task SSE.
- `GET /api/v1/papers/{paper_id}/pdf` inline PDF.
- `DELETE /api/v1/papers/{paper_id}` cascade cleanup.

Use validated resolved paths under `data/papers/files`; never serve a user-provided path. Register the paper router and call `init_db()` during startup before services run.

- [ ] **Step 4: Run API and health tests**

Run: `venv\Scripts\python.exe -m pytest tests/test_paper_api.py tests/test_api.py tests/test_health.py -q`

### Task 7: Implement on-demand Q&A, translation, notes, and presentation outline

**Files:**
- Modify: `app/paper/prompts.py`
- Modify: `app/paper/service.py`
- Modify: `app/paper/router.py`
- Test: `tests/test_paper_tasks.py`

- [ ] **Step 1: Write failing tests for all four task types and conversation recovery**

```python
@pytest.mark.parametrize("task_type", ["qa", "translation", "notes", "presentation"])
async def test_task_is_grounded_and_persisted(task_type, service, ready_paper):
    events = [event async for event in service.run_task(ready_paper.id, task_type,
                                                        input_text="Methods", session_id="s1")]
    done = next(e for e in events if e["event"] == "done")
    assert done["artifact_id"]
    assert all(c["paper_id"] == ready_paper.id for c in done["citations"])
```

- [ ] **Step 2: Run and verify missing task behavior**

Run: `venv\Scripts\python.exe -m pytest tests/test_paper_tasks.py -q`

- [ ] **Step 3: Implement the shared task pipeline**

Validate task type, retrieve only from the current paper (and selected section for translation), inject the last ten `PaperMessage` records for Q&A, stream progress/token/done events, verify citations, persist user/assistant messages and a `PaperArtifact`, and mark the task failed with a retryable error when the LLM fails.

- [ ] **Step 4: Run task tests**

Run: `venv\Scripts\python.exe -m pytest tests/test_paper_tasks.py -q`

### Task 8: Add frontend contracts, list, upload progress, and navigation

**Files:**
- Create: `web/src/types/paper.ts`
- Create: `web/src/api/paper.ts`
- Create: `web/src/views/PaperListView.vue`
- Modify: `web/src/router/index.ts`
- Modify: `web/src/router/menu.ts`
- Modify: `web/src/layouts/components/AppSidebar.vue`

- [ ] **Step 1: Add strict TypeScript contracts and API functions**

```ts
export interface PaperCitation {
  paper_id: number;
  paper_title: string;
  page: number | null;
  section: string;
  chunk_id: string;
  quote: string;
  verified: boolean;
}

export type PaperStatus = "uploaded" | "parsing" | "indexing" | "reporting" | "ready" | "failed";
```

Implement upload, list, detail, delete, retry, and task-stream calls using the existing HTTP client and `fetch` SSE convention.

- [ ] **Step 2: Build the paper list and upload experience**

The view accepts only `.pdf`, shows a staged progress timeline, displays title/authors/language/pages/status, supports open/retry/delete, and explains `unsupported_scan` in plain Chinese.

- [ ] **Step 3: Register routes and sidebar icon**

Add `/papers` and `/papers/:paperId`, menu title `论文助手`, and an Element Plus reading icon.

- [ ] **Step 4: Run TypeScript build**

Run: `pnpm --dir web run build`

Expected: exit code 0.

### Task 9: Build the three-column reading workspace

**Files:**
- Create: `web/src/views/PaperWorkspaceView.vue`
- Create: `web/src/components/paper/PaperOutline.vue`
- Create: `web/src/components/paper/PaperReport.vue`
- Create: `web/src/components/paper/PaperTaskPanel.vue`
- Create: `web/src/components/paper/PaperCitationList.vue`
- Create: `web/src/components/paper/PaperPdfViewer.vue`

- [ ] **Step 1: Implement the workspace state contract**

Load paper detail by route ID, keep `activeSection`, `activeArtifact`, `activeTask`, `pdfPage`, and streaming state in the workspace. Route citation clicks through one handler:

```ts
function openCitation(citation: PaperCitation) {
  if (citation.page !== null) pdfPage.value = citation.page;
}
```

- [ ] **Step 2: Implement the left outline and center artifact/task area**

Left column shows metadata and page-ranged sections. Center column provides tabs for report, Q&A, translation, notes, and presentation outline, renders verified citations as buttons, and restores saved artifacts after refresh.

- [ ] **Step 3: Implement the PDF viewer**

Use the browser PDF renderer with `iframe :src="`${pdfUrl}#page=${page}`"`; replace the iframe key when the page changes so citation navigation is reliable.

- [ ] **Step 4: Add responsive behavior and accessible empty/error states**

Desktop uses `280px minmax(420px, 1fr) minmax(380px, 42vw)`. Below 1100px, move the PDF into a drawer while keeping citation navigation functional.

- [ ] **Step 5: Run the frontend build**

Run: `pnpm --dir web run build`

Expected: exit code 0.

### Task 10: Verify the complete vertical slice and cleanup invariants

**Files:**
- Create: `tests/test_paper_e2e.py`
- Modify: `README.md`

- [ ] **Step 1: Add an end-to-end backend test**

Create a small two-page text PDF fixture, upload it, wait for `ready`, assert persisted pages/sections/chunks/report, run one Q&A task, verify every page citation is in range, fetch the PDF, delete the paper, and assert database rows, source file, artifact, and index directory are gone.

- [ ] **Step 2: Run the full backend suite**

Run: `venv\Scripts\python.exe -m pytest -q`

Expected: all tests pass with zero failures.

- [ ] **Step 3: Run the production frontend build**

Run: `pnpm --dir web run build`

Expected: TypeScript and Vite build complete with exit code 0.

- [ ] **Step 4: Perform local browser acceptance**

Start backend and frontend, upload a real text PDF, observe every processing stage, open the report, run Q&A/translation/notes/presentation, click citations to navigate pages, refresh to confirm persistence, delete the paper, and confirm it disappears without stale PDF/index access.

- [ ] **Step 5: Document operation and limitations**

Add README instructions for the paper assistant, supported text PDFs, scan limitation, model configuration reuse, storage locations, and cleanup behavior.

## Execution note

This project copy has no `.git` directory, so worktree isolation and per-task commits are unavailable. Execute in place, preserve unrelated files, and rely on red-green tests plus file-level review checkpoints before each task transition.
