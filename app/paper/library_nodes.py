"""论文全库问答 LangGraph 节点函数。

每个 Node 只做一件事, 通过 State 通信。
通过 RunnableConfig 注入 session_factory / retriever / llm。
"""

import re as _re

from sqlalchemy import or_, select
from langgraph.types import RunnableConfig

from loguru import logger

from app.models.paper import Paper
from app.paper.library_state import LibraryQAState
from app.paper.nodes import _json_content
from app.paper.prompts import (
    build_chitchat_prompt,
    build_filter_extraction_prompt,
    build_library_catalog_prompt,
    parse_filter_response,
)


def _get_from_config(config, key):
    if config is None:
        return None
    return config.get("configurable", {}).get(key)


# ============================================================
# 意图路由 / 闲聊 / 库清单
# ============================================================

CHITCHAT_KEYWORDS = ("你好", "您好", "你是谁", "你是什么", "谢谢", "感谢", "再见", "hello", "hi")
CATALOG_KEYWORDS = (
    "有什么论文", "有哪些论文", "几篇论文", "多少篇",
    "列举", "清单", "库里有什么", "库里有哪些",
)


def intent_router_node(state: LibraryQAState) -> dict:
    """把用户问题分为 chitchat / catalog / qa 三类。"""
    text = (state.get("input_text") or "").lower()
    if any(kw in text for kw in CHITCHAT_KEYWORDS):
        return {"intent": "chitchat"}
    if any(kw in text for kw in CATALOG_KEYWORDS):
        return {"intent": "catalog"}
    intro = "介绍" in text and ("论文库" in text or "库里" in text)
    if intro and not any(m in text for m in ("方法", "原理", "如何", "为什么", "技术")):
        return {"intent": "catalog"}
    return {"intent": "qa"}


async def chat_node(state: LibraryQAState, config: RunnableConfig = None) -> dict:
    """闲聊回复: 不检索, 直接自然对话。LLM 异常时回退到默认文案。"""
    default = "你好!我是论文知识问答助手, 可以问我论文库里的内容。"
    llm = _get_from_config(config, "llm")
    if llm is None:
        return {"content": default}
    try:
        prompt = build_chitchat_prompt(state.get("input_text", ""), state.get("history"))
        response = await llm.ainvoke(prompt)
        payload = _json_content(response.content if hasattr(response, "content") else response)
        content = str(payload.get("content", "")).strip()
        return {"content": content or default}
    except Exception as exc:
        logger.warning(f"chat_node LLM 调用失败, 使用兜底文案: {exc}")
        return {"content": default}


async def catalog_node(state: LibraryQAState, config: RunnableConfig = None) -> dict:
    """论文清单回复: 优先用 state.candidates, 为空则查全库(清单不需要 direction_select 的过滤)。

    图里 catalog 分支不经过 direction_select, candidates 恒为空, 必须自行加载全部论文,
    与旧 run_library_qa 的清单分支行为一致。LLM 异常时回退到默认文案。
    """
    llm = _get_from_config(config, "llm")
    session_factory = _get_from_config(config, "session_factory")
    papers = state.get("candidates", []) or []
    if not papers and session_factory is not None:
        try:
            async with session_factory() as session:
                papers = (await session.execute(select(Paper).order_by(Paper.created_at.desc()))).scalars().all()
        except Exception as exc:
            logger.warning(f"catalog_node 加载全库论文失败: {exc}")
            papers = []
    if not papers:
        return {"content": "论文库当前没有论文。"}
    default = "论文库当前有 %d 篇论文。" % len(papers)
    if llm is None:
        return {"content": default}
    try:
        prompt = build_library_catalog_prompt(state.get("input_text", ""), papers, state.get("history"))
        response = await llm.ainvoke(prompt)
        payload = _json_content(response.content if hasattr(response, "content") else response)
        content = str(payload.get("content", "")).strip()
        return {"content": content or default}
    except Exception as exc:
        logger.warning(f"catalog_node LLM 调用失败, 使用兜底文案: {exc}")
        return {"content": default}


# ============================================================
# 方向选择 / 检索
# ============================================================


