# 论文问答相关性判定 + 单篇检索优化(设计方案)

> 日期: 2026-08-21
> 状态: 草案(待用户审阅)
> 前置: 论文全库问答 Workflow 化已完成(9 节点图); 现状是"所有非闲聊/清单问题一律走 RAG", 导致常识/无关问题答非所问、单篇具体问题检索不精准。

## 1. 目标

让智能问答"更智能":
1. **相关性判定**: 先判断"这个问题跟论文库相关吗"——相关才走 RAG 检索论文, 不相关(常识/闲聊/无关话题)由 LLM 正常对话回答;
2. **单篇检索优化**: 问题提到具体论文名时, 优先精确匹配该论文再采样, 解决"PGDUN 的 PGSA 怎么实现"答非所问;
3. **对比问题支持**: 问题同时提到多篇论文时, 采样覆盖这些论文并对比回答。

## 2. 现状与问题(已实测)

| 问题 | 现状路径 | 结果 |
|---|---|---|
| "什么是深度学习"(常识) | 走 RAG | 答成 SR 论文综述(跑题) |
| "今天天气怎么样"(无关) | 走 RAG | 答成论文综述(跑题) |
| "PGDUN 的 PGSA 怎么实现"(单篇) | RAG + 3次重试 | 答成深度学习/AMCANet(跑题) |
| "MWAT-SR 和 Dual-domain 不同"(对比) | RAG + 3次重试 | 答成 PGDUN(跑题) |
| "超分方向有哪些方法"(宽泛) | RAG | 正常 |

根因: intent_router 只有 3 路(闲聊/清单/问答), 没有"问题与论文库相关性"判断; 且按方向采样对单篇/对比问题不够精准。

## 3. 设计

### 3.1 相关性判定(混合方案)

intent_router 增加第 4 路 `general`(自由对话), qa 细分时判定相关性:

```
intent_router
  |-- chitchat -> chat_node
  |-- catalog  -> catalog_node
  |-- qa: 先规则粗判
  |     命中库内元数据(论文标题词/方向词/作者名/论文术语) -> 走 RAG
  |     未命中 -> LLM 判定 "问题需要查论文库吗?"
  |         是 -> 走 RAG
  |         否 -> general_chat_node(自由对话)
  `-- general -> general_chat_node
```

#### 规则粗判(relevance_rule)

问题文本若包含以下任一 → 必进 RAG:
- 库内论文标题的显著词(PGDUN/PromptSR/MWAT-SR/AMCANet/Text-Image 等, 从 candidates/全库标题提取);
- 方向词(超分辨率/超分/去雾/去噪/复原/图像修复 等, 从库内 research_field 提取);
- 作者名(库内 authors_json 中的人名);
- 论文领域术语(轻量/注意力/Transformer/损失函数/重建/光谱 等常见词)。

#### LLM 判定(relevance_llm)

规则未命中时, 用一次 LLM 调用判定:

```
你是问答助手。判断以下问题是否需要检索论文库(论文库包含图像超分辨率/去雾/复原等方向的学术论文)。
需要检索 -> 输出 true; 不需要(常识/闲聊/无关话题) -> 输出 false。
问题: {input_text}
只输出 true 或 false。
```

### 3.2 general_chat_node(自由对话)

新节点: 不检索, LLM 直接回答(带历史)。prompt 类似 chitchat 但定位为"通用问答助手, 擅长论文库相关话题, 也可回答常识"。

### 3.3 单篇检索优化(single-paper match)

retrieve_node 之前加一步: 用问题文本精确匹配库内论文标题;

```
matched = [p for p in candidates if 标题关键词出现在问题中]
if matched:  # 问题明确提到具体论文
    只对 matched 论文采样(每篇 top-5, 深挖单篇)
else:
    按方向采样(现状, 每篇 top-3)
```

匹配方式: 标题的显著词(去掉通用词)做子串匹配; 如 "PGDUN" 命中标题含 PGDUN 的论文。

### 3.4 对比问题(多篇匹配)

3.3 的匹配天然支持: 问题含多篇论文名时, matched 是多篇, 对每篇采样并对比。

## 4. 状态扩展

LibraryQAState 增加:
```
relevance: str       # "rag" | "general"(判定结果)
matched_papers: list # 单篇/对比匹配到的论文
```

## 5. 图变更

```
intent_router -> (qa) -> relevance_check(新: 规则+LLM 判定)
  relevance_check -> [rag] direction_select -> retrieve(single-match 优先) -> ...
  relevance_check -> [general] general_chat_node -> END
```

## 6. 与现有功能兼容

- 闲聊/清单分支不变;
- 宽泛综述(命中方向词)仍走 RAG, 行为不变;
- 多轮上下文/history 在 general_chat 也使用;
- 重试环仅 RAG 路径保留。

## 7. 文件变更

```
app/paper/library_nodes.py  修改: intent_router 扩展 + relevance_check_node + general_chat_node + retrieve 单篇匹配
app/paper/library_state.py  修改: 加 relevance/matched_papers 字段
app/paper/library_graph.py  修改: 加 relevance_check 节点和边
app/paper/prompts.py        修改: 加 build_relevance_prompt + build_general_chat_prompt
tests/test_library_nodes.py 修改: intent/relevance/general/单篇匹配测试
tests/test_library_graph.py 修改: 图结构 + relevance 分支测试
```

## 8. 验证

1. "什么是深度学习" -> general_chat(自由回答, 不检索);
2. "今天天气" -> general_chat;
3. "PGDUN 的 PGSA 怎么实现" -> 单篇匹配 PGDUN, 检索该篇回答;
4. "MWAT-SR 和 Dual-domain 对比" -> 匹配两篇, 对比回答;
5. "超分方向有哪些方法" -> 仍走 RAG(方向词命中);
6. 回归: 全量测试 + 前端 build。

