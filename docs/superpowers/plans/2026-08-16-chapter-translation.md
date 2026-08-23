# Chapter Tree and Full Translation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-section/Top-K translation behavior with a hierarchical chapter tree and ordered, resumable full-chapter Chinese translation.

**Architecture:** Parse numbered and layout-like headings into ordered section levels, split page text at heading boundaries so chunks retain the correct section, and route translation tasks through a dedicated sequential block pipeline rather than the retriever. Persist each completed translation block in a new table and expose its progress through the existing SSE endpoint; render the section list as a Vue tree with a Chinese-only reading pane.

**Tech Stack:** Python 3.10+, FastAPI, SQLAlchemy async, SQLite, pdfplumber, pytest, Vue 3, TypeScript, Element Plus, SSE.

---

### Task 1: Hierarchical section recognition

**Files:**
- Modify: `app/paper/parser.py`
- Modify: `app/paper/schemas.py`
- Test: `tests/test_paper_parser.py`

- [ ] **Step 1: Add failing parser tests**

Add tests with same-page headings, Roman numeral headings, subsection numbering, and a false-positive plain `Methods` table label. Assert the normalized titles, levels, ordering, and page ranges:

```python
def test_infer_sections_builds_hierarchy_and_ignores_plain_table_label():
    pages = [
        PaperPage(page_number=1, text="Abstract\nSummary\nI. INTRODUCTION\nBody"),
        PaperPage(page_number=2, text="Methods\nII. RELATED WORK\nBody\nA. Image SR\nBody"),
        PaperPage(page_number=3, text="III. METHOD\nBody\n3.1 Cascade Prompt Block\nBody"),
        PaperPage(page_number=4, text="IV. EXPERIMENTS\nBody\nV. CONCLUSION\nBody"),
        PaperPage(page_number=5, text="REFERENCES\n[1] ..."),
    ]
    sections = infer_sections(pages)
    assert [item.title for item in sections] == [
        "Abstract", "I. INTRODUCTION", "II. RELATED WORK", "A. Image SR",
        "III. METHOD", "3.1 Cascade Prompt Block", "IV. EXPERIMENTS",
        "V. CONCLUSION", "REFERENCES",
    ]
    assert [item.level for item in sections] == [1, 1, 1, 2, 1, 2, 1, 1, 1]
    assert sections[1].page_start == 1
    assert sections[-1].page_end == 5
```

- [ ] **Step 2: Verify the parser test fails**

Run: `venv\Scripts\python.exe -m pytest tests/test_paper_parser.py::test_infer_sections_builds_hierarchy_and_ignores_plain_table_label -v`

Expected: FAIL because the current parser accepts only canonical exact titles and only one title per page.

- [ ] **Step 3: Implement heading classification**

Add a `SectionHeading` helper and classifiers that:

```python
_ROMAN_HEADING = re.compile(r"^(?P<number>[IVXLC]+)[.、]?\s+(?P<title>.+)$", re.I)
_DECIMAL_HEADING = re.compile(r"^(?P<number>\d+(?:\.\d+)+|\d+)[.、]?\s+(?P<title>.+)$")
_LETTER_HEADING = re.compile(r"^(?P<number>[A-Z])[.、]\s+(?P<title>.+)$")

def _heading_level(number: str | None) -> int:
    if not number:
        return 1
    if re.fullmatch(r"[A-Z]", number, re.I):
        return 2
    if re.fullmatch(r"[IVXLC]+", number, re.I):
        return 1
    return min(number.count(".") + 1, 3)
```

Scan every line, allow multiple headings on one page, prefer numbered headings over unnumbered generic labels, reject lines over 120 characters, and preserve canonical normalization when known. Compute `page_end` from the next section start.

- [ ] **Step 4: Run parser tests**

Run: `venv\Scripts\python.exe -m pytest tests/test_paper_parser.py -v`

Expected: all parser tests PASS.

### Task 2: Split chunks at section boundaries

**Files:**
- Modify: `app/paper/chunker.py`
- Test: `tests/test_paper_parser.py`

- [ ] **Step 1: Add a failing chunk-order test**

```python
def test_chunk_pages_assigns_same_page_text_to_the_correct_section():
    pages = [PaperPage(page_number=1, text="I. INTRODUCTION\nintro body\nII. METHOD\nmethod body")]
    sections = infer_sections(pages)
    chunks = chunk_pages(1, pages, sections, chunk_size=200, overlap=0)
    assert [(chunk.section, chunk.content) for chunk in chunks] == [
        ("I. INTRODUCTION", "I. INTRODUCTION\nintro body"),
        ("II. METHOD", "II. METHOD\nmethod body"),
    ]
```

- [ ] **Step 2: Verify the chunk test fails**

Run: `venv\Scripts\python.exe -m pytest tests/test_paper_parser.py::test_chunk_pages_assigns_same_page_text_to_the_correct_section -v`

Expected: FAIL because section assignment currently uses page number only.