async def direction_select_node(state: LibraryQAState, config: RunnableConfig = None) -> dict:
    """提取方向+归一化 -> 候选论文集(含降级)。与旧 run_library_qa 的 1/1b/2 步一致。"""
    session_factory = _get_from_config(config, "session_factory")
    llm = _get_from_config(config, "llm")
    input_text = state.get("input_text") or ""
    if session_factory is None:
        return {"candidates": [], "filters": {}}

    filters: dict = {}
    try:
        async with session_factory() as session:
            field_rows = (await session.execute(select(Paper.research_field).where(Paper.research_field != "").distinct())).scalars().all()
        available_fields = [str(f) for f in field_rows if f]
        if llm is not None:
            raw = await llm.ainvoke(build_filter_extraction_prompt(input_text, available_fields))
            filters = parse_filter_response(raw.content if hasattr(raw, "content") else raw)
    except Exception:
        filters = {}

    # 1b) 方向归一化: 用问题里的 2-4 字窗口匹配库内方向名, 命中则用标准方向名覆盖
    #     LLM 的输出(LLM 可能原样输出口语/缩写如"超分"→"超分辨率"), 不依赖 LLM 理解缩写。
    #     匹配规则: 只有"前缀或后缀唯一指向某个方向名"的连续片段才作为方向线索——
    #     "图像" 是 图像去雾/图像复原/图像编辑 的共享前缀, 属通用领域词, 不能误覆盖;
    #     "超分"(超分辨率前缀) / "去雾"(图像去雾后缀) 这类方向特异线索才生效。
    try:
        async with session_factory() as session:
            field_rows = (await session.execute(select(Paper.research_field).where(Paper.research_field != "").distinct())).scalars().all()
        field_names = [str(f) for f in field_rows if str(f)]
        # 优先匹配更长窗口(4字→3字→2字)
        matched = None
        for size in (4, 3, 2):
            for i in range(len(input_text) - size + 1):
                token = input_text[i:i+size]
                pref = [n for n in field_names if n.startswith(token)]
                suff = [n for n in field_names if n.endswith(token)]
                if len(pref) == 1:
                    matched = pref[0]
                    break
                if len(suff) == 1:
                    matched = suff[0]
                    break
            if matched:
                break
        if matched:
            filters["field"] = matched
    except Exception:
        pass

    # 2) 候选论文集(过滤条件可能由 LLM 提取, 允许降级)
    #    注: keywords 不参与 SQL 候选过滤——它们是检索词(交给向量/BM25 检索)
    def _stmt(cf: dict):
        stmt = select(Paper)
        conds = []
        if cf.get("field"):
            conds.append(Paper.research_field == cf["field"])
        if cf.get("year_min"):
            conds.append(Paper.publication_year >= cf["year_min"])
        if cf.get("year_max"):
            conds.append(Paper.publication_year <= cf["year_max"])
        if cf.get("authors"):
            conds.append(or_(*[Paper.authors_json.ilike(f"%{a}%") for a in cf["authors"]]))
        if cf.get("language"):
            conds.append(Paper.language == cf["language"])
        if conds:
            stmt = stmt.where(*conds)
        return stmt.order_by(Paper.created_at.desc())

    async def _fetch(cf: dict):
        # DB 异常节点内降级: 返回空候选, 不冒泡到 service(后续走 retrieve/generate 兜底)
        try:
            async with session_factory() as session:
                return (await session.execute(_stmt(cf))).scalars().all()
        except Exception as exc:
            logger.warning(f"direction_select 候选查询失败(filters={cf}), 返回空候选: {exc}")
            return []

    papers = await _fetch(filters)
    degraded: list[str] = []
    if not papers:
        # LLM 幻觉过滤可能把候选集滤空: 依次降级(去掉年份 → 去掉全部条件), 全库为空才返回空
        if filters.get("year_min") or filters.get("year_max"):
            relaxed = {**filters, "year_min": None, "year_max": None}
            rp = await _fetch(relaxed)
            if rp:
                papers, filters, degraded = rp, relaxed, ["year"]
        if not papers:
            ap = await _fetch({})
            if ap:
                papers, filters, degraded = ap, {}, ["all"]
    # 点名论文兜底: 问题点名了具体论文(标题缩写命中)时, 无论 field 过滤如何, 该论文必须进入候选集。
    # 与 relevance_check 规则1 同源: LLM 幻觉 field(如把 PGDUN 判成超分辨率)不能把目标论文挡在候选外。
    try:
        async with session_factory() as session:
            all_rows = (await session.execute(select(Paper))).scalars().all()
        named = _match_papers_by_title(input_text, all_rows)
        if named:
            existing_ids = {p.id for p in papers}
            for p in named:
                if p.id not in existing_ids:
                    papers = list(papers) + [p]
    except Exception:
        pass
    return {"candidates": list(papers), "filters": filters, "degraded": degraded}


