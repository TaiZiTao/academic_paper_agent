# Paper Translation Figure/Table Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove figure/table captions and extracted visual-label noise from chapter translations while preserving prose and numbered equations.

**Architecture:** Add deterministic filters at the backend translation-input boundary and the frontend historical-display boundary. Both filters anchor removal to explicit figure/table captions and conservatively retain prose when confidence is low; the LLM prompt adds a final guardrail.

**Tech Stack:** Python, FastAPI service layer, pytest, TypeScript, Vue 3, Vitest, KaTeX

---

### Task 1: Backend visual-region filter

**Files:**
- Create: `app/paper/content_filter.py`
- Create: `tests/test_paper_content_filter.py`

- [ ] **Step 1: Write failing tests**

Add tests proving that a PromptSR-style block ending in `Fig. 2:` is removed, a `Table 1:` block is removed, numbered equations remain, and text without captions is unchanged.

- [ ] **Step 2: Run tests and verify RED**

Run: `D:\anaconda3\envs\pytorch\python.exe -m pytest tests/test_paper_content_filter.py -q`

Expected: import failure because `strip_visual_regions` does not exist.

- [ ] **Step 3: Implement the filter**

Create `strip_visual_regions(text: str) -> str`. Detect `Fig.`, `Figure`, `Table`, `图 N`, and `表 N` captions; remove nearby runs of short labels and caption continuation lines; stop at a prose paragraph, section heading, or numbered equation. If no caption is present, return the input unchanged.

- [ ] **Step 4: Run tests and verify GREEN**

Run the same pytest command and expect all tests to pass.

### Task 2: Apply backend filter before LLM translation

**Files:**
- Modify: `app/paper/service.py`
- Modify: `app/paper/prompts.py`
- Modify: `tests/test_paper_prompts.py`
- Modify: `tests/test_paper_service.py`

- [ ] **Step 1: Write failing integration tests**

Assert that translation source passed to the prompt excludes figure/table regions and that the prompt explicitly instructs the model to skip visual content while preserving prose and equations.

- [ ] **Step 2: Run targeted tests and verify RED**

Run: `D:\anaconda3\envs\pytorch\python.exe -m pytest tests/test_paper_prompts.py tests/test_paper_service.py -q`

- [ ] **Step 3: Integrate minimal production changes**

Call `strip_visual_regions` after page-prefix cleanup and before translation-unit merging. Extend `build_translation_prompt` with a concise visual-content exclusion rule.

- [ ] **Step 4: Run targeted tests and verify GREEN**

Run the same pytest command and expect all tests to pass.

### Task 3: Clean historical translations at display time

**Files:**
- Create: `web/src/utils/paperVisualContent.ts`
- Create: `web/src/utils/paperVisualContent.test.ts`
- Modify: `web/src/components/paper/PaperTaskPanel.vue`

- [ ] **Step 1: Write failing Vitest cases**

Use the existing translated PromptSR figure-label sample. Assert that visual labels and `图2` caption disappear while surrounding Chinese prose and numbered equations remain.

- [ ] **Step 2: Run tests and verify RED**

Run: `pnpm test`

Expected: import failure because `stripVisualRegions` does not exist.

- [ ] **Step 3: Implement and integrate**

Create `stripVisualRegions(content: string): string` with the same caption-anchored conservative rules. Pass cleaned block content to `ScientificText`; do not mutate API data or SQLite records.

- [ ] **Step 4: Run tests and verify GREEN**

Run `pnpm test` and expect all utility tests to pass.

### Task 4: End-to-end verification

**Files:**
- Verify only; no new production files.

- [ ] **Step 1: Run backend regression tests**

Run targeted paper parser, prompt, service, and content-filter tests.

- [ ] **Step 2: Run frontend tests and production build**

Run `pnpm test` and `pnpm run build` in `web`.

- [ ] **Step 3: Verify the live page**

Open paper 1, choose chapter translation and `B. Global Anchor Prompting Layer`. Confirm the figure-label block and caption are absent, body prose and equations `(6)`–`(12)` remain, and there are no browser console errors.

> Note: this directory is not a Git repository, so commit steps are intentionally omitted.
