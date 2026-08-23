"""
论文知识问答工作流节点函数

每个 Node 只做一件事，通过 State 通信，禁止直接调用其他 Node。
通过 RunnableConfig 注入 LLM / Retriever / Embedding 等外部依赖。
"""

import re as _re
from langgraph.types import RunnableConfig

from loguru import logger

from app.graph.state import GraphState


# ============================================================
# 查询改写
# ============================================================

def query_rewrite_node(state: GraphState, config: RunnableConfig = None) -> dict:
    """将用户原始查询改写为更适合检索的形式，支持同义词扩展"""
    original = state.get("query", "")
    retry_count = state.get("retry_count", 0)
    llm = _get_from_config(config, "llm")

    if llm is not None:
        try:
            if retry_count > 0:
                prompt = (
                    f"上一次检索未找到相关内容。请用完全不同的关键词和角度重新表述以下问题，"
                    f"提取其中的核心概念。只输出改写后的查询，不要加任何解释。\n\n"
                    f"原始查询：{original}"
                )
            else:
                prompt = (
                    f"将以下用户查询改写为更适合知识库检索的语句。要求：\n"
                    f"1. 补充同义词和相关术语（如'校准'可拓展为'校准 复校 计量 检定'）\n"
                    f"2. 保留原始问题的核心意图\n"
                    f"3. 只输出一句话，不要解释\n\n"
                    f"原始查询：{original}"
                )
            response = llm.invoke(prompt)
            rewritten = response.content if hasattr(response, "content") else str(response)
            return {"rewritten_query": rewritten.strip(), "retry_count": retry_count + 1}
        except Exception:
            pass

    return {"rewritten_query": original, "retry_count": retry_count + 1}


# ============================================================
# 意图路由
# ============================================================

def intent_router_node(state: GraphState) -> dict:
    """识别用户意图类型，当前硬编码为 knowledge_qa"""
    return {"intent": "knowledge_qa"}


# ============================================================
# 知识库选择 — 混合路由（embedding 相似度 + LLM 判决）
# ============================================================

async def knowledge_selection_node(state: GraphState, config: RunnableConfig = None) -> dict:
    """根据 query 与各 KB 描述的语义相似度，结合 LLM 选择目标知识库"""
    kb_id = state.get("kb_id", 0)
    if kb_id != 0:
        return {"kb_id": kb_id, "intent": "knowledge_qa"}

    embedding_model = _get_from_config(config, "embedding")
    if embedding_model is None:
        return {"kb_id": 0, "intent": "knowledge_qa"}

    try:
        import json, math
        from app.database import async_session as _sf
        from sqlalchemy import select
        from app.models.knowledge_base import KnowledgeBase

        async with _sf() as session:
            kbs = (await session.execute(select(KnowledgeBase))).scalars().all()

        if not kbs:
            return {"kb_id": 0, "intent": "knowledge_qa"}

        query = state.get("rewritten_query") or state.get("query", "")
        query_vec = await embedding_model.embed_text(query)
        scored = []

        for k in kbs:
            if k.embedding_vector:
                try:
                    kb_vec = json.loads(k.embedding_vector)
                    dot = sum(a * b for a, b in zip(query_vec, kb_vec))
                    norm_q = math.sqrt(sum(a * a for a in query_vec))
                    norm_k = math.sqrt(sum(b * b for b in kb_vec))
                    sim = dot / (norm_q * norm_k) if norm_q and norm_k else 0
                    scored.append((k.id, k.name, round(sim, 4)))
                except Exception:
                    scored.append((k.id, k.name, 0))
            else:
                scored.append((k.id, k.name, 0))

        scored.sort(key=lambda x: x[2], reverse=True)

        logger.debug(f"选库: query='{state.get('query','')[:40]}'")
        for sid, sname, sscore in scored:
            logger.debug(f"  KB#{sid} {sname}: {sscore}")

        llm = _get_from_config(config, "llm")
        if llm and len(scored) > 1 and scored[0][2] > 0:
            kb_list = "\n".join(f"{sid}.{sname}（{sscore}）" for sid, sname, sscore in scored[:5])
            prompt = f"用户问题：{state.get('query', '')}\n\n知识库语义匹配度：\n{kb_list}\n\n请选择最相关的知识库ID（只回复数字）。都不相关回复0。"
            response = llm.invoke(prompt)
            selected = (response.content if hasattr(response, "content") else str(response)).strip()
            llm_choice = int("".join(c for c in selected if c.isdigit()) or "0")
            logger.debug(f"  → LLM选择: KB#{llm_choice}")
            kb_id = llm_choice
        elif scored and scored[0][2] > 0.3:
            kb_id = scored[0][0]
            logger.debug(f"  → 自动选择: KB#{kb_id}")
        else:
            logger.debug("  → 无匹配，全局检索")
    except Exception as e:
        logger.warning(f"选库失败: {e}")

    return {"kb_id": kb_id, "intent": "knowledge_qa"}


