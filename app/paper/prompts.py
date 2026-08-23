"""论文精读与按需任务提示词。"""

import json

from app.paper.schemas import PaperChunkData


REPORT_FIELDS = (
    "background",
    "motivation",
    "existing_problems",
    "solution",
    "contributions",
    "terms",
)

REPORT_EVIDENCE_BUDGET = 36_000


def _evidence_block(chunk: PaperChunkData, content: str | None = None) -> str:
    return (
        f"[chunk_id={chunk.chunk_id}; page={chunk.page_start}; section={chunk.section}]\n"
        f"{chunk.content if content is None else content}"
    )


def _build_bounded_evidence(chunks: list[PaperChunkData], max_chars: int) -> str:
    """按页均匀抽取首尾证据，避免整篇论文形成超大单次请求。"""
    if not chunks:
        return ""

    pages: dict[int, list[PaperChunkData]] = {}
    for chunk in sorted(chunks, key=lambda item: (item.page_start, item.ordinal)):
        pages.setdefault(chunk.page_start, []).append(chunk)

    representatives: list[PaperChunkData] = []
    for page_chunks in pages.values():
        representatives.append(page_chunks[0])
        if page_chunks[-1].chunk_id != page_chunks[0].chunk_id:
            representatives.append(page_chunks[-1])

    header_allowance = 96
    excerpt_size = max(240, max_chars // len(representatives) - header_allowance)
    blocks: list[str] = []
    used = 0
    selected_ids: set[str] = set()
    for chunk in representatives:
        block = _evidence_block(chunk, chunk.content[:excerpt_size])
        remaining = max_chars - used
        if remaining <= header_allowance:
            break
        if len(block) > remaining:
            header = _evidence_block(chunk, "")
            block = _evidence_block(chunk, chunk.content[: max(0, remaining - len(header))])
        blocks.append(block)
        used += len(block) + 2
        selected_ids.add(chunk.chunk_id)

    # 小论文或短页面仍有预算时，按原文顺序补入未选中的完整分块。
    for chunk in chunks:
        if chunk.chunk_id in selected_ids:
            continue
        block = _evidence_block(chunk)
        if used + len(block) + 2 > max_chars:
            continue
        blocks.append(block)
        used += len(block) + 2

    return "\n\n".join(blocks)


def build_report_prompt(
    paper_title: str,
    metadata: dict,
    sections: list[dict],
    chunks: list[PaperChunkData],
    validation_errors: list[str] | None = None,
) -> str:
    evidence = _build_bounded_evidence(chunks, REPORT_EVIDENCE_BUDGET)
    retry_note = ""
    if validation_errors:
        retry_note = (
            "\n上次输出存在以下引用错误，请只改用证据中真实存在的 chunk_id、页码和原文：\n- "
            + "\n- ".join(validation_errors)
        )
    schema = {
        "report": {field: ([] if field == "terms" else "中文内容") for field in REPORT_FIELDS},
        "citations": [
            {
                "paper_id": 1,
                "paper_title": paper_title,
                "page": 1,
                "section": "章节名",
                "chunk_id": "证据中的 chunk_id",
                "quote": "从证据逐字摘录的短句",
            }
        ],
    }
    return (
        "你是严谨的科研论文精读助手。请基于下方证据生成中文精读报告，关键英文术语保留。\n"
        "报告聚焦论文的来龙去脉与核心价值，按四部分组织：\n"
        "1. background(研究背景与方向)：该研究所在领域、当前发展脉络与整体方向；\n"
        "2. motivation(论文动机)：作者为什么做这件事，要解决的核心矛盾或缺口；\n"
        "3. existing_problems(现有方法存在的问题)：已有方法/基线的主要不足(尽量引用原文)；\n"
        "4. solution(解决方案与创新点)：本文方法如何针对上述问题提出解决方案，与现有工作的本质区别、核心创新；\n"
        "5. contributions(论文主要贡献)：提取论文在引言/摘要中明确陈述的贡献点(通常为编号列表)，逐条用中文转述，尽量引用原文支撑。\n"
        "不得使用证据之外的信息，不得自行猜测页码。每个重要结论都应由 citations 中的原文支持。\n"
        "报告要有实质内容、避免空洞结论：每个文本字段写 300~600 个汉字，"
        "作为逻辑完整、有来龙去脉的段落(可用 Markdown 加粗与短列表组织)，"
        "讲清背景脉络、动机来源、问题依据与方案细节，重要论断尽量引用原文支撑；"
        "引用原文的规则：正文中只摘录简短的英文关键短语(不超过 15 个英文单词)，并在其后用中文解释其含义；"
        "较长的英文原文一律用中文转述其意思，不要把整句/整段英文照搬进正文；英文术语本身(如 window-based self-attention)可保留。"
        "排版规则：并列要点(如第一/第二/第三、多条问题或建议)必须用 Markdown 列表(`1. ` 或 `- `)逐条换行呈现，条目间用换行分隔，不要挤在同一段里；段落开头说明句(如\"现有方法主要存在三方面不足：\")单独一行。"
        "terms 最多 12 项，citations 控制在 6-10 条，每条 quote 不超过 120 个字符。\n"
        "只输出一个合法 JSON 对象，不要输出 Markdown 代码围栏。\n"
        f"输出结构：{json.dumps(schema, ensure_ascii=False)}\n"
        f"论文标题：{paper_title}\n"
        f"元信息：{json.dumps(metadata, ensure_ascii=False)}\n"
        f"章节：{json.dumps(sections, ensure_ascii=False)}\n"
        f"{retry_note}\n\n证据：\n{evidence}"
    )


def build_task_prompt(
    task_type: str,
    paper_title: str,
    input_text: str,
    chunks: list[PaperChunkData],
    history: list[dict] | None = None,
) -> str:
    instructions = {
        "qa": "回答用户问题，若证据不足请明确说明。回答末尾给出 2~3 个基于本次回答、值得继续追问的问题(每个不超过 25 字)。",
        "translation": "把所选章节准确翻译为中文，保留专业英文术语。",
        "presentation": "生成适合科研汇报的逐页提纲：每一页一个对象，包含标题(title)、要点(bullets)和演讲备注(notes)；页面数量与输入要求的时长匹配(约每分钟 1 页，最多不超过 12 页，每页要点 2~4 条)。",
        "review": "以审稿人视角审阅这篇论文并输出结构化审稿意见：概要、主要贡献、优点、主要问题、次要问题、分项评价、修改建议、推荐意见与评分。每条优点/问题尽量标注原文页码；评分 1~10。",
    }
    evidence = "\n\n".join(
        f"[chunk_id={chunk.chunk_id}; page={chunk.page_start}; section={chunk.section}]\n{chunk.content}"
        for chunk in chunks
    )
    if task_type == "review":
        schema = (
            "{\"summary\": \"论文概要\", \"contributions\": [\"主要贡献\"], "
            "\"strengths\": [\"优点（尽量标注页码）\"], \"major_issues\": [\"主要问题\"], "
            "\"minor_issues\": [\"次要问题\"], \"ratings\": {\"novelty\": \"创新性评价\", "
            "\"correctness\": \"技术正确性评价\", \"experiments\": \"实验充分性评价\", \"writing\": \"写作质量评价\"}, "
            "\"suggestions\": [\"修改建议\"], \"recommendation\": \"Accept|Minor Revision|Major Revision|Reject\", "
            "\"score\": 7, \"citations\": [{\"paper_id\": 1, \"paper_title\": \"标题\", \"page\": 1, "
            "\"section\": \"章节\", \"chunk_id\": \"证据ID\", \"quote\": \"原文短句\"}]}"
        )
    elif task_type == "presentation":
        schema = (
            "{\"slides\": [{\"title\": \"第1页标题\", \"bullets\": [\"要点1\", \"要点2\"], "
            "\"notes\": \"演讲备注\"}], \"citations\": [{\"paper_id\": 1, \"paper_title\": \"标题\", "
            "\"page\": 1, \"section\": \"章节\", \"chunk_id\": \"证据ID\", \"quote\": \"原文短句\"}]}"
        )
    elif task_type == "qa":
        schema = (
            "{\"content\": \"结果\", \"citations\": [{\"paper_id\": 1, \"paper_title\": \"标题\", "
            "\"page\": 1, \"section\": \"章节\", \"chunk_id\": \"证据ID\", \"quote\": \"原文短句\"}], "
            "\"suggestions\": [\"追问1\", \"追问2\"]}"
        )
    else:
        schema = (
            "{\"content\": \"结果\", \"citations\": [{\"paper_id\": 1, \"paper_title\": \"标题\", "
            "\"page\": 1, \"section\": \"章节\", \"chunk_id\": \"证据ID\", \"quote\": \"原文短句\"}]}"
        )
    return (
        "你是严谨的科研论文助手。"
        + instructions[task_type]
        + "\n只输出 JSON：" + schema + "。"
        + "不得编造 chunk_id、quote 或页码。\n"
        + f"论文：{paper_title}\n任务输入：{input_text}\n"
        + f"历史：{json.dumps(history or [], ensure_ascii=False)}\n证据：\n{evidence}"
    )


def build_translation_prompt(
    paper_title: str,
    section: str,
    page_start: int,
    page_end: int,
    source_text: str,
) -> str:
    page_label = str(page_start) if page_start == page_end else f"{page_start}-{page_end}"
    return (
        "你是严谨的科研论文翻译助手。请把下面原文完整翻译为简体中文。\n"
        "要求：不总结、不删减、不扩写；保留公式、变量、引用编号和通用英文缩写；"
        "不要翻译图片、表格、图注、表注及其内部文字，只保留正文和公式；"
        "公式统一使用 LaTeX 表示，禁止使用 <sub> 或 <sup> HTML 标签；"
        "专业术语首次出现时可写成中文（English）；只返回中文译文，不要 JSON、标题或解释。\n"
        f"论文：{paper_title}\n章节：{section}\n来源页码：{page_label}\n原文：\n{source_text}"
    )


def build_caption_prompt(caption: str) -> str:
    """图表标题翻译提示词:只翻译标题,保留编号。"""
    return (
        "你是严谨的科研论文翻译助手。请把下面的论文图表标题翻译成简体中文。\n"
        "要求：保留编号(如 Fig. 2、TABLE I、图 2)；只返回译文本身,不要额外解释。\n\n"
        f"原文标题：\n{caption}\n\n中文译文："
    )


# 研究方向预置列表(仅作 LLM 参考与前端分组默认顺序, 不是硬限制)
DEFAULT_FIELDS = [
    "超分辨率",
    "图像去雾",
    "图像去噪",
    "图像修复",
    "低光增强",
    "图像复原",
    "目标检测",
    "图像分割",
    "图像生成",
    "视觉Transformer",
    "多模态",
    "医学影像",
    "遥感影像",
    "OCR文档",
    "NLP大模型",
    "其他",
]


def build_field_classification_prompt(
    title: str, abstract: str, keywords: list[str]
) -> str:
    """研究方向自动分类提示词: 根据标题/摘要/关键词推断一个研究方向。

    允许输出预置列表之外的新方向(方向列表动态增长); 判不出时输出"其他"。
    """
    field_hint = "、".join(DEFAULT_FIELDS)
    return (
        "你是科研论文信息整理助手。请判断下面这篇论文属于哪个研究方向。\n"
        f"常见方向参考(可输出这些, 也可输出更具体的新方向, 如\"图像去雨\"): {field_hint}\n"
        "要求：只输出一个简短的中文研究方向短语(2-8 字), 不要解释、不要标点、不要 JSON。\n"
        "如果从标题/摘要/关键词完全判断不出方向, 输出\"其他\"。\n\n"
        f"标题：{title}\n"
        f"关键词：{', '.join(keywords) if keywords else '无'}\n"
        f"摘要：{(abstract or '')[:800]}\n\n研究方向："
    )


def parse_field_response(text: str) -> str:
    """解析分类返回: 取首行/去引号/去标点, 空则"其他"。"""
    import re

    cleaned = (text or "").strip()
    # 去掉可能的 JSON 包裹或引号
    cleaned = re.sub(r"^[\"'{\[]+|[\"'}\]]+$", "", cleaned.strip())
    line = cleaned.splitlines()[0].strip() if cleaned else ""
    line = re.sub(r"[。.!！,，、\s]+$", "", line)
    return line or "其他"


FILTER_SCHEMA = (
    '{"field": "研究方向或留空", "year_min": null, "year_max": null, '
    '"authors": ["作者名"], "keywords": ["关键词"], "language": "en|zh|留空"}'
)


def build_filter_extraction_prompt(question: str, available_fields: list[str] | None = None) -> str:
    field_guidance = (
        "2) 研究方向类问题(如『超分辨率论文』)优先填写 field, 不要只放 keywords; "
        "field 必须是以下库内已有方向之一(用户用口语/缩写时请归一化到最接近的标准名, 如『超分』→『超分辨率』、『去雾』→『图像去雾』): "
        + ("、".join(available_fields) if available_fields else "无")
        + "; "
    )
    return (
        "你是论文检索助手。从用户问题中提取检索过滤条件。只输出 JSON: "
        + FILTER_SCHEMA
        + '. 规则: 1) 只有用户问题明确提到年份(如"2024年""近三年""近两年")时才填写 '
        + "year_min/year_max, 未提到年份时两者必须为 null; "
        + field_guidance
        + "3) keywords 填论文标题/关键词中可能出现的词; 提取不到的字段留空/null。\n用户问题: " + question
    )


def parse_filter_response(text: str) -> dict:
    import re
    m = re.search(r"\{[^{}]*\}", text or "")
    if not m:
        return {"field": "", "year_min": None, "year_max": None, "authors": [], "keywords": [], "language": ""}
    try:
        data = json.loads(m.group(0))
    except Exception:
        return {"field": "", "year_min": None, "year_max": None, "authors": [], "keywords": [], "language": ""}

    def _norm_year(v):
        if isinstance(v, str) and v.isdigit():
            v = int(v)
        return v if isinstance(v, int) and 1900 <= v <= 2100 else None

    def _norm_list(v):
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, list):
            return []
        return [str(x).strip() for x in v if x is not None and str(x).strip()]

    return {
        "field": str(data.get("field") or "").strip(),
        "year_min": _norm_year(data.get("year_min")),
        "year_max": _norm_year(data.get("year_max")),
        "authors": _norm_list(data.get("authors")),
        "keywords": _norm_list(data.get("keywords")),
        "language": str(data.get("language") or "").strip(),
    }

