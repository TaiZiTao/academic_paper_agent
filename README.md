# 📚 论文智答 · 论文知识问答平台

<p align="center">
  <h1 align="center">📚 论文智答</h1>
  <p align="center">
    <strong>PaperQA — 面向论文精读的知识问答平台（二次开发项目）</strong>
  </p>
  <p align="center">
    <img src="https://img.shields.io/badge/Python-3.10-blue.svg" alt="Python 3.10">
    <img src="https://img.shields.io/badge/FastAPI-0.141+-009688.svg" alt="FastAPI">
    <img src="https://img.shields.io/badge/LangGraph-0.6+-green.svg" alt="LangGraph">
    <img src="https://img.shields.io/badge/MinerU-3.4-8A2BE2.svg" alt="MinerU">
    <img src="https://img.shields.io/badge/Vue-3.x-4FC08D.svg" alt="Vue 3">
    <img src="https://img.shields.io/badge/Tests-588%20passed-brightgreen.svg" alt="Tests">
    <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License">
  </p>
</p>

> **⚠️ 二次开发声明**：本项目基于 MIT 开源项目「GraphRAG 企业知识智能问答平台」二次开发（fork + 重构）。
> 原项目是面向企业的**通用知识库问答系统**；本仓库将其重新定位为**论文知识问答平台**，并对核心解析链路进行了彻底重构
> （MinerU-first 版面解析 + LLM 审计兜底）。当前系统为标准 **RAG**（FAISS + BM25 混合检索），**并未实现知识图谱**，
> 故更名为「论文智答」，避免「GraphRAG」命名造成误导。上游版权见 [LICENSE](LICENSE)（Copyright (c) 2026 Kbabyshark）。

---

## 📖 目录

