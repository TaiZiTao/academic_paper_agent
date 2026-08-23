# 论文全库问答(方案 B): 动态过滤 + 聚合索引 RAG(设计方案)

> 日期: 2026-08-20
> 状态: 草案(待用户审阅)
> 前置: 论文库管理(研究方向分组)已上线; 本设计是其"全库问答"阶段的完整方案

## 1. 目标

把侧边栏"智能问答"升级为**论文全库问答**: 用户用自然语言提问, 系统从整个论文库(目标 100+ 篇)检索证据并生成带引用的回答。

核心能力:
- **跨论文检索**: 答案可综合多篇论文(横向对比/综述型问题);
- **引用可跳转**: 每条引用带论文标题 + 章节 + 页码, 点击跳转对应论文工作台;
- **动态过滤**: 过滤条件由 LLM 从问题中提取(方向/年份/作者/关键词/语言), 不需要前端筛选项;
- **多轮对话**: 支持追问, 引用随上下文保持。

## 2. 范围

### 2.1 本次做

1. **发表年份提取**: 解析流程新增年份字段(从 PDF 元数据/首页文本提取, LLM 兜底);
2. **聚合索引**: 全库论文 chunks 合并建统一 FAISS + BM25 索引, chunk 元数据带 paper_id/section/page;
3. **动态过滤提取器**: LLM 从问题解析 {field, year_min, year_max, authors, keywords, language}, 转为检索候选集过滤;
4. **全库问答接口**: 流式问答(复用单篇 run_task 骨架), 覆盖过滤/采样/聚合生成/引用;
5. **前端**: 智能问答页改为论文全库问答(引用来源面板复用 PaperCitationList);
6. **测试**: 过滤解析/聚合检索/引用正确性/前端。

### 2.2 本次不做(后续阶段)

- 期刊/会议提取与过滤(需要额外解析首页版面, 后续补);
- 引用数/影响力过滤(需要外部数据源);
- 论文之间的语义关联图谱/推荐(研究方向的同方向推荐可基于现有 research_field 做, 但图谱不做);
- 企业问答 WorkflowPanel(选库/相关性重试节点不适用于论文全库)。

## 3. 数据模型

### 3.1 papers 表新增字段

```
publication_year INTEGER NULL  -- 发表年份(NULL=未知, 过滤时未知年份论文默认排除在年份范围外, 但可被"不过滤年份"的查询命中)
```

- 索引: 与 research_field 一起建复合索引, 支撑过滤查询;
- 迁移: 复用现有幂等迁移模式(PRAGMA 检查列存在, 不存在则 ALTER TABLE ADD COLUMN);
- 存量论文: 触发一次全量回填(LLM 从首页/摘要提取年份), 失败置 NULL 不阻断。

### 3.2 聚合索引

复用企业 RAG 的全局 Retriever 结构(Retriever = VectorStore + KeywordStore + weighted_fusion), 论文侧新建聚合索引:

```
data/papers/index_all/faiss.index   -- 全库向量索引(IndexFlatIP, 1536维)
data/papers/index_all/chunks.pkl    -- chunk 元数据(含 paper_id/section/page)
data/papers/index_all/bm25.json     -- 全库 BM25 序列化(可选, 或运行时重建)
```

- chunk 元数据扩展: {paper_id, paper_title, research_field, publication_year, section, page_start, page_end, chunk_id};
- 增量维护: 论文 process_paper 完成时 → 追加到聚合索引; 论文删除 → 聚合索引重建(或标记删除);
- 论文重解析(rebuild) → 更新该篇在聚合索引中的 chunks。

## 4. 动态过滤(问题即过滤)

### 4.1 过滤提取器

LLM 从用户问题提取结构化过滤条件, 复用现有 `_ainvoke_with_retry` 与 JSON 解析模式:

```json
{
  "field": "超分辨率",        // 研究方向, 空=不过滤
  "year_min": 2024,          // 发表年份下限, 空=不限
  "year_max": 2025,          // 发表年份上限, 空=不限
  "authors": ["张", "Wang"], // 作者关键词(任一命中), 空=不过滤
  "keywords": ["轻量"],      // 标题/关键词匹配词, 空=不过滤
  "language": "en"           // 语言, 空=不过滤
}
```

### 4.2 过滤到候选集

提取结果 → SQLAlchemy 条件:

```
research_field == X
AND publication_year BETWEEN Y1 AND Y2   -- 忽略 NULL 年份
AND authors_json LIKE %任一作者%
AND (title LIKE %kw% OR keywords_json LIKE %kw%)
AND language == L
```

- 无匹配 → 返回空候选, 提示"未找到符合条件的论文", 可建议放宽条件;
- 过滤提取失败/LLM 不可用 → 降级为不过滤的基础 RAG。

### 4.3 动态 vs 固定筛选的取舍