def build_library_qa_prompt(question: str, papers, evidence, history: list[dict] | None = None) -> str:
    """全库问答聚合 prompt: 证据带论文标题/章节/页码, 要求覆盖多篇论文。"""
    import json as _json
    paper_titles = {p.id: p.title for p in papers}
    paper_lines = "\n".join(f"- {p.title}" for p in papers)
    evidence_block = "\n\n".join(
        f"[{chunk.section}; p{chunk.page_start}; {paper_titles.get(chunk.paper_id, chunk.paper_id)}]\n{chunk.content}"
        for chunk in evidence
    )
    history_block = _json.dumps(history or [], ensure_ascii=False)
    return (
        "你是严谨的科研论文综述助手。基于以下多篇论文的证据回答用户问题。"
        "回答要覆盖尽量多的论文, 引用时给出论文标题+章节+页码。"
        "若证据不足请说明。只输出 JSON: {\"content\": \"回答\", "
        "\"citations\": [{\"paper_id\": 1, \"paper_title\": \"标题\", \"page\": 1, "
        "\"section\": \"章节\", \"chunk_id\": \"ID\", \"quote\": \"原文短句\"}]}. "
        "\n\n对话历史(供理解追问, 不要重复已回答内容):\n" + history_block
        + "\n\n论文清单:\n" + paper_lines + "\n\n证据:\n" + evidence_block
    )