# ============================================================
# 混合检索
# ============================================================

async def retrieval_node(state: GraphState, config: RunnableConfig = None) -> dict:
    """执行 FAISS + BM25 混合检索，支持按 kb_id 过滤"""
    retriever = _get_from_config(config, "retriever")

    if retriever is not None:
        for attempt in range(3):
            try:
                query = state.get("rewritten_query", state.get("query", ""))
                k = _get_from_config(config, "retrieval_k", 5)
                kb_id = state.get("kb_id", 0) or None
                results = await retriever.search(query, k=k, kb_id=kb_id)

                docs = [
                    {"chunk_id": c.chunk_id, "content": c.content, "score": round(s, 4),
                     "doc_name": c.metadata.get("doc_name", "")}
                    for c, s in results
                ]
                logger.debug(f"检索: query='{query[:60]}' kb_id={kb_id} → {len(docs)}条")
                for d in docs:
                    logger.debug(f"  [{d['score']}] {d['chunk_id']}:\n{d['content']}\n---")
                return {"retrieved_documents": docs}
            except Exception:
                if attempt == 2:
                    raise

    return {"retrieved_documents": []}


# ============================================================
# 相关性评估
# ============================================================

async def relevance_evaluation_node(state: GraphState, config: RunnableConfig = None) -> dict:
    """对检索结果逐条进行 1-5 分 LLM 评分，结合关键词过滤无关文档"""
    docs = state.get("retrieved_documents", [])
    if not docs:
        return {"relevance_scores": []}

    query = state.get("rewritten_query") or state.get("query", "")
    llm = _get_from_config(config, "llm")
    relevance_scores: list[dict] = []

    if llm is not None:
        for i, doc in enumerate(docs[:10]):
            try:
                import json as _json
                meta = {k: v for k, v in doc.items() if k != "content"}
                prompt = (
                    f"评估以下文档片段与用户问题的相关性（1-5分）：\n"
                    f"用户问题：{query}\n"
                    f"片段元数据：{_json.dumps(meta, ensure_ascii=False)}\n"
                    f"片段内容：{doc.get('content', '')}\n"
                    f"注意：如果问题提到具体编号/名称，请对比元数据中的文件来源，"
                    f"内容相似但来源不同的应打低分。"
                    f"5=完全匹配 | 4=高度相关 | 3=部分相关 | 2=弱相关 | 1=不相关。只回复数字。"
                )
                response = await llm.ainvoke(prompt)
                score_text = (response.content if hasattr(response, "content") else str(response)).strip()
                score = int("".join(c for c in score_text if c.isdigit()) or "3")
                score = max(1, min(5, score))
            except Exception:
                score = 3
            relevance_scores.append({"index": i, "score": score})

        keywords = [w for w in _re.findall(r"[一-鿿]{2,}", query)]
        sorted_scores = sorted(relevance_scores, key=lambda s: s["score"], reverse=True)
        keep_indices: set[int] = set()
        for s in sorted_scores:
            idx = s["index"]
            doc_text = docs[idx].get("content", "")
            has_kw = any(kw in doc_text for kw in keywords)
            if has_kw or s["score"] >= 2:
                keep_indices.add(idx)

        if not keep_indices:
            keep_indices = {s["index"] for s in sorted_scores}

        filtered_docs = [docs[i] for i in keep_indices if i < len(docs)]
        logger.debug(f"评分: {len(docs)}条 → 保留{len(filtered_docs)}条")
        for s in sorted_scores:
            mark = "✓" if s["index"] in keep_indices else "✗"
            kw = "+KW" if keywords and any(kw in docs[s["index"]].get("content", "") for kw in keywords) else ""
            logger.debug(f"  {mark} [{s['score']}分{kw}] {docs[s['index']].get('doc_name','')[:40]}")

        return {"retrieved_documents": filtered_docs, "relevance_scores": relevance_scores}

    return {"relevance_scores": relevance_scores}