# 论文标题通用词排除表: 高频非显著词不参与标题匹配
EXCLUDED = frozenset({
    "image", "lightweight", "network", "for", "the", "with",
    "attention", "transformer", "modulation", "super", "resolution",
    "deep", "guided", "unfolding", "prompt", "based", "multi",
    "window", "feature", "level", "fusion", "remote", "sensing",
    "collaborative", "heterogeneous", "expert", "mechanism",
    "learning", "approach", "method", "efficient", "novel", "scale",
    "toward", "using", "time",
})


def _title_initials(title: str) -> str:
    """论文标题单词首字母串(连字符词按部件拆), 用于问题侧缩写的标题匹配。

    例: "A lightweight multi-window attention transformer for image super-resolution"
    -> "almwatfisr" ("MWAT-SR" 的两个部件 mwat / sr 都能在其中命中)。
    """
    import re as _re
    out = []
    for w in _re.findall(r"[A-Za-z][A-Za-z0-9-]*", title or ""):
        parts = w.split("-") if "-" in w else [w]
        out.extend(p[0] for p in parts if p and p[0].isalpha())
    return "".join(out).lower()


def _match_papers_by_title(text: str, papers: list) -> list:
    """问题中的论文标题显著词(大写缩写/关键词)命中候选论文 -> 返回匹配论文; 否则空。

    连字符缩写(如 MWAT-SR)拆出部件(MWAT/SR), 支持空格分隔/只写缩写两种提问写法。
    另支持问题侧全大写连字符缩写(如 MWAT-SR)与标题单词首字母序列匹配,
    使 "MWAT-SR" 能命中标题为 "multi-window attention transformer for image super-resolution" 的论文。
    """
    import re as _re
    text_l = text.lower()
    # 问题侧全大写连字符缩写候选: 如 "MWAT-SR" -> ["mwat", "sr"]
    q_abbrs = []
    for tok in _re.findall(r"[A-Z][A-Z0-9-]*", text):
        if not tok.isupper() or "-" not in tok:
            continue
        parts = [p.lower() for p in tok.split("-") if p]
        if len(parts) >= 2 and all(len(p) >= 2 for p in parts):
            q_abbrs.append(parts)

    matched = []
    for p in papers:
        title = getattr(p, "title", "") or ""
        tokens = _re.findall(r"[A-Za-z][A-Za-z0-9-]*", title)
        parts = []
        for t in tokens:
            parts.append(t)
            if "-" in t and t.split("-")[0].isupper():
                parts.extend(t.split("-"))
        sig = [t for t in parts if len(t) >= 4 and t.lower() not in EXCLUDED]
        if any(tok.lower() in text_l for tok in sig):
            matched.append(p)
            continue
        # 问题侧缩写(如 MWAT-SR)的所有部件都命中标题单词首字母序列才认为匹配
        if q_abbrs:
            initials = _title_initials(title)
            if any(all(part in initials for part in abbr) for abbr in q_abbrs):
                matched.append(p)
    return matched


async def retrieve_node(state: LibraryQAState, config: RunnableConfig = None) -> dict:
    """按方向逐篇采样; 问题提到具体论文时, 只对匹配论文深挖。"""
    from app.paper.aggregate_retriever import AggregateRetriever
    retriever = _get_from_config(config, "retriever")
    papers = state.get("candidates", []) or []
    query = state.get("query") or state.get("input_text", "") or "论文综述"
    if retriever is None or not papers:
        return {"evidence": []}
    # 匹配用用户原问题 input_text: rewrite 会改写 query, 但单篇归属不应随改写丢失
    matched = _match_papers_by_title(state.get("input_text") or query, papers)
    agg = AggregateRetriever(retriever)
    if matched:
        # 单篇/对比: 只对匹配论文采样, 每篇 top-5 深挖
        evidence = await agg.sample_papers([p.id for p in matched], query, per_paper=5)
    else:
        evidence = await agg.sample_papers([p.id for p in papers], query)
    return {"evidence": evidence, "query": query, "matched_papers": [p.id for p in matched]}

# ============================================================
# 相关性评估 / 重试
# ============================================================