def build_library_catalog_prompt(question: str, papers, history: list[dict] | None = None) -> str:
    """库清单 prompt: 只给论文元数据(标题/方向/年份), 不检索证据。"""
    import json as _json
    lines = []
    for p in papers:
        year = getattr(p, "publication_year", None) or "未知"
        field = getattr(p, "research_field", "") or "未分类"
        lines.append(f"- {p.title} (方向: {field}, 发表年份: {year})")
    history_block = _json.dumps(history or [], ensure_ascii=False)
    return (
        "你是论文库助手。用户问的是论文库的清单/数量/介绍类问题。"
        "直接基于以下论文元数据回答(列标题、研究方向、发表年份), 不要编造。"
        "若用户问『有什么/哪些论文』等清单问题, 必须逐条列出每篇论文的标题(每篇一行), 不要只做统计汇总; "
        "若用户问数量, 再额外给出总数。只输出 JSON: {\"content\": \"回答\", \"citations\": []}. "
        "\n\n对话历史(供理解追问):\n" + history_block
        + "\n\n论文清单:\n" + "\n".join(lines)
    )


def build_chitchat_prompt(question: str, history: list[dict] | None = None) -> str:
    """闲聊/寒暄 prompt: 不检索论文, 直接自然对话(你是论文问答助手)。"""
    import json as _json
    history_block = _json.dumps(history or [], ensure_ascii=False)
    return (
        "你是论文知识问答助手, 帮助用户解答论文库相关内容。"
        "用户现在说的是寒暄/闲聊/身份类话语, 请自然友好地回应, 并引导回论文问答主题。"
        "不要编造论文内容。只输出 JSON: {\"content\": \"回应\", \"citations\": []}. "
        "\n\n对话历史:\n" + history_block
        + "\n\n用户话语: " + question
    )


def build_relevance_prompt(question: str) -> str:
    """判定问题是否需要检索论文库。只输出 true 或 false。"""
    return (
        "你是问答助手。判断以下问题是否需要检索论文库(论文库包含图像超分辨率/去雾/复原等方向的学术论文)。"
        "需要检索(问论文内容/方法/对比/某篇论文) -> 输出 true; "
        "不需要(常识/闲聊/无关话题/纯技术原理不涉及库内论文) -> 输出 false。"
        "\n问题: " + question + "\n只输出 true 或 false。"
    )


def build_general_chat_prompt(question: str, history: list[dict] | None = None) -> str:
    """通用问答 prompt: 不检索论文, LLM 自由回答。"""
    import json as _json
    history_block = _json.dumps(history or [], ensure_ascii=False)
    return (
        "你是知识问答助手, 擅长图像超分辨率/去雾/复原等方向, 也可回答通用问题。"
        "直接回答用户问题, 不要编造论文库内容。只输出 JSON: {\"content\": \"回答\", \"citations\": []}. "
        "\n\n对话历史:\n" + history_block + "\n\n用户问题: " + question
    )
