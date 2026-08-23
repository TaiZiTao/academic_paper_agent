# 科研文献搜索下载 Agent(设计方案)

> 日期: 2026-08-21
> 状态: 已批准(用户确认方案 A: 单体扩展 + 内置 Playwright 浏览器服务)
> 前置: 论文库管理(研究方向分组)、全库问答(方案 B)已上线; 本设计是"科研助手 Agent 系统"的第一个子系统: 全网文献搜索与下载入库

## 1. 目标

把论文库从"手动上传"扩展为"Agent 自动检索入库": 用户用自然语言描述科研需求, Agent 规划检索词、多源并行搜索、相关性排序, 用户勾选后自动下载 PDF 并走现有解析管道入库。

核心能力:
- **LLM 驱动的检索 Agent**: 自然语言 → 多组检索词 + 数据源选择 → 并行搜索 → 去重合并 → 相关性排序;
- **四级下载策略**: 开放直链 → Unpaywall OA 镜像 → 学校 VPN 浏览器自动化 → 手动链接兜底;
- **西南交通大学 VPN 集成**: 首次手动登录复用浏览器会话(不存密码), 之后自动访问付费墙数据库下载;
- **无缝入库**: 下载 PDF 直接复用现有 PaperService 解析流程, 与手动上传体验一致;
- **过程可视化**: Agent 规划/搜索/排序过程 SSE 实时推送到前端。

## 2. 范围

### 2.1 本次做

1. app/research/ 模块: Searcher(arxiv/s2) + SearchAgent(LangGraph 4 节点) + Downloader(四级降级) + BrowserService(Playwright);
2. 前端论文库页新增「文献检索」标签页: 搜索 → 结果卡片 → 勾选下载并入库 → 导入队列进度;
3. paper_imports 表: 下载入库任务持久化跟踪;
4. VPN 浏览器会话管理 API(登录/状态/关闭);
5. 下载限速与错误降级; 测试覆盖。

### 2.2 本次不做(后续阶段)

- 其他付费墙数据库的专门适配(IEEE/Springer 通用页面模式优先, 特殊站点后续补);
- 批量下载策略调度(一次导入多篇即串行队列, 不做优先级/并发调度);
- 检索历史持久化与收藏(前端会话内保留);
- 期刊/会议影响因子过滤;
- 与全库问答聚合索引的联动(新论文入库后自动进聚合索引已有机制覆盖)。

## 3. 运行环境

- 项目 venv: `E:/codex/GraphRAG--main/venv`(基于 conda env `D:/anaconda3/envs/pytorch`, Python 3.10.20, torch 2.2.2+cu118);
- 新增依赖: `playwright`(pip 装进 venv) + `playwright install chromium`(下载浏览器内核);
- LLM: 复用现有 OpenAI 兼容接口(阿里云百炼 qwen-plus, 见 .env LLM_*);
- 网络: arXiv/Semantic Scholar 直连, 不可用时经 `RESEARCH_PROXY`(Clash 等)访问; 付费墙数据库经学校 VPN。

## 4. 架构与组件

### 4.1 总体架构

在现有 FastAPI 应用内新增 `app/research/` 模块(与 app/paper/ 平级), 前端论文库页新增「文献检索」标签页。复用现有 LLM(qwen-plus)、SQLite 数据库、入库解析管道。

### 4.2 组件划分

**① Searcher 层**(app/research/searchers/)
- `arxiv.py` ArxivSearcher: arXiv API, 免费无需 Key, 按相关度排序, 直接提供 PDF 直链;
- `s2.py` SemanticScholarSearcher: S2 API, 免费限速, 含引用数/年份/开放 PDF 链接;
- 统一输出 `SearchResult {source, title, authors[], year, venue, abstract, doi, pdf_url, page_url, citations}`。

