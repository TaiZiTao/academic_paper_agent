"""章节树审查 agent: 在解析后自动检查并修正异常的章节标题。

两层审查:
- 规则层(确定性, 零成本): 正文句特征("N. We can find..."), 编号跳变,
  标题拆行(上一标题被截断成两行), 超长标题;
- LLM 层(精准兜底): 把章节树 + 规则标记 + 可疑标题的上下文喂给 LLM,
  输出修正后的章节树 JSON(丢弃伪标题、补全被截断的标题、修正层级/编号)。

LLM 审查失败或超时不阻断主流程: 回退原章节树(或仅应用规则层修正)。
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from app.paper.schemas import PaperPage, PaperSectionData


# 正文句特征: 编号后跟句子结构词/动词开头的长句(不是章节标题)
_SENTENCE_START = re.compile(
    r"^(\d+(?:\.\d+)*)[.\s]+(We|This|The|These|Those|In|For|Since|However|Moreover|"
    r"Specifically|Notably|Given|As|Compared|Figure|Table|Our|Their|Its|A|An)\s",
    re.IGNORECASE,
)
# 标题被拆行的特征: 上一标题短(<= 60 字符)且不以常见结尾符结尾, 下一行是孤立的短词


@dataclass
class SectionAuditResult:
    sections: list[PaperSectionData]
    dropped: list[str] = field(default_factory=list)  # 被判定为伪标题丢弃的
    fixed: list[tuple[str, str]] = field(default_factory=list)  # (原, 修正后)
    by_llm: bool = False


def _rule_flags(sections: list[PaperSectionData]) -> tuple[list[int], list[tuple[int, str]]]:
    """规则层: 返回 (应丢弃的索引, [(索引, 原因), ...])。"""
    drop: list[int] = []
    issues: list[tuple[int, str]] = []
    prev: PaperSectionData | None = None
    for idx, sec in enumerate(sections):
        title = (sec.title or "").strip()
        # 1) 正文句特征: 编号 + 句子结构词开头的长句(> 6 词)是正文不是标题
        m = _SENTENCE_START.match(title)
        if m and len(title.split()) > 6:
            drop.append(idx)
            issues.append((idx, f"正文句被当标题: {title[:50]}"))
            continue
        # 2) 明显伪标题: 无编号且是完整句(以句号结尾且 > 10 词)
        if (
            not re.match(r"^(?:\d+|[IVXLC]+|[A-Z])\.?\s", title, re.I)
            and title.endswith(".")
            and len(title.split()) > 10
        ):
            drop.append(idx)
            issues.append((idx, f"无编号完整句被当标题: {title[:50]}"))
            continue
        prev = sec
    # 触发 LLM 的信号(不改动章节, 仅决定是否值得调 LLM 复核):
    # 同页连续短标题(可能是标题拆行的两行)或一级标题编号跳变
    for i in range(1, len(sections)):
        a, b = sections[i - 1], sections[i]
        ta, tb = (a.title or "").strip(), (b.title or "").strip()
        if (
            a.page_start == b.page_start
            and 0 < len(ta) < 60
            and 0 < len(tb) <= 12
            and not ta.rstrip().endswith((".", ":", ";"))
            # 前一行不是完整标题(不以句号/冒号结尾), 本行是孤立短行 →
            # 很可能是标题拆行("...Less Meets" + "More!"), 触发 LLM 复核
            and re.match(r"^[A-Za-z]", tb)
        ):
            issues.append((i, f"疑似标题拆行: {ta!r} + {tb!r}"))
        # 一级标题编号跳变: 1. -> 4. -> 6.
        if a.level == 1 and b.level == 1:
            na = re.match(r"^(\d+)(?:\.|\s)", ta)
            nb = re.match(r"^(\d+)(?:\.|\s)", tb)
            if na and nb:
                gap = int(nb.group(1)) - int(na.group(1))
                if gap > 1:
                    issues.append((i, f"编号跳变: {ta!r} -> {tb!r}"))
    return drop, issues


_AUDIT_PROMPT = """你是论文解析系统的章节树审查员。
下面是解析器从一篇论文提取的章节树(标题/层级/页码)。解析器偶尔会犯错:
1. 把正文句子当成章节标题(如 "4. We can find that GFPose consistently outperforms...");
2. 标题文字粘连(如 "Pre-trainingBART" 应为 "Pre-training BART");
3. 标题被拆成两行(如 "Less Meets" + "More!" 实际是同一标题的两行)。

