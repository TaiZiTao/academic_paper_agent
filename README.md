<p align="center">
  <h1 align="center">🔍 GraphRAG 企业知识智能问答平台</h1>
  <p align="center">
    <strong>Enterprise Knowledge Q&A Platform — GraphRAG + Agent Workflow</strong>
  </p>
  <p align="center">
    <img src="https://img.shields.io/badge/Python-3.11+-blue.svg" alt="Python 3.11+">
    <img src="https://img.shields.io/badge/FastAPI-0.115+-009688.svg" alt="FastAPI">
    <img src="https://img.shields.io/badge/LangGraph-0.6+-green.svg" alt="LangGraph">
    <img src="https://img.shields.io/badge/Vue-3.x-4FC08D.svg" alt="Vue 3">
    <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License">
    <img src="https://img.shields.io/badge/Tests-139%20passed-brightgreen.svg" alt="Tests">
  </p>
</p>

---

## 📖 目录

- [项目简介](#-项目简介)
- [技术栈](#-技术栈)
- [核心特性](#-核心特性)
- [系统架构](#-系统架构)
- [快速开始](#-快速开始)
- [环境变量](#-环境变量)
- [API 端点](#-api-端点)
- [项目结构](#-项目结构)
- [LangGraph 工作流](#-langgraph-工作流)
- [RAG 检索架构](#-rag-检索架构)
- [运行测试](#-运行测试)
- [Demo 流程](#-demo-流程)
- [路线图](#-路线图)
- [贡献指南](#-贡献指南)
- [License](#-license)

---

## 📖 项目简介

**GraphRAG 企业知识智能问答平台** 是一个面向企业的端到端知识库智能问答系统。它基于 **GraphRAG（图谱增强检索生成）** 理念，采用 **8 节点 LangGraph Agent Workflow** 编排，融合 **FAISS 向量检索** 与 **BM25 关键词检索** 实现混合检索，支持多知识库管理、文档解析与引用溯源、SSE 流式问答、三级会话记忆等生产级能力。

前端采用 **Vue 3 + Element Plus** 构建企业后台管理界面，后端基于 **FastAPI** 提供 RESTful API 与 SSE 流式端点，整体架构遵循高内聚、低耦合的模块化设计原则。

---

## 🛠 技术栈

### 后端

| 类别 | 技术 |
|------|------|
| Web 框架 | FastAPI + Uvicorn |
| AI Workflow | LangGraph + LangChain |
| 向量检索 | FAISS |
| 关键词检索 | BM25 (rank-bm25) |
| 文档解析 | MinerU + pdfplumber + BeautifulSoup4 |
| 数据验证 | Pydantic v2 + Pydantic Settings |
| 数据库 | SQLite + SQLAlchemy (async) + aiosqlite |
| 状态持久化 | LangGraph SqliteSaver |
| 日志 | Loguru |
| 测试 | Pytest (139 条用例) |

### 前端

| 类别 | 技术 |
|------|------|
| 框架 | Vue 3 (Composition API) |
| 构建工具 | Vite |
| 语言 | TypeScript |
| UI 组件库 | Element Plus |
| 状态管理 | Pinia |
| 路由 | Vue Router |
| HTTP 客户端 | Axios |
| 流式通信 | SSE (Server-Sent Events) |

---

## ✨ 核心特性

### 🔬 文档解析
- **MinerU** 解析 PDF（OCR + 版面分析），自动识别标题、正文、表格
- 支持 **HTML / TXT / Markdown** 多格式上传
- **页码映射**：每个 Chunk 携带源文档页码，支持原文跳转
- 表格自动转 **Markdown Table**，保留结构化信息

### 🔍 混合检索
- **FAISS** 向量检索：语义相似度匹配
- **BM25** 关键词检索：精确关键词命中
- **Min-Max 归一化** + **可调权重融合**（默认向量:关键词 = 0.7:0.3）
- 支持 Top-K 自由调节

### 🧠 8 节点 Agent Workflow

```
QueryRewrite → IntentRoute → KBSelect → HybridRetrieve
                                              ↓
                                         RelevanceEval
                                        ↙              ↘
                               (不足) RetryRewrite    (达标) AnswerGenerate
                                                              ↓
                                                        CitationFormat
                                                              ↓
                                                        ErrorHandler
```

- **条件重试**：相关性不足时自动重写查询 → 重新检索（最多 2 次）
- **全部节点状态**通过 SSE 实时推送到前端可视化

### 🗂 自动知识库选择
- **Embedding 语义匹配**：问题与知识库描述进行语义比对
- **LLM 混合路由**：复杂场景下由 LLM 决策分库/联合检索
- 支持用户手动指定知识库

### 🧩 三级会话记忆
| 层级 | 实现 | 说明 |
|------|------|------|
| 工作记忆 | LangGraph State | 单次问答上下文 |
| 短期记忆 | 滑动窗口（默认 10 轮） | 多轮对话历史 |
| 长期记忆 | SQLite 持久化 + 摘要压缩 | 跨会话知识沉淀 |

### 📡 SSE 流式输出
- Token 级实时推送
- 8 节点执行状态可视化（排队 / 执行中 / 已完成 / 失败）
- 支持中断重试

### 📎 引用溯源
- 答案中的每条事实关联源文档 + 页码
- 基于文档名的引用去重
- 引用数据持久化存储，页面刷新不丢失

### 📖 单篇论文精读
- 上传一篇中文或英文文本型 PDF，自动生成元数据、章节结构、贡献点和结构化精读报告
- 每篇论文使用独立的 FAISS + BM25 索引，问答、翻译和笔记不会串到其他论文
- 支持论文问答、章节翻译、研究笔记和汇报提纲五种工作模式
- 引用必须通过原文片段与页码校验；点击引用可直接跳转到内置 PDF 阅读器对应页面
- 解析与分析进度通过 SSE 实时更新，失败任务可重试，论文档案可完整删除

使用方式：启动前后端后，在左侧进入「论文助手」，上传文本型 PDF，等待状态变为「精读完成」，然后打开论文档案。扫描版 PDF 会被明确提示暂不支持。

---

## 🏗 系统架构

```
┌──────────────────────────────────────────────────────────────┐
│                     Vue 3 前端                                │
│     Element Plus + Pinia + Axios + SSE Stream                │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────┐   │
│  │ 知识库管理│  │ 文档管理  │  │ 智能问答  │  │ 会话管理   │   │
│  └──────────┘  └──────────┘  └──────────┘  └────────────┘   │
└────────────────────────┬─────────────────────────────────────┘
                         │ HTTP REST + SSE Stream
┌────────────────────────▼─────────────────────────────────────┐
│                      FastAPI 后端                             │
│                                                              │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐ │
│  │   API Router   │  │   Services     │  │   LangGraph     │ │
│  │  · /chat       │  │  · QA Service  │  │  8-Node Agent   │ │
│  │  · /kb         │  │  · Doc Service │  │  Workflow       │ │
│  │  · /documents  │  │  · KB Service  │  │                 │ │
│  └───────┬────────┘  └───────┬────────┘  └───────┬─────────┘ │
│          │                   │                    │           │
│  ┌───────▼───────────────────▼────────────────────▼─────────┐ │
│  │                       RAG Engine                         │ │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐  │ │
│  │  │Embedding │  │  FAISS   │  │   BM25   │  │ Fusion  │  │ │
│  │  │  Model   │  │  Vector  │  │ Keyword  │  │  Score   │  │ │
│  │  └──────────┘  └──────────┘  └──────────┘  └─────────┘  │ │
│  └──────────────────────────────────────────────────────────┘ │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐ │
│  │  Parser  │  │  Memory  │  │ SQLite   │  │   Config     │ │
│  │ (MinerU) │  │(3-Level) │  │+Saver    │  │  (pydantic)  │ │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

---

## 🚀 快速开始

### 前置要求

- **Python** `>= 3.11, < 3.13`
- **Node.js** `>= 18`
- **pnpm**（前端包管理器）

### 1. 克隆项目

```bash
git clone https://github.com/your-username/graphrag-enterprise-qa.git
cd graphrag-enterprise-qa
```

### 2. 启动后端

```bash
# 安装 Python 依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY 和 EMBEDDING_API_KEY

# 启动服务
python run.py
```

后端运行在 **http://127.0.0.1:8000**  
Swagger 文档：**http://127.0.0.1:8000/docs**

### 3. 启动前端

```bash
cd web
pnpm install
pnpm run dev
```

前端运行在 **http://localhost:5173**

### 4. 生产构建

```bash
cd web
pnpm run build    # 产物输出到 web/dist/
```

---

## ⚙️ 环境变量

完整配置项参见 [`.env.example`](.env.example)。

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LLM_API_KEY` | 大模型 API 密钥 | **必填** |
| `LLM_MODEL` | 大模型名称 | `gpt-4o` |
| `LLM_BASE_URL` | 大模型 API 地址 | `https://api.openai.com/v1` |
| `LLM_PROVIDER` | 大模型提供商 | `openai` |
| `EMBEDDING_API_KEY` | 向量模型 API 密钥 | **必填** |
| `EMBEDDING_MODEL` | 向量模型名称 | `text-embedding-3-small` |
| `EMBEDDING_BASE_URL` | 向量模型 API 地址 | `https://api.openai.com/v1` |
| `PARSER_CHUNK_SIZE` | 文档分块大小（字符） | `1500` |
| `PARSER_CHUNK_OVERLAP` | 分块重叠字符数 | `300` |
| `PAPER_CHUNK_SIZE` | 单篇论文分块大小（字符） | `1200` |
| `PAPER_CHUNK_OVERLAP` | 单篇论文分块重叠字符数 | `150` |
| `RETRIEVAL_TOP_K` | 检索返回条数 | `5` |
| `RETRIEVAL_VECTOR_WEIGHT` | 向量检索权重 | `0.7` |
| `RETRIEVAL_KEYWORD_WEIGHT` | 关键词检索权重 | `0.3` |
| `SHORT_MEMORY_SIZE` | 短期记忆窗口大小 | `10` |
| `DEBUG` | 调试模式 | `true` |
| `DATABASE_URL` | 数据库连接 | `sqlite+aiosqlite:///data/graphrag.db` |

---

## 📡 API 端点

### 问答

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/chat` | 知识问答（同步 JSON） |
| `POST` | `/api/v1/chat/stream` | 知识问答（SSE 流式） |

### 知识库

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/kb` | 知识库列表 |
| `POST` | `/api/v1/kb` | 创建知识库 |
| `PUT` | `/api/v1/kb/{id}` | 编辑知识库 |
| `DELETE` | `/api/v1/kb/{id}` | 删除知识库 |

### 文档

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/documents/upload` | 上传文档（multipart） |
| `GET` | `/api/v1/documents` | 文档列表 |
| `DELETE` | `/api/v1/documents/{id}` | 删除文档 |

### 论文精读

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/papers` | 上传 PDF 并启动精读 |
| `GET` | `/api/v1/papers` | 论文档案列表 |
| `GET` | `/api/v1/papers/{id}` | 论文、章节、报告和历史任务详情 |
| `GET` | `/api/v1/papers/{id}/events` | 解析与分析进度（SSE） |
| `GET` | `/api/v1/papers/{id}/pdf` | 内联查看原始 PDF |
| `POST` | `/api/v1/papers/{id}/retry` | 重试失败的精读任务 |
| `POST` | `/api/v1/papers/{id}/tasks/stream` | 问答、翻译、笔记、汇报提纲（SSE） |
| `DELETE` | `/api/v1/papers/{id}` | 删除论文及其索引、产物和会话 |

### 会话

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/conversations` | 会话列表 |
| `GET` | `/api/v1/conversations/{id}/messages` | 会话消息历史 |
| `DELETE` | `/api/v1/conversations/{id}` | 删除会话 |

### 系统

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查 |
| `GET` | `/api/v1/stats` | 首页统计 |
| `GET` | `/api/v1/settings` | 系统配置 |

---

## 📂 项目结构

```
graphrag-enterprise-qa/
├── app/                         # 后端应用
│   ├── api/                     #   API 路由 + Pydantic Schema
│   │   ├── router.py            #     所有路由注册
│   │   ├── schemas.py           #     请求/响应模型
│   │   └── dependencies.py      #     依赖注入
│   ├── services/                #   业务服务层
│   │   ├── qa_service.py        #     问答流程编排
│   │   ├── document_service.py  #     文档管理
│   │   └── kb_service.py        #     知识库管理
│   ├── graph/                   #   LangGraph 工作流
│   │   ├── state.py             #     Workflow State 定义
│   │   ├── nodes.py             #     8 个 Node 实现
│   │   └── workflow.py          #     Graph 编排 + Edge
│   ├── rag/                     #   检索引擎
│   │   ├── embedding.py         #     Embedding 模型封装
│   │   ├── vector_store.py      #     FAISS 向量存储
│   │   ├── keyword_store.py     #     BM25 关键词索引
│   │   ├── retriever.py         #     混合检索器
│   │   └── fusion.py            #     Min-Max 分数融合
│   ├── parser/                  #   文档解析
│   │   ├── loader.py            #     多格式加载器
│   │   ├── models.py            #     解析结果模型
│   │   ├── cleaner.py           #     文档清洗
│   │   └── chunker.py           #     智能分块
│   ├── memory/                  #   三级记忆
│   │   ├── manager.py           #     记忆管理器
│   │   ├── short_term.py        #     滑动窗口短期记忆
│   │   ├── long_term.py         #     SQLite 长期记忆
│   │   └── models.py            #     记忆数据模型
│   ├── models/                  #   ORM 模型
│   │   ├── knowledge_base.py    #     知识库
│   │   ├── document.py          #     文档
│   │   └── conversation.py      #     会话
│   ├── database/                #   数据库
│   │   ├── database.py          #     引擎 + Session
│   │   └── base.py              #     Declarative Base
│   ├── config/                  #   配置管理
│   │   └── settings.py          #     pydantic-settings
│   └── utils/                   #   工具
│       ├── logger.py            #     Loguru 配置
│       └── exceptions.py        #     自定义异常
├── web/                         # 前端应用
│   └── src/
│       ├── api/                 #   Axios 接口封装
│       ├── composables/         #   组合式函数（useChat / useSSE / ...）
│       ├── components/          #   通用组件（按业务分包）
│       ├── views/               #   页面
│       ├── stores/              #   Pinia 全局状态
│       ├── types/               #   TypeScript 类型定义
│       ├── router/              #   Vue Router 配置
│       ├── styles/              #   全局样式
│       └── layouts/             #   布局组件
├── tests/                       # 后端测试（pytest）
│   ├── test_health.py           #   健康检查测试
│   ├── test_database.py         #   数据库测试
│   ├── test_rag.py              #   检索测试
│   ├── test_memory.py           #   记忆测试
│   ├── test_graph.py            #   工作流测试
│   ├── test_services.py         #   服务测试
│   ├── test_parser.py           #   解析器测试
│   ├── test_api.py              #   API 集成测试
│   └── test_e2e.py              #   端到端测试
├── data/                        # 运行时数据（FAISS 索引、SQLite）
├── logs/                        # 运行日志
├── requirements.txt             # Python 依赖
├── .env.example                 # 环境变量模板
├── run.py                       # 后端启动入口
└── CLAUDE.md                    # 项目开发规范
```

---

## 🔄 LangGraph 工作流

8 节点 Agent Workflow，每个节点职责单一，通过 **State** 通信，**Conditional Edge** 控制分支：

```
                        ┌──────────────┐
                        │   用户问题    │
                        └──────┬───────┘
                               │
                        ┌──────▼───────┐
                        │ ① QueryRewrite│  同义词扩展 / 指代消解
                        └──────┬───────┘
                               │
                        ┌──────▼───────┐
                        │ ② IntentRoute │  意图分类 / 路由决策
                        └──────┬───────┘
                               │
                        ┌──────▼───────┐
                        │  ③ KBSelect   │  Embedding 语义匹配 + LLM 路由
                        └──────┬───────┘
                               │
                        ┌──────▼───────┐
                        │④ HybridRetrieve│  FAISS + BM25 并行检索
                        └──────┬───────┘
                               │
                        ┌──────▼───────┐
                        │⑤ RelevanceEval│  LLM 逐条 1-5 分 + 关键词过滤
                        └──┬───────┬───┘
                           │       │
                    (不足)  │       │  (达标)
                   ┌───────▼──┐ ┌──▼───────────┐
                   │⑥ RetryRewrite│ │⑦ AnswerGenerate│  流式 Token 输出
                   │  最多 2 次  │ └──────┬───────┘
                   └───────┬──┘        │
                           │    ┌──────▼───────┐
                           └────│⑧ CitationFormat│  文档名去重 + 页码映射
                                └──────┬───────┘
                                       │
                                ┌──────▼───────┐
                                │  ErrorHandler │  全局异常兜底
                                └──────────────┘
```

**关键设计**：
- 每个 Node 只做一件事，通过 `WorkflowState` 传递数据
- 条件边 `RelevanceEval → RetryRewrite` 实现自适应检索
- `SqliteSaver` 持久化 Workflow 状态，支持断点恢复

---

## 🔍 RAG 检索架构

```
文档上传
  → Parser (MinerU PDF / HTML / TXT / MD)
  → Cleaner (去噪 / 格式化)
  → Chunker (滑动窗口分块, chunk_size=500, overlap=50)
  → Embedding (OpenAI / 兼容接口)
  → FAISS Index (向量存储)
  → BM25 Index (关键词索引)

用户问题
  → Query Rewrite (同义词扩展)
  → 并行检索:
     ├─ FAISS.vector_search(query, top_k)  → 语义结果
     └─ BM25.keyword_search(query, top_k)  → 关键词结果
  → Min-Max 归一化
  → 加权融合: final_score = 0.7 × vec_score + 0.3 × kw_score
  → Top-K 排序
  → 返回候选 Chunks
```

**融合策略**：默认权重 `0.7:0.3`（向量:关键词），可通过环境变量 `RETRIEVAL_VECTOR_WEIGHT` / `RETRIEVAL_KEYWORD_WEIGHT` 调整。

---

## 🤝 贡献指南

本项目欢迎 Issue 和 PR。

### 开发流程

1. Fork 本仓库
2. 创建特性分支：`git checkout -b feature/amazing-feature`
3. 遵循 [CLAUDE.md](CLAUDE.md) 中的开发规范
4. 确保测试通过：`pytest tests/ -v`
5. 提交代码：`git commit -m 'feat: add amazing feature'`
6. 推送分支：`git push origin feature/amazing-feature`
7. 创建 Pull Request

### 代码规范

- **后端**：Python 标准命名，Type Hint 优先，Loguru 统一日志
- **前端**：Composition API + `<script setup lang="ts">`，Scoped CSS，禁止 `any`
- **架构**：单⼀职责，高内聚低耦合，KISS / DRY / SOLID

详见 [CLAUDE.md](CLAUDE.md)。

---

## 📄 License

本项目基于 **MIT License** 开源，详见 [LICENSE](LICENSE) 文件。

---

<p align="center">
  <sub>Built with ❤️ using FastAPI + LangGraph + Vue 3</sub>
</p>