# ============================================================
# 答案生成
# ============================================================

async def generation_node(state: GraphState, config: RunnableConfig = None) -> dict:
    """基于检索文档和历史对话生成答案，LLM 不可用时降级为骨架回答"""
    llm = _get_from_config(config, "llm")

    if llm is not None:
        try:
            prompt = _build_prompt(state)
            full_answer = ""
            async for chunk in llm.astream(prompt):
                content = chunk.content if hasattr(chunk, "content") else str(chunk)
                if content:
                    full_answer += content
            if full_answer:
                return {"answer": full_answer, "citations": _extract_citations(state)}
        except Exception:
            pass

    query = state.get("query", "")
    return {"answer": f"抱歉，未能生成关于 '{query}' 的回答，请稍后重试。", "citations": []}


# ============================================================
# 引用格式化
# ============================================================

def citation_formatting_node(state: GraphState) -> dict:
    """从检索文档中提取引用来源，去重后返回"""
    citations = _extract_citations(state)
    seen = set()
    unique = []
    for c in citations:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return {"citations": unique}


# ============================================================
# 错误处理
# ============================================================

def error_handler_node(state: GraphState) -> dict:
    """检查 answer/error 状态，必要时提供 fallback 回复"""
    error = state.get("error", "")
    answer = state.get("answer", "")

    if error and not answer:
        return {"answer": "抱歉，处理您的请求时遇到了问题，请稍后重试。", "citations": []}

    if not answer:
        query = state.get("query", "")
        return {"answer": f"抱歉，未能找到关于 '{query}' 的相关信息，请尝试更换关键词或确认知识库中已上传相关文档。", "citations": []}

    return {}


# ============================================================
# 工具函数
# ============================================================

def _get_from_config(config: RunnableConfig | None, key: str, default=None):
    """从 LangGraph RunnableConfig 中安全提取 configurable 参数"""
    if config is None:
        return default
    return config.get("configurable", {}).get(key, default)


def _build_prompt(state: GraphState) -> str:
    """基于 State 构建 LLM 提示词"""
    query = state.get("query", "")
    docs = state.get("retrieved_documents", [])
    messages = state.get("messages", [])

    history = ""
    for msg in messages[-6:]:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        history += f"{role}: {content}\n"

    doc_text = ""
    if docs:
        doc_text = "\n相关知识库内容：\n"
        for i, doc in enumerate(docs):
            doc_text += f"[{i+1}] {doc.get('content', '')}\n"
    else:
        doc_text = "\n（未检索到相关知识库内容）\n"

    return (
        f"你是一个企业知识库智能助手。基于以下信息回答用户问题。\n\n"
        f"## 对话历史\n{history}\n"
        f"## 知识库检索结果{doc_text}\n"
        f"## 用户问题\n{query}\n\n"
        f"回答要求：\n"
        f"1. 必须基于检索到的文档内容回答，不要编造信息\n"
        f"2. 仔细查找文档中与问题相关的所有细节，包括日期、数字、周期、条件等\n"
        f"3. 如果文档中有明确答案，直接引用具体内容\n"
        f"4. 如果检索结果确实不包含相关信息，如实告知"
    )


def _extract_citations(state: GraphState) -> list[str]:
    """从检索文档中提取引用标识，优先使用文档名"""
    docs = state.get("retrieved_documents", [])
    seen = set()
    result = []
    for d in docs:
        label = d.get("doc_name") or d.get("chunk_id", "")
        if label and label not in seen:
            seen.add(label)
            result.append(label)
    return result