MAX_RETRIES = 3


async def relevance_evaluate_node(state: LibraryQAState, config: RunnableConfig = None) -> dict:
    """对证据 top-3 逐条 LLM 1-5 分评估(控制成本), 结合关键词过滤。
    评分供 should_retry 判定重试; 关键词过滤只保留含查询词的片段。
    """
    evidence = state.get("evidence", []) or []
    if not evidence:
        return {"relevance_scores": []}
    query = state.get("query") or state.get("input_text", "")
    llm = _get_from_config(config, "llm")

    # 仅 top-3 评分(用户确认的成本控制)
    top = evidence[:3]
    scores = []
    if llm is not None:
        for chunk in top:
            try:
                prompt = (
                    "评估以下论文片段与问题的相关性(1-5分), 只回复数字: "
                    "\n问题: " + query
                    + "\n论文: " + getattr(chunk, "section", "") + " p" + str(getattr(chunk, "page_start", ""))
                    + "\n片段: " + (chunk.content or "")[:500]
                )
                resp = await llm.ainvoke(prompt)
                t = (resp.content if hasattr(resp, "content") else str(resp)).strip()
                score = int("".join(c for c in t if c.isdigit()) or "3")
                scores.append({"score": max(1, min(5, score))})
            except Exception:
                scores.append({"score": 3})
    # 关键词过滤(中文 2+ 字词出现在片段中则保留); 无关键词时不过滤
    keywords = [w for w in _re.findall(r"[\u4e00-\u9fff]{2,}", query)]
    if keywords:
        filtered = [c for c in evidence if any(kw in (c.content or "") for kw in keywords)]
        if filtered:
            return {"evidence": filtered, "relevance_scores": scores}
    return {"relevance_scores": scores}


def should_retry(state: LibraryQAState) -> str:
    """返回 "retry" 或 "next"。平均分<2 且未超次数 -> 重试。"""
    if state.get("retry_count", 0) >= MAX_RETRIES:
        return "next"
    scores = state.get("relevance_scores", []) or []
    if not scores:
        return "next"
    avg = sum(s.get("score", 3) for s in scores) / len(scores)
    return "retry" if avg < 2 else "next"


async def rewrite_query_node(state: LibraryQAState, config: RunnableConfig = None) -> dict:
    """重试: LLM 用不同关键词改写查询, retry_count+1。LLM 异常时保留原查询。"""
    llm = _get_from_config(config, "llm")
    current = state.get("query") or state.get("input_text", "")
    if llm is None:
        return {"query": current, "retry_count": state.get("retry_count", 0) + 1}
    try:
        prompt = (
            "上一次检索未找到相关论文内容。请用完全不同的关键词和角度重新表述以下问题, "
            "提取核心概念。只输出改写后的查询。\n原始查询: " + current
        )
        resp = await llm.ainvoke(prompt)
        rewritten = (resp.content if hasattr(resp, "content") else str(resp)).strip()
        return {"query": rewritten or current, "retry_count": state.get("retry_count", 0) + 1}
    except Exception as exc:
        logger.warning(f"rewrite_query_node LLM 调用失败, 保留原查询: {exc}")
        return {"query": current, "retry_count": state.get("retry_count", 0) + 1}


# ============================================================
# 生成 / 引用校验
# ============================================================


async def generate_node(state: LibraryQAState, config: RunnableConfig = None) -> dict:
    """聚合生成(带多轮历史)。返回 content + raw_citations。"""
    llm = _get_from_config(config, "llm")
    evidence = state.get("evidence", []) or []
    papers = state.get("candidates", []) or []
    if llm is None or not evidence:
        return {"content": "未能在论文库中找到充分证据回答该问题。", "raw_citations": []}
    from app.paper.prompts import build_library_qa_prompt

    try:
        prompt = build_library_qa_prompt(state.get("input_text", ""), papers, evidence, state.get("history"))
        response = await llm.ainvoke(prompt)
        payload = _json_content(response.content if hasattr(response, "content") else response)
        content = str(payload.get("content", "")).strip()
        if not content:
            content = "未能在论文库中找到充分证据回答该问题。"
        raw = payload.get("citations", []) if isinstance(payload.get("citations"), list) else []
        return {"content": content, "raw_citations": raw}
    except Exception as exc:
        logger.warning(f"generate_node LLM 调用失败, 使用兜底文案: {exc}")
        return {"content": "未能在论文库中找到充分证据回答该问题。", "raw_citations": []}


