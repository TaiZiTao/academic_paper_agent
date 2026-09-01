# 论文智答 README 重写 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将仓库 README 重写为兼顾秋招项目展示与开源部署的准确、完整文档。

**Architecture:** README 以三大业务功能为入口，以三套 LangGraph Workflow、论文解析、Hybrid RAG、可信引用、会话记忆和 SSE 为技术主线。所有数字、路径、命令和能力声明必须从当前代码、配置或已完成评测中获得证据。

**Tech Stack:** Markdown、GitHub Mermaid、Python/FastAPI、Vue 3、LangGraph、FAISS、BM25、MinerU、SQLite、SSE

---

### Task 1: 核对 README 中使用的项目事实

**Files:**
- Read: `app/research/agent.py`
- Read: `app/paper/graph.py`
- Read: `app/paper/library_graph.py`
- Read: `app/paper/service.py`
- Read: `app/paper/retriever.py`
- Read: `app/models/paper.py`
- Read: `app/config/settings.py`
- Read: `app/paper/router.py`
- Read: `web/package.json`
- Read: `.env.example`

- [ ] **Step 1: 核对三套 Workflow 的节点名称与数量**

Run:

```powershell
Select-String -Path app/research/agent.py,app/paper/graph.py,app/paper/library_graph.py -Pattern 'add_node'
```

Expected: 文献检索 5 节点、单论文报告 6 节点、多论文问答 11 节点。

- [ ] **Step 2: 核对上传、解析、索引和会话的存储路径**

Run:

```powershell
Select-String -Path app/api/dependencies.py,app/paper/service.py,app/paper/retriever.py,app/models/paper.py -Pattern 'files_dir|root_dir|session_id|PaperChunk|PaperMessage'
```

Expected: PDF 位于 `data/papers/files`，索引位于 `data/papers/index/{paper_id}`，结构化数据与消息位于 SQLite。

- [ ] **Step 3: 核对启动命令、环境变量和 API**

Run:

```powershell
Get-Content .env.example
Get-Content web/package.json
Select-String -Path app/paper/router.py,app/research/router.py -Pattern '@router'
```

Expected: README 中只使用仓库存在的配置项、脚本和端点。

### Task 2: 重写 README 主体

**Files:**
- Modify: `README.md`
- Reference: `docs/superpowers/specs/2026-09-01-readme-rewrite-design.md`

- [ ] **Step 1: 重写首页与功能概览**

首页包含项目定位、技术栈、三大核心功能和快速导航；二次开发声明移到文档末尾。

- [ ] **Step 2: 增加架构与 Workflow 图**

使用 GitHub Mermaid 展示前端、FastAPI、三套 LangGraph Workflow、RAG、SQLite 和文件索引之间的数据流。分别列出 5、6、11 节点，不使用 Multi-Agent 表述。

- [ ] **Step 3: 补充核心实现说明**

独立说明多源文献检索、PDF 解析与分块翻译、FAISS + BM25、引用校验、会话记忆、SSE 以及数据存储结构。

- [ ] **Step 4: 加入真实评测结果**

写入 36 条检索题的 Hit@K、MRR、NDCG，以及单论文和多论文严格引用准确率；说明样本规模，不把工程测试数混作 Agent 评测数。

- [ ] **Step 5: 重写安装、启动、API 与故障排查**

提供 Windows PowerShell 为主的可复制命令，同时说明 Python、Node.js、MinerU、LLM 与 Embedding 配置要求。

### Task 3: 验证 README

**Files:**
- Verify: `README.md`

- [ ] **Step 1: 扫描过期和不准确表述**

Run:

```powershell
Select-String -Path README.md -Pattern 'Multi-Agent|三级记忆|588|GraphRAG 系统'
```

Expected: 不出现将当前论文模块描述为 Multi-Agent、三级记忆或 GraphRAG 的句子；允许在二次开发声明中提及上游项目名称。

- [ ] **Step 2: 验证相对链接与本地路径**

Run:

```powershell
Select-String -Path README.md -Pattern '\]\(([^)]+)\)'
```

Expected: 仓库内相对链接均指向存在的文件，不包含本机绝对路径。

- [ ] **Step 3: 验证前后端命令**

Run:

```powershell
E:\codex\GraphRAG--main\venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
Set-Location web
npm test
npm run build
```

Expected: 后端 598 项通过；前端 25 项通过；Vite 生产构建成功。

- [ ] **Step 4: 检查 Markdown 差异**

Run:

```powershell
git diff --check -- README.md
git diff --stat -- README.md
```

Expected: 无空白错误，修改范围仅为 README 内容重写。

### Task 4: 提交并推送

**Files:**
- Add: `docs/superpowers/plans/2026-09-01-readme-rewrite.md`
- Modify: `README.md`

- [ ] **Step 1: 暂存指定文档**

```powershell
git add README.md docs/superpowers/plans/2026-09-01-readme-rewrite.md
```

- [ ] **Step 2: 创建提交**

```powershell
git commit -m "docs: expand project README"
```

- [ ] **Step 3: 推送并核对远端**

```powershell
git push origin main
git ls-remote origin refs/heads/main
```

Expected: 远端 `main` 与本地 `HEAD` 哈希一致。