- [ ] **Step 3: Implement page segmentation**

Create ordered page segments by locating every recognized section title in page text, carrying the active section from the previous page, then chunk each segment independently. Preserve page number, source character offsets, global ordinal, and section title.

- [ ] **Step 4: Run parser and chunker tests**

Run: `venv\Scripts\python.exe -m pytest tests/test_paper_parser.py tests/test_paper_retriever.py -v`

Expected: PASS.

### Task 3: Persist resumable translation blocks

**Files:**
- Modify: `app/models/paper.py`
- Modify: `app/paper/service.py`
- Modify: `app/paper/router.py`
- Modify: `app/paper/schemas.py`
- Test: `tests/test_paper_tasks.py`

- [ ] **Step 1: Add failing translation pipeline tests**

Add a recording LLM and assert that translation loads every selected-section chunk in ordinal order, emits per-block progress, persists each completed block, and resumes after a simulated failure without retranslating completed blocks.

```python
assert [event["event"] for event in events].count("block") == 3
assert [event["block_index"] for event in events if event["event"] == "block"] == [0, 1, 2]
assert recording_llm.sources == ["first", "second", "third"]
```

- [ ] **Step 2: Verify translation tests fail**

Run: `venv\Scripts\python.exe -m pytest tests/test_paper_tasks.py -v`

Expected: FAIL because translation currently uses Top-K retrieval and has no block persistence.

- [ ] **Step 3: Add `PaperTranslationBlock`**

Create a new table with `paper_id`, `section`, `block_index`, `page_start`, `page_end`, `source_text`, `translated_text`, `status`, `error_message`, and timestamps. Add a unique constraint on `(paper_id, section, block_index)` and include it in paper deletion.

- [ ] **Step 4: Implement the dedicated translation branch**

Before generic retrieval in `run_task`, branch `task_type == "translation"` to `_run_translation`. Load all `PaperChunk` rows matching the exact section ordered by ordinal, translate each missing block independently with a plain-text translation prompt, persist immediately, and emit:

```python
{"event": "progress", "stage": "translation", "current": index, "total": total}
{"event": "block", "block_index": index, "page_start": chunk.page_start,
 "page_end": chunk.page_end, "content": translated_text}
{"event": "done", "task_id": task_id, "artifact_id": artifact_id,
 "content": merged_translation, "blocks": serialized_blocks}
```

On retry, skip completed blocks and emit their stored contents in order before continuing. On an empty response, mark the current block failed and emit `error` without deleting completed rows.

- [ ] **Step 5: Run translation tests**

Run: `venv\Scripts\python.exe -m pytest tests/test_paper_tasks.py -v`

Expected: PASS.

### Task 4: Expose section tree and block events to Vue

**Files:**
- Modify: `web/src/types/paper.ts`
- Modify: `web/src/api/paper.ts`
- Modify: `web/src/views/PaperWorkspaceView.vue`
- Modify: `web/src/components/paper/PaperTaskPanel.vue`
- Create: `web/src/components/paper/PaperSectionTree.vue`

- [ ] **Step 1: Add frontend types and SSE callback**

Add `level`-based tree types, `PaperTranslationBlock`, optional progress fields, and `onBlock`. Update `dispatchSseBlock` to route `event: block`.

- [ ] **Step 2: Build `PaperSectionTree.vue`**

Render sections as an Element Plus tree whose hierarchy is derived by a stack from the ordered `level` values. Each node displays title and page range and emits the exact section title on selection.

- [ ] **Step 3: Replace the translation select**

For translation only, render the tree on the left and a Chinese-only block list on the right. Maintain `translationBlocks`, `current`, and `total` in `PaperWorkspaceView`; append `onBlock` payloads and make each page button emit `openCitation`/`openPage`.

- [ ] **Step 4: Build frontend**

Run: `npm run build` in `web`.

Expected: `vue-tsc` and Vite complete successfully.

### Task 5: Regression verification and current-paper rebuild

**Files:**
- Modify only if tests expose a regression.

- [ ] **Step 1: Run all backend tests**

Run: `venv\Scripts\python.exe -m pytest -q`

Expected: all tests PASS.

- [ ] **Step 2: Reprocess the current PromptSR paper**

Invoke the service processing flow for paper `1` after removing only its derived pages, sections, chunks, report/task/artifact rows; keep the uploaded PDF and paper record. Confirm the detail API returns Introduction, Related Work, Method, Experiments, Conclusion, and References rather than a single Methods range.

- [ ] **Step 3: Restart and smoke-test**

Restart GraphRAG backend and frontend, verify `/health`, `/api/v1/papers/1`, select a chapter, observe ordered SSE block events, refresh the page, and confirm cached Chinese translation remains visible with working PDF page links.

## Repository note

`E:\codex\GraphRAG--main` currently has no `.git` directory, so commit steps cannot be executed. Do not initialize Git or create commits unless the user separately requests it.