def cite_verify_node(state: LibraryQAState) -> dict:
    """引用形状校验: paper_id 在候选集, chunk_id 在证据集。"""
    papers = state.get("candidates", []) or []
    evidence = state.get("evidence", []) or []
    valid_paper_ids = {p.id for p in papers}
    valid_chunk_ids = {getattr(c, "chunk_id", None) for c in evidence}
    citations = []
    for item in state.get("raw_citations", []) or []:
        if not isinstance(item, dict):
            continue
        pid = item.get("paper_id")
        cid = item.get("chunk_id")
        if not isinstance(pid, int) or pid not in valid_paper_ids:
            continue
        if cid and (not isinstance(cid, str) or cid not in valid_chunk_ids):
            continue
        citations.append({
            "paper_id": pid,
            "paper_title": item.get("paper_title", ""),
            "page": item.get("page"),
            "section": item.get("section", ""),
            "chunk_id": cid or "",
            "quote": str(item.get("quote", ""))[:240],
            "verified": True,
            "reason": "library_qa",
        })
    return {"citations": citations}


# ============================================================
# 相关性判定(问题是否需要检索论文库) / 通用问答
# ============================================================

PAPER_TERMS = (
    "超分辨率", "超分", "去雾", "去噪", "复原", "图像修复", "低光", "光谱",
    "重建", "注意力", "transformer", "轻量", "损失函数", "psnr", "ssim",
)


async def relevance_check_node(state: LibraryQAState, config: RunnableConfig = None) -> dict:
    """判定问题是否需要检索论文库: 规则粗判 + LLM 兜底。
    返回 {"intent_route": "rag" | "general"}。
    """
    import re as _re
    text = (state.get("input_text") or "").lower()

    # 规则粗判 1: 全库论文标题显著词(大写缩写如 PGDUN)。
    # 本节点在 direction_select 之前运行, state.candidates 恒为空, 必须直接查库取标题;
    # session_factory 为 None 时跳过规则 1(规则 2 与 LLM 兜底仍可用)。
    session_factory = _get_from_config(config, "session_factory")
    title_tokens: set[str] = set()
    if session_factory is not None:
        try:
            async with session_factory() as session:
                titles = (await session.execute(select(Paper.title))).scalars().all()
            for t in titles:
                for tok in _re.findall(r"[A-Z]{2,}", t or ""):
                    if len(tok) >= 3:
                        title_tokens.add(tok.lower())
        except Exception as exc:
            logger.warning(f"relevance_check 加载论文标题失败, 跳过规则1: {exc}")
    if any(tok in text for tok in title_tokens):
        return {"intent_route": "rag"}

    # 规则粗判 2: 方向词/论文术语
    if any(kw in text for kw in PAPER_TERMS):
        return {"intent_route": "rag"}

    # LLM 兜底判定
    llm = _get_from_config(config, "llm")
    if llm is None:
        return {"intent_route": "rag"}  # 无 LLM 时保守走 RAG
    from app.paper.prompts import build_relevance_prompt
    try:
        resp = await llm.ainvoke(build_relevance_prompt(state.get("input_text", "")))
        out = (resp.content if hasattr(resp, "content") else str(resp)).strip().lower()
        return {"intent_route": "rag" if (not out or "true" in out) else "general"}
    except Exception as exc:
        logger.warning(f"relevance_check LLM 判定失败, 保守走 RAG: {exc}")
        return {"intent_route": "rag"}


async def general_chat_node(state: LibraryQAState, config: RunnableConfig = None) -> dict:
    """通用问答: 不检索, LLM 自由回答(常识/无关话题)。"""
    llm = _get_from_config(config, "llm")
    default = "我是论文知识问答助手, 可以问我论文库里的内容, 也可以聊通用问题。"
    if llm is None:
        return {"content": default}
    try:
        from app.paper.prompts import build_general_chat_prompt
        prompt = build_general_chat_prompt(state.get("input_text", ""), state.get("history"))
        response = await llm.ainvoke(prompt)
        payload = _json_content(response.content if hasattr(response, "content") else response)
        content = str(payload.get("content", "")).strip()
        return {"content": content or default}
    except Exception as exc:
        logger.warning(f"general_chat_node LLM 调用失败, 使用兜底: {exc}")
        return {"content": default}