**② SearchAgent**(app/research/agent.py, LangGraph 4 节点)
- `PlanQuery`: LLM 把自然语言拆成多组检索词 + 选择数据源(如「轻量超分的注意力机制」→ ["lightweight super-resolution attention"] × [arxiv, s2]);
- `ParallelSearch`: asyncio.gather 并行调各源, 单源超时降级(默认 15s);
- `DedupeMerge`: 标题归一化去重(DOI 对齐);
- `RelevanceRank`: LLM 按用户意图打分排序, 截取 top-N(默认 20);
- LLM 不可用时降级为直查模式(关键词 + 规则排序), 不阻断搜索。

**③ Downloader**(app/research/downloader.py)四级降级
- L1: arXiv/S2 开放 PDF 直链 → httpx 下载;
- L2: DOI → Unpaywall 查 OA 镜像 → 下载;
- L3: 付费墙 → BrowserService 用 VPN 会话打开论文页 → 点下载 → 捕获浏览器下载文件;
- L4: 全部失败 → 返回 page_url + DOI, 前端提示手动下载后拖入(走现有上传);
- 全局串行下载队列 + `RESEARCH_DOWNLOAD_DELAY` 间隔限速, 防过量下载封 IP。

**④ BrowserService**(app/research/browser.py, Playwright)
- chromium, 生命周期挂在 FastAPI lifespan;
- 首次: 打开有头窗口手动登录**西南交通大学 VPN 网页门户**(`VPN_PORTAL_URL`) → 持久化浏览器 profile(cookie);
- 之后: 复用会话自动导航到付费墙论文页 → 点击「Download PDF」→ 监听 download 事件取文件;
- 会话失效检测(访问目标页探测) → 前端提示重新登录;
- 代理可配置(供 arXiv/S2 直连不可用时使用)。

**⑤ 入库管道**: 下载 PDF 直接调用现有 PaperService 解析流程(MinerU → 分块 → 图表 → 单篇索引), 与手动上传一致。

## 5. 数据模型

### 5.1 新增表 paper_imports(下载入库任务)

```
paper_imports
├── id            INTEGER PK
├── title         VARCHAR       -- 论文标题
├── source        VARCHAR       -- arxiv | semantic_scholar
├── external_id   VARCHAR NULL  -- arxiv_id / S2 paperId
├── doi           VARCHAR NULL
├── pdf_url       VARCHAR NULL  -- 开放直链(可空)
├── page_url      VARCHAR NULL  -- 论文页链接(付费墙)
├── status        VARCHAR       -- pending | downloading | parsing | done | failed
├── progress      INTEGER       -- 0-100(下载/解析进度)
├── error_message VARCHAR NULL
├── paper_id      INTEGER NULL  -- 入库成功后关联 papers.id
├── created_at / updated_at
```

- 复用现有幂等迁移模式(PRAGMA 检查表/列, 不存在则建);
- 下载/解析进度写入 progress, 前端轮询展示。

## 6. API 设计(app/research/router.py, 前缀 /api/v1/research)

### 6.1 搜索(SSE 流式, Agent 过程可视化)

```
POST /api/v1/research/search
body: {"query": "轻量级图像超分辨率的注意力机制", "top_k": 20}

SSE 事件:
  event: plan       data: {"queries":["lightweight image super-resolution attention"], "sources":["arxiv","s2"]}
  event: progress   data: {"stage":"searching","source":"arxiv","status":"ok","hits":38}
  event: results    data: {"items":[SearchResult...], "total": 20}
  event: error      data: {"message":"..."}   // LLM 不可用时已降级为直查
```

### 6.2 下载入库(REST + 后台任务)

```
POST /api/v1/research/imports          # body: {items:[{source,title,doi,pdf_url,page_url}...]}
                                       # → 创建 paper_imports 行 → 串行下载队列(BackgroundTasks)
                                       # 返回 [{import_id, title, status}]
GET  /api/v1/research/imports          # 列出全部导入任务(前端轮询进度)
GET  /api/v1/research/imports/{id}     # 单个状态
POST /api/v1/research/imports/{id}/retry   # 失败重试
```

### 6.3 VPN 浏览器会话管理