- [项目简介](#-项目简介)
- [二次开发内容](#-二次开发内容)
- [核心特性](#-核心特性)
- [系统架构](#-系统架构)
- [论文解析管线（MinerU-first）](#-论文解析管线mineru-first)
- [快速开始](#-快速开始)
- [环境变量](#-环境变量)
- [API 端点](#-api-端点)
- [项目结构](#-项目结构)
- [运行测试](#-运行测试)
- [贡献指南](#-贡献指南)
- [License](#-license)

---

## 📖 项目简介

**论文智答**是一个面向科研论文的**精读 + 问答**平台：上传一篇 PDF 论文，系统通过 **MinerU 版面解析**自动还原
章节结构、提取图表与表格、生成精读报告，并建立该论文**独立的 FAISS + BM25 混合索引**，支持基于原文片段的问答、
章节翻译、研究笔记与汇报提纲，引用均带**页码溯源**，可一键跳转到内置 PDF 阅读器对应页面。

平台同时保留了上游项目的**通用知识库问答**能力（LangGraph 8 节点工作流）与**文献检索下载** Agent，
形成「论文库 → 精读 → 问答 → 检索下载」的完整科研工作流。

- 后端：FastAPI + LangGraph + MinerU + FAISS/BM25（Python 3.10）
- 前端：Vue 3 + Vite + Element Plus + Pinia（TypeScript）
- 模型：阿里云百炼 DashScope（qwen-plus 对话 / text-embedding-v4 向量），OpenAI 兼容接口

---

## 🔧 二次开发内容

| 模块 | 上游基础 | 本次二次开发（本仓库） |
|------|----------|------------------------|
| 产品定位 | 企业通用知识库问答 | 重新定位为**论文知识问答平台**，应用更名「论文智答」 |
| 文档解析 | MinerU + pdfplumber 通用解析 | **MinerU-first 重构**：版面检测结果作为权威输出，规则仅做异常检测 |
| 章节结构 | 无 | **章节树**：MinerU 版面标题（level）→ 章节层级，正文句过滤防伪标题 |
| 图表处理 | 无 | 图表区域**裁剪渲染**、多面板图**连通域合并**、图内文字从正文**剔除**、全宽表格支持 |
| 论文精读 | 无 | 单篇论文独立索引；精读报告 + 问答/翻译/汇报提纲/评审四种按需任务 |
| 审计兜底 | 无 | **LLM 审计 Agent**（section_audit / figure_audit）：图注粘连修复、伪标题剔除、重复去重 |
| 通用问答 | 8 节点 LangGraph 工作流 | 保留，适配论文语料 |
| 文献检索 | 无 | 多数据库检索 + 批量导入 + VPN 门户浏览器辅助（app/research） |

---

## ✨ 核心特性

### 📄 论文精读（核心）
- **MinerU 版面解析**：进程内 `do_parse`（DocLayout-YOLO 布局检测），自动识别标题层级、图表块、表格与公式
- **章节树**：按版面标题还原论文层级结构（如 1 → 1.1 → 1.1.1），支持展开/折叠阅读
- **精读报告**：自动生成元数据、章节结构、贡献点与结构化报告
- **四种按需任务**：论文问答 / 章节翻译 / 汇报提纲 / 评审

### 🖼 图表级处理
- 图表按**版面坐标裁剪渲染**，缩略图 + 大图查看
- **多面板图合并**：同编号、相邻位置的分块图自动聚合成一张完整图
- **图内文字剔除**：图表内部文字不会混入正文与检索分块
- 图注/表注提取与中文翻译，粘连图注由 LLM 审计修复

### 🔍 混合检索（RAG）
- **FAISS 向量检索** + **BM25 关键词检索**，Min-Max 归一化 + 权重融合（默认 0.7 : 0.3）
- 每篇论文**独立索引**，问答/翻译/笔记互不串库；另有全库聚合问答
- 引用必须通过**原文片段 + 页码校验**，点击引用直接跳转 PDF 对应页

### 🧠 通用知识库问答（上游保留）
- 8 节点 LangGraph Agent Workflow：QueryRewrite → IntentRoute → KBSelect → HybridRetrieve →
  RelevanceEval（不足时重写重试）→ AnswerGenerate → CitationFormat → ErrorHandler
- SSE 实时推送节点状态与 Token 流，三级会话记忆

### 🔎 文献检索下载
- 多数据库（arXiv / Semantic Scholar / OpenAlex / Unpaywall 等）检索与全文下载
- 批量导入任务、进度查询、失败重试；VPN 门户浏览器登录辅助

### 📡 SSE 流式与任务管理
- 解析与分析进度通过 SSE 实时更新，失败任务可重试，论文档案可完整删除

---

## 🏗 系统架构

```
┌────────────────────────────────────────────────────────────┐
│                  Vue 3 前端（论文智答）                        │
│  论文库 · 论文精读 · 章节/图表阅读 · 知识库问答 · 文献检索        │
└─────────────────────────┬──────────────────────────────────┘
                          │ HTTP REST + SSE Stream
┌─────────────────────────▼──────────────────────────────────┐
│                       FastAPI 后端                           │
│  ┌────────────────────┐  ┌────────────────────┐             │
│  │   论文精读管道       │  │  通用问答管道        │             │
│  │  MinerU-first 解析  │  │  LangGraph 8 节点  │             │
│  │  章节/图表/正文抽取   │  │  (上游保留)         │             │
│  └─────────┬──────────┘  └─────────┬──────────┘             │
│            │                       │                        │
│  ┌─────────▼───────────────────────▼──────────┐             │
│  │              RAG 引擎                       │             │
│  │   FAISS 向量(0.7) + BM25 关键词(0.3) 融合    │             │
│  │   单篇独立索引 / 全库聚合索引                 │             │
│  └─────────┬───────────────────────┬──────────┘             │
│            │                       │                        │
│  ┌─────────▼──────┐    ┌───────────▼─────────┐              │
│  │   SQLite 存储    │    │  MinerU 版面模型     │              │
│  │  索引/分块/任务   │    │  缓存 (ModelSingleton)│             │
│  └────────────────┘    └─────────────────────┘              │
└─────────────────────────────────────────────────────────────┘
```

---

## 🧠 论文解析管线（MinerU-first）

本仓库最重要的重构：**以 MinerU 版面检测结果为准，规则只做异常检测，LLM 只做兜底审计**，
不依赖文本启发式规则去「猜」论文结构，从而适配不同排版风格的论文。

```
PDF 上传
  └─ ① MinerU 版面解析（进程内 do_parse，DocLayout-YOLO）
        ├─ 版面标题（level 0~N）  → 章节树骨架
        ├─ 图像/表格块（bbox+图注）→ 图表区域
        └─ 正文/公式块            → 正文抽取（剔除图表区域内文字）
  └─ ② 规则异常检测（只探测，不臆造）
        ├─ 疑似正文句的「伪标题」过滤
        ├─ 同编号多面板图合并（连通域聚类）
        └─ 空图注 / 粘连图注标记
  └─ ③ LLM 审计 Agent（兜底，保守不改编号/顺序）
        ├─ section_audit：剔除伪标题、校正错位章节
        └─ figure_audit：图注粘连修复、同编号去重
  └─ ④ 产出
        ├─ 章节树（含页码）→ 章节翻译 / 定位跳转
        ├─ 图表（裁剪渲染 + 图注）→ 图表阅读
        └─ 正文分块 → 独立 FAISS/BM25 索引 → 问答/翻译/汇报
```

> 设计原则：MinerU 输出是「事实」，规则只回答「哪里可能有问题」，LLM 负责「怎么修」。
> 避免堆叠大量文本启发式规则——那是无法适配所有论文的。

---

## 🚀 快速开始

### 前置要求

- **Python** 3.10（本项目使用 conda 环境 `pytorch` 基础上创建的 venv，含 torch 2.2.2+cu118）
- **Node.js** >= 18、**pnpm**（前端包管理器）
- 阿里云百炼 API Key（DashScope，OpenAI 兼容接口）

> 注：项目依赖随本地 `venv/` 管理，**未维护 requirements.txt**（上游沿用下来的习惯）。
> 核心依赖参考：`fastapi` `uvicorn` `pydantic-settings` `sqlalchemy>=2.0` `aiosqlite`
> `langgraph>=0.6` `langchain-core` `langchain-openai` `faiss-cpu` `rank-bm25`
> `pdfplumber` `beautifulsoup4` `loguru` `httpx` `mineru>=3.4`（MinerU 依赖 torch，GPU 可选，RTX 4060 实测可用）。

### 1. 克隆项目

```bash
git clone https://github.com/TaiZiTao/academic_paper_agent.git
cd academic_paper_agent
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY 与 EMBEDDING_API_KEY（DashScope 密钥）
```

### 3. 启动后端

```bash
# 方式一：使用 run.py（会在 import 前设置 OpenBLAS 线程环境变量，推荐）
venv/Scripts/python run.py

# 方式二：直接 uvicorn
venv/Scripts/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

后端运行在 **http://127.0.0.1:8000**，Swagger 文档：**http://127.0.0.1:8000/docs**

> 首次解析论文时，MinerU 会自动下载版面检测模型（缓存于本地模型目录）。

### 4. 启动前端

```bash
cd web
pnpm install
pnpm run dev
```

前端运行在 **http://localhost:5173**（Vite 默认绑定 IPv6 回环地址 `::1`）

### 5. 生产构建

```bash
cd web
pnpm run build    # 产物输出到 web/dist/
```

---

## ⚙️ 环境变量

完整配置项参见 [`.env.example`](.env.example)。

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `APP_NAME` | 应用名称 | `论文智答` |
| `APP_VERSION` | 应用版本 | `0.1.0` |
| `DEBUG` | 调试模式 | `true` |
| `HOST` / `PORT` | 后端监听地址/端口 | `127.0.0.1` / `8000` |
| `DATABASE_URL` | 数据库连接 | `sqlite+aiosqlite:///data/graphrag.db` |
| `LLM_PROVIDER` | 大模型提供商 | `dashscope` |
| `LLM_MODEL` | 对话模型 | `qwen-plus` |
| `LLM_API_KEY` | 大模型 API 密钥 | **必填** |
| `LLM_BASE_URL` | 大模型 API 地址 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `EMBEDDING_MODEL` | 向量模型 | `text-embedding-v4`（1024 维） |
| `EMBEDDING_API_KEY` | 向量模型 API 密钥 | **必填** |
| `EMBEDDING_BASE_URL` | 向量模型 API 地址 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `DATA_DIR` / `LOG_DIR` | 数据/日志目录 | `data` / `logs` |
| `PARSER_CHUNK_SIZE` / `_OVERLAP` | 通用文档分块 | `500` / `50` |
| `PAPER_CHUNK_SIZE` / `_OVERLAP` | 单篇论文分块 | `1200` / `150` |
| `RETRIEVAL_TOP_K` | 检索返回条数 | `5` |
| `RETRIEVAL_VECTOR_WEIGHT` | 向量检索权重 | `0.7` |
| `RETRIEVAL_KEYWORD_WEIGHT` | 关键词检索权重 | `0.3` |
| `SHORT_MEMORY_SIZE` | 短期记忆窗口 | `10` |
| `RESEARCH_TOP_K` | 文献检索条数 | `20` |
| `RESEARCH_SEARCH_TIMEOUT` | 检索超时（秒） | `15` |
| `RESEARCH_DOWNLOAD_DELAY` | 下载间隔（秒） | `4` |
| `RESEARCH_PROXY` | 检索代理 | 空 |
| `VPN_PORTAL_URL` | VPN 门户地址 | `https://vpn.swjtu.edu.cn` |
| `UNPAYWALL_EMAIL` | Unpaywall 邮箱 | 空 |
| `LOG_LEVEL` | 日志级别 | `DEBUG` |

---

## 📡 API 端点

所有业务端点前缀 `/api/v1`。

### 论文精读

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/papers` | 上传 PDF 并启动精读（202） |
| `GET` | `/papers` | 论文档案列表 |
| `GET` | `/papers/{id}` | 论文、章节、图表、报告与历史任务详情 |
| `GET` | `/papers/{id}/events` | 解析与分析进度（SSE） |
| `POST` | `/papers/{id}/retry` | 重试失败的精读任务 |
| `POST` | `/papers/{id}/tasks/stream` | 问答/翻译/汇报/评审任务（SSE） |
| `GET` | `/papers/{id}/pdf` | 内联查看原始 PDF |
| `GET` | `/papers/{id}/figures/{figure_id}/image` | 图表裁剪渲染图片 |
| `PUT` | `/papers/{id}/field` | 更新论文字段 |
| `DELETE` | `/papers/{id}` | 删除论文及其索引、产物和会话 |
| `POST` | `/papers/qa/stream` | 全库聚合问答（SSE） |
| `GET` | `/papers/qa/history` | 全库问答历史 |

### 文献检索

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/research/search` | 多数据库检索 |
| `POST` | `/research/imports` | 批量导入（202） |
| `GET` | `/research/imports` | 导入任务列表 |
| `GET` | `/research/imports/{import_id}` | 导入任务详情 |
| `POST` | `/research/imports/{import_id}/retry` | 重试导入 |
| `GET` | `/research/browser/status` | VPN 浏览器状态 |
| `POST` | `/research/browser/login` / `/verify` / `/close` | VPN 门户登录辅助 |

### 通用问答与知识库（上游保留）

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/chat` | 知识问答（同步 JSON） |
| `POST` | `/chat/stream` | 知识问答（SSE 流式） |
| `GET` / `POST` | `/kb` | 知识库列表 / 创建 |
| `PUT` / `DELETE` | `/kb/{kb_id}` | 编辑 / 删除知识库 |
| `GET` / `DELETE` | `/documents` / `/documents/{doc_id}` | 文档列表 / 删除 |
| `GET` / `DELETE` | `/conversations` / `/conversations/{session_id}` | 会话列表 / 删除 |
| `GET` | `/conversations/{session_id}/messages` | 会话历史 |
| `GET` | `/settings` | 系统配置 |
| `GET` | `/stats` | 首页统计 |

### 系统

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查（返回 app_name 等） |

---

## 📂 项目结构

```
academic_paper_agent/
├── app/                          # 后端应用
│   ├── paper/                    # ★ 论文精读核心（二次开发重点）
│   │   ├── parser.py             #   MinerU-first 论文解析（版面标题 → 章节树）
│   │   ├── figures.py            #   MinerU 图表转换/合并/裁剪渲染/正文剔除
│   │   ├── section_audit.py      #   章节 LLM 审计兜底（伪标题剔除）
│   │   ├── figure_audit.py       #   图表 LLM 审计兜底（图注粘连修复/去重）
│   │   ├── service.py            #   精读任务编排（解析→审计→索引→任务）
│   │   ├── retriever.py          #   单篇论文混合检索
│   │   ├── aggregate_retriever.py#   全库聚合问答检索
│   │   ├── graph.py / library_graph.py / library_nodes.py  # 问答 LangGraph
│   │   ├── router.py / schemas.py / prompts.py / state.py
│   │   ├── chunker.py / citations.py / content_filter.py
│   ├── research/                 # 文献检索下载 Agent（搜索/导入/VPN 浏览器）
│   ├── graph/                    # 上游通用问答 8 节点 LangGraph 工作流
│   ├── rag/                      # FAISS 向量 + BM25 关键词检索引擎
│   ├── parser/                   # 通用文档解析（上游保留：loader/cleaner/chunker）
│   ├── memory/                   # 三级会话记忆
│   ├── models/                   # SQLAlchemy ORM（含 paper 系列模型）
│   ├── services/                 # 知识库/文档/问答服务
│   ├── api/                      # 通用问答 API 路由
│   ├── config/                   # pydantic-settings 配置
│   └── database/ utils/
├── web/                          # Vue 3 前端（Vite + Element Plus + Pinia）
│   └── src/
│       ├── api/                  #   Axios 接口封装
│       ├── views/                #   页面（论文库/精读/问答/图表/设置…）
│       ├── components/           #   组件（PDF 阅读器、章节树、图表画廊…）
│       ├── composables/          #   useChat / useSSE / …
│       ├── stores/ router/ types/ styles/ layouts/
├── scripts/                      # 运维脚本（chunk 重建、MinerU 图表回填、审计重跑）
├── tests/                        # pytest（588 个用例）
├── docs/superpowers/             # 内部设计与开发计划文档
├── .env.example                  # 环境变量模板
├── run.py                        # 后端启动入口（先设 OpenBLAS 环境变量）
├── main.py                       # FastAPI 应用入口
└── LICENSE                       # MIT（继承上游版权）
```

---

## 🧪 运行测试

```bash
venv/Scripts/python -m pytest tests/ -v
```

当前 **588 个用例全部通过**，覆盖：论文解析/章节（MinerU sections）、图表（裁剪/全宽/区域间距）、
图注审计、章节审计、问答服务、聚合检索、引用校验、通用 RAG、记忆、API 与端到端流程。

---

## 🤝 贡献指南

本项目是二次开发项目，欢迎 Issue 和 PR。

1. Fork 本仓库，创建特性分支：`git checkout -b feature/xxx`
2. 修改后运行测试：`venv/Scripts/python -m pytest tests/ -v`
3. 提交并推送：`git push origin feature/xxx`，创建 Pull Request

**代码规范**：

- 后端：Python 类型注解优先，Loguru 统一日志，单一职责
- 前端：Composition API + `<script setup lang="ts">`，Scoped CSS
- 解析链路：遵循 **MinerU-first** 原则——优先信任版面检测结果，规则只做异常检测，LLM 只做兜底审计

---

## 📄 License

本项目基于 **MIT License** 开源（继承上游版权），详见 [LICENSE](LICENSE)。

---

<p align="center">
  <sub>Built on top of ❤️ GraphRAG 企业知识智能问答平台 (FastAPI + LangGraph + Vue 3)，二次开发为论文知识问答平台</sub>
</p>