- 前端**不加**方向/年份下拉框: 过滤条件是问题的一部分, 随问随解析;
- 论文库管理页保留现有方向筛选(那是库管理浏览用途, 与问答无关)。

## 5. 全库问答流程

### 5.1 综述型问题(跨论文)的采样策略

普通 RAG 全局 top-k 会漏掉论文: 10 篇论文里只覆盖 2-3 篇。综述型问题用**按论文采样**:

```
① 动态过滤 → 候选论文集 P(如 超分+近两年 → 10 篇)
② 每篇 P 单独检索 top-3 chunk(优先命中引言/相关工作/局限章节)
   → 证据集覆盖全部候选论文; 总量上限 ~20000 字, 候选论文特别多时
   每篇降为 top-1/top-2(保证每篇至少 1 个片段, 不爆上下文)
③ 证据带 [论文标题; 章节; 页码] 组装进 prompt
④ LLM 按主题聚类生成: 共性不足 + 逐篇差异, 引用可跳转
```

### 5.2 单点型问题

(如"PGDUN 的 PGSA 模块怎么实现的")— 候选集自动缩小到相关论文, 走普通 top-k 检索即可。

### 5.3 问题类型判定

过滤提取器同时输出 `question_type`: `survey`(综述, 需按篇采样) 或 `specific`(单点, 全局 top-k)。
判定依据: 问题是否出现"对比/综述/哪些/不足/趋势/近年"等词, 或候选论文数 > 阈值。

## 6. API 设计

### 6.1 全库问答(流式)

```
POST /api/v1/papers/qa/stream
body: {"input_text": "近两年超分论文提出了哪些问题?", "session_id": "..."}

SSE 事件:
  event: filter     data: {"field":"超分辨率","year_min":2024,...,"candidates":10}
  event: progress   data: {"stage":"retrieval","status":"running","total_papers":10,"current":3}
  event: token      data: {"content":"..."}
  event: done       data: {"content":"...","citations":[{paper_id,paper_title,page,section,chunk_id,quote}],"suggestions":[...]}
```

- 会话: 复用 paper_messages 表按 session_id 持久化历史;
- 引用: 与单篇问答同一结构, 前端可直接复用 PaperCitationList;
- 跳转: 引用点击 → /papers/{paper_id}(带 section/page 定位参数, 论文工作台滚动到对应位置)。

### 6.2 存量接口兼容

- 论文库列表/详情/单篇问答接口不变;
- 智能问答入口的 ChatView 改为调用新接口(替换原企业 /chat 调用);
- 企业问答后端代码保留(路由仍注册), 前端不再调用。

## 7. 前端设计

### 7.1 智能问答页改造(ChatView)

- 页面标题: 智能问答(可保留);
- 中部: 对话消息流(复用 ChatMessageList, 消息含引用);
- 右侧: 引用来源面板(复用 PaperCitationList, 展示本次答案引用的论文/章节/页码);
- 移除: WorkflowPanel(企业编排节点不适用);
- 新增(可选): 检索过程提示(如"正在检索 10 篇论文…", 来自 filter/progress 事件);
- 会话侧栏: 复用 ConversationSidebar(历史会话列表)。

## 8. 年份提取实现

1. 解析流程中, 优先取 PDF 元数据(creationDate/metadata)中的年份;
2. 元数据缺失 → 首页文本正则匹配(20\d{2} / © 20\d{2} / 发表年份字段);
3. 仍未命中 → LLM 从首页/摘要提取(可空);
4. 提取失败 → NULL, 不阻断精读主流程; 库管理页可手动补。

## 9. 验证

1. 年份提取: 现有 5 篇论文回填后年份正确;
2. 动态过滤: "近两年超分论文" → 解析出 field=超分辨率, year_min=当前-2, 候选集正确;
3. 综述采样: 构造 5+ 篇跨方向论文, 综述问题回答覆盖全部候选论文(引用含全部论文标题);
4. 引用跳转: 点击引用 → 论文工作台对应章节/页码;
5. 回归: 单篇问答/翻译/报告不受影响; 后端测试 + 前端 build/vitest;
6. 容量: 100 篇规模下聚合检索延迟可接受(实测)。

## 10. 迁移与兼容(冲突规避)

1. **DB 迁移**: papers 表加 publication_year(幂等 ALTER TABLE);
2. **索引维护**: 聚合索引与单篇索引并存(单篇问答仍走单篇索引, 全库问答走聚合索引);
3. **前端切换**: ChatView 换数据源, 企业问答入口被替换但代码保留;
4. **新论文入库**: process_paper 完成后追加聚合索引(不阻断主流程, 失败下次启动补建);
5. **存量回填**: 启动时检测聚合索引缺失/过时 → 全量重建(5 篇秒级, 100 篇可接受)。

