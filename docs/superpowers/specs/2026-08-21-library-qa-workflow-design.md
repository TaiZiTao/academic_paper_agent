# 论文全库问答 Workflow 化(设计方案)

> 日期: 2026-08-21
> 状态: 草案(待用户审阅)
> 前置: 方案 B 全库问答已上线(动态过滤+聚合采样+多轮+闲聊/清单分支); 本次把线性 run_library_qa 重构为 LangGraph 图

## 1. 目标

把论文全库问答从"线性 if-else 函数"重构为 **LangGraph 节点图**, 获得:
- **节点级可视化**: 前端复用企业 WorkflowPanel, 实时显示执行到哪个节点(检索/评估/生成…);
- **检索质量评估 + 重试**: 检索后 LLM 评估相关性, 不足时换关键词重试(最多 3 次), 提升回答质量;
- **多方向选择编排**: 研究方向选择(提取+归一化)成为独立节点, 可扩展;
- **分支统一进图**: 闲聊/清单/问答三类问题由 intent_router 路由, 节点面板完整可见。

## 2. 现状(被重构的代码)

`app/paper/service.py` 的 `run_library_qa`(约 300 行)现为线性流程:

```
0) 加载历史 -> 1) LLM 提取过滤条件 -> 1b) 方向归一化(超分→超分辨率)
  -> 2) 候选集 + 降级 -> 3a0) 闲聊分支 -> 3a) 清单分支 -> 3) 按篇采样
  -> 4) 聚合生成 -> 5) 会话持久化
```

问题: 分支(闲聊/清单/问答)靠 if-else 堆叠, 无检索质量评估, 前端只有 filter/progress 简单事件, 无法看到节点级执行状态。

## 3. 目标图结构(6 节点 + 1 环)

```
START -> intent_router
   |-- [chitchat] -> chat_node -> END
   |-- [catalog]  -> catalog_node -> END
   `-- [qa]       -> direction_select -> retrieve -> relevance_evaluate
                        ^                      |
                        `-- rewrite_query(重试) `  (不足时回跳, 最多3次)
        relevance 达标 -> generate -> cite_verify -> END
```

### 节点职责

| 节点 | 职责 | 复用现状 |
|---|---|---|
| intent_router | 分类: 闲聊/清单/问答 | 现有 chitchat/catalog 关键词逻辑搬入 |
| chat_node | 闲聊回复 | build_chitchat_prompt |
| catalog_node | 论文清单回复 | build_library_catalog_prompt |
| direction_select | 提取方向+归一化 -> 候选集 | 现有过滤/归一化/降级逻辑搬入 |
| retrieve | 按方向逐篇采样 | AggregateRetriever.sample_papers |
| rewrite_query | 重试: 换关键词重新检索 | LLM 改写(仿企业 query_rewrite_node) |
| relevance_evaluate | LLM 1-5 分评估 **仅 top-3** + 关键词过滤 | 仿企业 relevance_evaluation_node, 评分范围收窄 |
| generate | 聚合生成 | build_library_qa_prompt |
| cite_verify | 引用形状校验 | 现有 valid_paper_ids/valid_chunk_ids 逻辑 |

### 环(重试条件)

relevance_evaluate 后判定: 平均分 < 2 或高分片段不足 -> rewrite_query(重写查询) -> retrieve 再采; 最多 3 次; 3 次仍不足 -> 用现有证据生成(不报错, 提示证据有限)。

## 4. 状态设计

仿企业 GraphState(TypedDict, total=False):

```python
class LibraryQAState(TypedDict, total=False):
    session_id: str
    input_text: str
    history: list[dict]          # 多轮上下文
    intent: str                 # chitchat | catalog | qa
    filters: dict               # 动态过滤条件
    candidates: list            # 候选论文(ORM 对象)
    query: str                  # 当前检索词(重试时被改写)
    evidence: list              # PaperChunkData 列表
    relevance_scores: list      # LLM 评估分
    retry_count: int
    content: str                # 最终回答
    citations: list             # 安全引用
```

## 5. 事件流(前端 WorkflowPanel 复用)

service 用 graph.astream(state, stream_mode=["updates","messages"]) 驱动, 与企业管理版一致:

```
event: node  data: {"node":"intent_router","status":"running"}
event: node  data: {"node":"direction_select","status":"completed","filters":{...},"candidates":3}
event: node  data: {"node":"retrieve","status":"completed","docs_count":9}
event: node  data: {"node":"relevance_evaluate","status":"completed","avg_score":3.2}
event: node  data: {"node":"generate","status":"completed"}
event: token data: {"content":"..."}
event: done  data: {"content":"...","citations":[...]}
```

前端: 智能问答页右侧加回 <WorkflowPanel :nodes="workflowNodes" />(组件已存在, 从企业版移除的现在接回), useChat 消费 node 事件。

## 6. 与现有功能的兼容

- 多轮上下文(history): 在 START 前加载, 存入 state.history, generate/chat/catalog 节点使用;
- 闲聊/清单分支: 从 service 搬入 intent_router 路由, 行为不变(关键词规则保留);
- 方向归一化(超分->超分辨率): 搬入 direction_select, 逻辑不变;
- 候选降级(滤空->放宽): 搬入 direction_select, 逻辑不变;
- 引用校验: 搬入 cite_verify, 逻辑不变;
- 会话持久化: END 后由 service 统一写库(与企业版一致)。

## 7. 文件结构

```
app/paper/library_graph.py      新建: 图构建(仿 app/graph/workflow.py)
app/paper/library_nodes.py     新建: 节点函数(仿 app/graph/nodes.py)
app/paper/library_state.py     新建: LibraryQAState
app/paper/service.py           修改: run_library_qa 改为驱动图(大量逻辑搬入 nodes)
web/src/views/ChatView.vue     修改: 接回 WorkflowPanel
web/src/composables/useChat.ts 修改: 消费 node 事件, 重建 workflowNodes
tests/test_library_graph.py    新建: 图构建/路由/重试测试
tests/test_library_nodes.py    新建: 各节点单测
```

## 8. 验证

1. 后端: 图构建测试(节点齐全/边正确/环可触发);
2. 重试: 构造弱检索场景, 断言 rewrite_query 触发、retry_count 递增、3 次封顶;
3. 分支: 闲聊/清单/问答 各走正确节点链;
4. 回归: 现有 253 测试全过; 多轮/归一化/降级行为不变;
5. 前端: build + WorkflowPanel 显示节点状态;
6. E2E: Playwright 验证问答时节点面板逐步点亮。

## 9. 不做(本次范围外)

- 检索质量评估的阈值自动调优(先固定规则);
- 多方向并行检索(asyncio.gather 已有, 不新增);
- 跨请求状态持久化(checkpointer, 当前会话历史已够);
- 企业版选库节点(论文场景不需要实体级选库)。

