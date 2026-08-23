"""图表审查 agent: 检查并修正图表 caption 与重复项。

问题类型:
1. caption 文字粘连(如 "ComparisonoftheproposedPGMCandSOTA") — PDF 文本层无空格,
   启发式 _restore_caption_spacing 只能拆数字边界, 纯字母粘连需 LLM 语义补空格;
2. 同页同编号重复(同一张图被 MinerU 拆成两块, caption 一个有空格一个没有);
3. 伪图表(作者人像/期刊 logo — 已在检测阶段过滤空 caption)。

两层审查:
- 规则层(确定性): 标记粘连(字母连写>18)、同页同编号重复、退化区域;
- LLM 层(批量): 输入可疑 caption 列表, 输出修正后的 caption(补空格)
  与重复保留建议。失败回退规则层(不阻断)。
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable


# 字母连写 >18 视为粘连(正常英文单词极少超过 18 字母)
_GLUED = re.compile(r"[a-zA-Z]{19,}")
# 图注编号
_FIG_NUM = re.compile(r"^(?:fig(?:ure)?\.?|table)\s*(\d+[a-z]?|[IVXLCDM]+)", re.IGNORECASE)


@dataclass
class FigureAuditResult:
    fixed_captions: dict[int, str] = field(default_factory=dict)  # {index: 修正后 caption}
    drop_indexes: set[int] = field(default_factory=set)  # 应删除的重复项索引
    issues: list[str] = field(default_factory=list)
    by_llm: bool = False


def _rule_flags(
    captions: list[tuple[int, str]],  # (index, caption)
) -> FigureAuditResult:
    """规则层: 标记粘连与同页同编号重复。"""
    res = FigureAuditResult()
    # 粘连
    for idx, cap in captions:
        if _GLUED.search(cap or ""):
            res.issues.append(f"#{idx} caption 粘连: {cap[:50]}")
    # 同页同编号重复: 输入带 page 信息, 这里由调用方提供 (index, page, caption)
    return res


def _dedup_same_number(
    items: list[tuple[int, int, str, str]],  # (index, page, kind, caption)
) -> set[int]:
    """同页同编号重复: 保留 caption 带空格/更长的一条, 其余标记删除。"""
    from collections import defaultdict

    groups: dict[tuple[int, str], list[tuple[int, str, str]]] = defaultdict(list)
    for idx, page, kind, cap in items:
        m = _FIG_NUM.match(cap or "")
        if m:
            groups[(page, kind, m.group(1).lower())].append((idx, kind, cap))
    drop: set[int] = set()
    for (page, _kind, num), members in groups.items():
        if len(members) <= 1:
            continue
        # 保留标准: 带空格优先, 其次更长
        def score(m: tuple[int, str, str]) -> tuple[int, int]:
            _i, _k, c = m
            has_space = c.count(" ")
            return (has_space, len(c))
        members.sort(key=score, reverse=True)
        for idx, _k, _c in members[1:]:
            drop.add(idx)
    return drop


_AUDIT_PROMPT = """你是论文图表审查员。下面是从论文提取的图表 caption 列表。
问题: 部分 caption 的英文单词之间空格丢失(如 "ComparisonoftheproposedPGMCandSOTA"
应为 "Comparison of the proposed PGMC and SOTA"), 因为 PDF 文本层没有空格。

请输出**修正后的 caption 数组**, 每个元素对应输入顺序, 格式:
{"index": 原编号, "caption": "修正后的 caption(单词间补空格)"}

规则:
- 只补空格, 不要改动其他文字/标点/编号;
- 保持模型名(如 "PGMC", "SOTA", "GFPose", "BART")完整;
- 若某条 caption 本身正常(已有空格), 原样返回;
- 只输出 JSON 数组, 不要解释。

caption 列表:
{captions_json}
"""


async def audit_figures_with_llm(
    llm_ainvoke: Callable[[str], Any],
    items: list[tuple[int, int, str, str]],  # (index, page, kind, caption)
    max_batch: int = 40,
) -> FigureAuditResult:
    """LLM 审查图表: 修正粘连 caption, 标记重复。失败回退规则层。"""
    res = FigureAuditResult()
    # 规则层: 重复检测(确定性, 不依赖 LLM)
    res.drop_indexes = _dedup_same_number(items)
    for idx in sorted(res.drop_indexes):
        cap = dict((i, c) for i, _p, _k, c in items)[idx]
        res.issues.append(f"#{idx} 同页同编号重复, 建议删除: {cap[:45]}")
    # 找粘连项
    glued_idx = [i for i, _p, _k, c in items if _GLUED.search(c or "")]
    if not glued_idx:
        return res
    # 分批调 LLM
    for start in range(0, len(glued_idx), max_batch):
        batch = glued_idx[start : start + max_batch]
        payload = [{"index": i, "caption": dict((i, c) for i, _p, _k, c in items)[i]} for i in batch]
        try:
            raw = await llm_ainvoke(_AUDIT_PROMPT.replace("{captions_json}", json.dumps(payload, ensure_ascii=False)))
            fixed = _parse_llm_captions(raw)
            if fixed:
                for item in fixed:
                    idx = item.get("index")
                    cap = item.get("caption")
                    if idx is not None and cap:
                        res.fixed_captions[int(idx)] = str(cap).strip()
                res.by_llm = True
        except Exception:
            continue
    # 被规则层标记删除的项无需修 caption(反正不保留)
    for idx in list(res.fixed_captions):
        if idx in res.drop_indexes:
            del res.fixed_captions[idx]
    return res


def _parse_llm_captions(raw: str) -> list[dict[str, Any]] | None:
    if not raw:
        return None
    text = raw.strip()
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
    out = []
    for item in data:
        if isinstance(item, dict) and item.get("index") is not None and item.get("caption"):
            out.append({"index": item["index"], "caption": str(item["caption"]).strip()})
    return out or None