```
GET  /api/v1/research/browser/status   # 会话状态: none | alive | expired
POST /api/v1/research/browser/login    # 打开有头浏览器 → 手动登录西交 VPN → 持久化会话
POST /api/v1/research/browser/verify   # 探测会话有效性(访问一次目标数据库页)
POST /api/v1/research/browser/close    # 关闭浏览器释放资源
```

## 7. 配置(.env 新增, 全部可空)

```
RESEARCH_TOP_K=20
RESEARCH_SEARCH_TIMEOUT=15        # 单源搜索超时(秒)
RESEARCH_DOWNLOAD_DELAY=4         # 下载间隔(秒), 防过量下载封IP
RESEARCH_PROXY=                   # 可选 http://127.0.0.1:7890 (Clash), 供 arXiv/S2 直连
VPN_PORTAL_URL=https://vpn.swjtu.edu.cn
UNPAYWALL_EMAIL=                  # 可选, Unpaywall API 要求邮箱参数
```

## 8. 前端设计(论文库页新增「文献检索」标签页)

- 搜索框: 输入自然语言需求 + [Agent搜索]/[直查] 两个模式;
- Agent 过程流折叠区: 规划词/来源/各源命中数/去重结果/排序完成, 来自 SSE plan/progress 事件;
- 结果卡片列表(可勾选): 标题/作者/年份/会议期刊/被引数/摘要(展开), 徽标区分「开放获取(直链)」「VPN下载(付费墙)」「页面链接」;
- 底部操作栏: 已选计数 + [下载并入库];
- 导入队列右侧抽屉: 每项标题 + 状态徽标(下载中→解析中→完成/失败) + 进度条 + 失败可重试, 轮询 GET /imports;
- VPN 登录弹窗: 「需要登录西南交通大学 VPN」→ [打开浏览器登录] → 有头浏览器自动打开 VPN_PORTAL_URL, 登录成功后会话持久化;
- 复用现有 Element Plus + SSE 模式(聊天页已有); 入库完成后论文库主列表可见新论文。

## 9. 错误处理

| 场景 | 处理 |
|------|------|
| LLM 不可用 | Agent 降级为直查模式(关键词+规则排序), 不阻断搜索 |
| 单源超时/失败 | 只降级该源, 其余源继续; 最终结果标记来源 |
| 下载失败(L1/L2) | 尝试 L3 VPN → 再失败 L4 返回页面链接, 状态 failed 可重试 |
| VPN 会话过期 | 下载任务暂停, 前端弹窗提示重新登录 |
| 下载队列限速 | 全局串行 + RESEARCH_DOWNLOAD_DELAY 间隔, 防封 IP |
| 解析失败 | 复用现有 failed 状态 + 论文库页重试入口 |
| 浏览器崩溃 | BrowserService 自动重启, 会话 profile 持久化不丢失 |

## 10. 测试与验证

### 10.1 后端单测
- Searcher: mock 响应/超时/空结果;
- DedupeMerge: DOI 对齐/标题归一化去重;
- RelevanceRank: LLM mock 打分排序;
- Downloader: L1→L4 各级降级路径, 队列限速;
- API 端点: search SSE / imports CRUD / browser 状态机;
- 环境依赖: 测试不要求真实网络(全部 mock)。

### 10.2 集成
- Agent 全流程(mock LLM + mock 源): 规划→并行搜索→去重→排序→结果;
- VPN 会话状态机: none→alive→expired 流转;
- 下载→入库端到端: 构造本地 PDF fixture 走 PaperService 解析, 断言 papers 表新增。

### 10.3 前端
- 搜索结果渲染、勾选交互、导入队列轮询(vitest) + build;
- 回归: 现有 139+ 测试全绿。

## 11. 迁移与兼容

1. **DB**: 新建 paper_imports 表(幂等), 不动现有表;
2. **后端**: 新增 app/research/ 模块与路由, 现有 /papers 与 /chat 接口不动;
3. **前端**: 论文库页新增 Tab, 现有功能不动; 新依赖只在需要时初始化(Playwright 延迟到首次 VPN 操作);
4. **新论文入库**: 走现有解析管道, 自动进入单篇索引; 全库问答聚合索引的增量维护沿用既有机制。