请输出**修正后的完整章节树 JSON 数组**, 每项格式:
{"title": "修正后的标题", "level": 保持原值, "page_start": 保持原值, "page_end": 保持原值}

**严格规则(必须遵守):**
- **保持原有顺序**, 不要重排、不要增删章节(除非是明显伪标题);
- **保持原编号和层级**, 不要重新编号(如不要把 3.1 改成 3 Experiments);
- 每个标题的 level/page_start/page_end **必须与输入完全一致**, 不许改动;
- 只允许两种修正: (a) 删除明显是正文句子的项; (b) 修复标题文字粘连(补空格);
- 若标题明显被拆成两行(前一标题短且后一标题是孤立短词), 可合并, 页码取第一行;
- 不确定时**保持原样**, 不要臆造章节名;
- 只输出 JSON 数组, 不要任何解释。

章节树:
{sections_json}
"""



async def audit_sections_with_llm(
    llm_ainvoke: Callable[[str], Any],
    sections: list[PaperSectionData],
) -> SectionAuditResult:
    """LLM 审查章节树: 输入章节树, 输出修正后的树。失败回退原样。"""
    # 先跑规则层(确定性, 零成本)
    drop_idx, issues = _rule_flags(sections)
    rule_kept = [s for i, s in enumerate(sections) if i not in drop_idx]
    # 规则层已无问题则直接返回(避免无谓的 LLM 调用)
    if not issues or not rule_kept:
        return SectionAuditResult(
            sections=rule_kept or sections,
            dropped=[sections[i].title for i in drop_idx],
        )
    payload = [
        {"title": s.title, "level": s.level, "page_start": s.page_start, "page_end": s.page_end}
        for s in rule_kept
    ]
    try:
        raw = await llm_ainvoke(_AUDIT_PROMPT.replace("{sections_json}", json.dumps(payload, ensure_ascii=False)))
        fixed = _parse_llm_sections(raw)
        if not fixed:
            return SectionAuditResult(
                sections=rule_kept,
                dropped=[sections[i].title for i in drop_idx],
            )
        result = [
            PaperSectionData(
                title=item["title"],
                normalized_title=_normalize(item["title"]),
                level=int(item.get("level", 1)),
                ordinal=idx,
                page_start=int(item.get("page_start", 1)),
                page_end=int(item.get("page_end", 1)),
                summary="",
            )
            for idx, item in enumerate(fixed)
        ]
        return SectionAuditResult(
            sections=result,
            dropped=[sections[i].title for i in drop_idx],
            fixed=[(s.title, r.title) for s, r in zip(rule_kept, result) if s.title != r.title][:10],
            by_llm=True,
        )
    except Exception:
        # LLM 失败回退规则层结果
        return SectionAuditResult(
            sections=rule_kept,
            dropped=[sections[i].title for i in drop_idx],
        )


def _parse_llm_sections(raw: str) -> list[dict[str, Any]] | None:
    """解析 LLM 输出的章节 JSON 数组; 失败返回 None。"""
    if not raw:
        return None
    text = raw.strip()
    # 去掉可能的 ```json 围栏
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        out.append({
            "title": str(item["title"]).strip(),
            "level": int(item.get("level", 1) or 1),
            "page_start": int(item.get("page_start", 1) or 1),
            "page_end": int(item.get("page_end", 1) or 1),
        })
    return out or None


def _normalize(title: str) -> str:
    t = re.sub(r"^\d+(?:\.\d+)*[.、]?\s*", "", (title or "").strip(), flags=re.I)
    t = re.sub(r"^[IVXLC]+[.、]\s*", "", t, flags=re.I)
    t = re.sub(r"^[A-Z][.、]\s*", "", t)
    return (t.strip() or "other").lower().replace(" ", "_")
