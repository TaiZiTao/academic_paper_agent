"""以 pdfplumber 页面文本为页码真值的论文解析器。"""

import difflib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pdfplumber

from app.paper.schemas import PaperMetadata, PaperPage, PaperSectionData, ParsedPaper


class UnsupportedScanError(ValueError):
    code = "unsupported_scan"


_SECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("abstract", re.compile(r"^(?:abstract|摘要)\s*$", re.I)),
    ("introduction", re.compile(r"^(?:\d+(?:\.\d+)*[.、]?\s*)?(?:introduction|引言|绪论|研究背景)\s*$", re.I)),
    ("related_work", re.compile(r"^(?:\d+(?:\.\d+)*[.、]?\s*)?(?:related\s+work|文献综述|相关工作)\s*$", re.I)),
    # 注意: 部分期刊模板提取文本会把紧排标题连写("ProposedMethod"、"ExperimentalResults"),
    # 因此各模式允许 0 个空格(用 \s* 而非 \s+)
    ("method", re.compile(r"^(?:\d+(?:\.\d+)*[.、]?\s*)?(?:proposed\s*)?(?:method(?:ology)?s?|approach|model|方法|研究方法|模型)\s*$", re.I)),
    ("experiments", re.compile(r"^(?:\d+(?:\.\d+)*[.、]?\s*)?(?:experiments?|experimental\s*results?|evaluation|实验|实验结果|结果与分析)\s*$", re.I)),
    ("results", re.compile(r"^(?:\d+(?:\.\d+)*[.、]?\s*)?(?:results?|findings|结果)\s*$", re.I)),
    ("discussion", re.compile(r"^(?:\d+(?:\.\d+)*[.、]?\s*)?(?:discussion|讨论)\s*$", re.I)),
    ("conclusion", re.compile(r"^(?:\d+(?:\.\d+)*[.、]?\s*)?(?:conclusions?|总结|结论|结论与展望)\s*$", re.I)),
    ("references", re.compile(r"^(?:references|bibliography|参考文献)\s*$", re.I)),
]

_ROMAN_HEADING = re.compile(r"^(?P<number>[IVXLC]+)[.、]\s+(?P<title>.+)$")
_DECIMAL_HEADING = re.compile(r"^(?P<number>\d+(?:\.\d+)*)[.、]?\s+(?P<title>.+)$")
_LETTER_HEADING = re.compile(r"^(?P<number>[A-Z])[.、]\s+(?P<title>.+)$")
_TOP_LEVEL_IN_LINE = re.compile(
    r"\b(?P<number>[IVXLC]+)[.、]\s*(?P<title>"
    r"INTRODUCTION|RELATED\s*WORKS?|METHOD(?:OLOGY)?S?|"
    r"EXPERIMENTS?|EXPERIMENTAL\s+RESULTS?|CONCLUSIONS?|DISCUSSION)\b"
)
_ABSTRACT_PREFIX = re.compile(r"^\s*(?P<title>Abstract|摘要)(?:\s|[—–-])", re.I)
_REFERENCES_TOKEN = re.compile(r"(?P<title>REFERENCES|BIBLIOGRAPHY|参考文献)\s*$")
_KNOWN_TOP_LEVEL = {
    "abstract",
    "introduction",
    "related_work",
    "method",
    "experiments",
    "results",
    "discussion",
    "conclusion",
    "references",
}


@dataclass(frozen=True)
class SectionHeading:
    title: str
    normalized_title: str
    level: int
    numbered: bool


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    cleaned = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _word_inside_rect(word: dict[str, object], rect: tuple[float, float, float, float]) -> bool:
    """判断词中心点是否落在矩形(图形区域)内。

    rect 为 (x0, y0, x1, y1) PDF 点坐标。词中心在矩形内(含 0.5pt 容差)
    即视为图形内部文字(如架构图里的对话气泡/节点标签), 应从正文剔除。
    """
    cx = (float(word["x0"]) + float(word["x1"])) / 2.0
    cy = (float(word["top"]) + float(word["bottom"])) / 2.0
    x0, y0, x1, y1 = rect
    return x0 - 0.5 <= cx <= x1 + 0.5 and y0 - 0.5 <= cy <= y1 + 0.5


def mineru_exclude_rects(mineru_data: Any, page_number: int, page: object) -> list[tuple[float, float, float, float]]:
    """从 MinerU content_list 中取指定页(1 基)的图/表区域, 转成 PDF 点坐标矩形。

    这些区域内的文字(架构图标签/对话气泡/表格单元格)不属于正文,
    extract_page_text 用它过滤, 避免图内文字混入正文分块与翻译。
    """
    content_list = mineru_data if isinstance(mineru_data, list) else (mineru_data or {}).get("content_list") or []
    width = float(getattr(page, "width", 0) or 0)
    height = float(getattr(page, "height", 0) or 0)
    if width <= 0 or height <= 0:
        return []
    rects: list[tuple[float, float, float, float]] = []
    for item in content_list:
        if not isinstance(item, dict):
            continue
        if item.get("type") not in ("image", "chart", "table"):
            continue
        if int(item.get("page_idx", -1)) + 1 != page_number:
            continue
        bbox = item.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        try:
            norm = [float(v) / 1000.0 for v in bbox]
        except (TypeError, ValueError):
            continue
        x0n, y0n, x1n, y1n = norm
        if x1n - x0n < 0.001 or y1n - y0n < 0.001:
            continue
        # 过滤微小图标/装饰(期刊 logo 等), 与 figures.py 阈值一致
        if (x1n - x0n) * (y1n - y0n) < 0.002:
            continue
        rects.append((x0n * width, y0n * height, x1n * width, y1n * height))
    return rects


def extract_page_text(page: object, exclude_rects: list[tuple[float, float, float, float]] | None = None) -> str:
    """Extract a PDF page in reading order, including common two-column layouts.

    exclude_rects: 图形区域矩形列表(PDF 点坐标); 提供时, 落在矩形内的词(图内
    标签/对话气泡/表格单元格)从正文中剔除, 避免图内文字混入正文。
    """
    extract_text = getattr(page, "extract_text")
    extract_words = getattr(page, "extract_words", None)
    width = float(getattr(page, "width", 0) or 0)
    if extract_words is None or width <= 0:
        return extract_text() or ""

    try:
        words = extract_words(keep_blank_chars=False, use_text_flow=False) or []
    except (TypeError, AttributeError):
        return extract_text() or ""
    if exclude_rects:
        words = [w for w in words if not any(_word_inside_rect(w, r) for r in exclude_rects)]
    if len(words) < 8:
        return extract_text() or ""

    rows: list[list[dict[str, object]]] = []
    for word in sorted(words, key=lambda item: (float(item["top"]), float(item["x0"]))):
        top = float(word["top"])
        if rows and abs(top - float(rows[-1][0]["top"])) <= 2.5:
            rows[-1].append(word)
        else:
            rows.append([word])

    midpoint = width / 2
    split_rows: list[int] = []
    classified: list[tuple[list[dict[str, object]], list[dict[str, object]]]] = []
    for index, row in enumerate(rows):
        left = [word for word in row if (float(word["x0"]) + float(word["x1"])) / 2 < midpoint]
        right = [word for word in row if word not in left]
        classified.append((left, right))
        if left and right:
            column_gap = min(float(word["x0"]) for word in right) - max(
                float(word["x1"]) for word in left
            )
            if column_gap >= 8:
                split_rows.append(index)

    # 两栏判定: 跨栏行(split_rows)或左右栏词数+水平范围双保险。
    # 有些页(如公式区+正文)左右并行行很少, 但左/右栏词都很多且各居一侧,
    # 仍是两栏——若误判单栏会 fallback 到全局 y 排序, 右栏内容排到左栏标题之前,
    # 导致章节归属错(如 p.4 的公式 6-8 被归到 Text-image)。
    left_word_count = sum(len(left) for left, _ in classified)
    right_word_count = sum(len(right) for _, right in classified)
    all_left = [w for left, _ in classified for w in left]
    all_right = [w for _, right in classified for w in right]
    is_two_column = len(split_rows) >= 3 or (
        left_word_count >= 25
        and right_word_count >= 25
        and all_left
        and all_right
        and max(float(w["x1"]) for w in all_left) < midpoint + 10
        and min(float(w["x0"]) for w in all_right) > midpoint - 10
    )
    if not is_two_column:
        return extract_text() or ""

    first_body = split_rows[0] if split_rows else 0
    last_body = split_rows[-1] if split_rows else len(rows) - 1

    def row_text(items: list[dict[str, object]]) -> str:
        ordered = sorted(items, key=lambda item: float(item["x0"]))
        return " ".join(str(item["text"]) for item in ordered)

    # header(第一个跨栏行之前的行)也可能是单栏正文(如右栏公式区), 必须按栏分——
    # 否则两栏页顶部的一栏内容会按全局 y 排在另一栏标题之前, 章节归属错
    header_left: list[str] = []
    header_right: list[str] = []
    for index in range(first_body):
        left, right = classified[index]
        if left:
            header_left.append(row_text(left))
        if right:
            header_right.append(row_text(right))
    left_column: list[str] = []
    right_column: list[str] = []
    for index in range(first_body, last_body + 1):
        left, right = classified[index]
        if left:
            left_column.append(row_text(left))
        if right:
            right_column.append(row_text(right))
    footer = [row_text(rows[index]) for index in range(last_body + 1, len(rows))]
    # 阅读顺序: 左栏完再右栏(header 的左右栏分别并入对应栏)
    return "\n".join(
        item
        for item in [*header_left, *left_column, *header_right, *right_column, *footer]
        if item.strip()
    )


def detect_language(pages: list[PaperPage]) -> str:
    text = "".join(page.text[:3000] for page in pages)
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if chinese == 0 and latin == 0:
        return "unknown"
    if chinese > latin * 0.35:
        return "zh" if latin < chinese * 0.35 else "mixed"
    return "en" if chinese < latin * 0.08 else "mixed"


def _normalize_section_title(line: str) -> str | None:
    candidate = line.strip().rstrip(":：")
    for normalized, pattern in _SECTION_PATTERNS:
        if pattern.fullmatch(candidate):
            return normalized
    return None


def _heading_level(number: str | None) -> int:
    if not number:
        return 1
    if re.fullmatch(r"[IVXLC]+", number, re.I):
        return 1
    if re.fullmatch(r"[A-Z]", number, re.I):
        return 2
    return min(number.count(".") + 1, 3)


def parse_section_heading(line: str) -> SectionHeading | None:
    """识别常见论文标题，同时拒绝明显的正文长句。"""
    candidate = re.sub(r"\s+", " ", line.strip()).rstrip(":：")
    if not candidate:
        return None

    # 作者名/引用行(页眉 "Y. Yang et al." 等)不是章节标题
    if re.search(r"\bet al\.?\s*$", candidate, re.I):
        return None

    abstract = _ABSTRACT_PREFIX.search(candidate)
    if abstract:
        title = abstract.group("title")
        return SectionHeading(title=title, normalized_title="abstract", level=1, numbered=False)

    top_level = _TOP_LEVEL_IN_LINE.search(candidate)
    if top_level:
        title_text = top_level.group("title")
        title = f"{top_level.group('number')}. {title_text}"
        return SectionHeading(
            title=title,
            normalized_title=_normalize_section_title(title_text) or "other",
            level=1,
            numbered=True,
        )

    references = _REFERENCES_TOKEN.search(candidate)
    if references:
        title = references.group("title")
        return SectionHeading(title=title, normalized_title="references", level=1, numbered=False)

    if len(candidate) > 120:
        return None

    for pattern in (_LETTER_HEADING, _ROMAN_HEADING, _DECIMAL_HEADING):
        match = pattern.fullmatch(candidate)
        if not match:
            continue
        number = match.group("number")
        title_text = match.group("title").strip()
        if pattern is _LETTER_HEADING:
            title_text = re.split(r"\s+where\s+|\s+Reference\s+Patch\b", title_text, maxsplit=1)[0]
        if not title_text or len(title_text.split()) > 14 or not re.match(r"[A-Za-z\u4e00-\u9fff]", title_text):
            return None
        normalized = _normalize_section_title(title_text) or "other"
        # 参考文献条目/正文句子/表格碎片等伪标题守卫
        if pattern is _DECIMAL_HEADING and int(number.split(".")[0]) > 20:
            return None
        if pattern is _DECIMAL_HEADING and "." not in number and int(number) > 50:
            return None
        if pattern is _DECIMAL_HEADING and "." not in number and int(number) == 0:
            return None
        if pattern is _DECIMAL_HEADING and normalized == "other" and re.search(r"[,;]", title_text):
            return None
        if pattern is _DECIMAL_HEADING and normalized == "other" and title_text[:1].islower():
            return None
        if pattern is _DECIMAL_HEADING and "." not in number and normalized == "other" and len(title_text.split()) < 2:
            return None
        # 标题不应是正文句子: 以虚词结尾(如 "to a")或字母+数字碎片(如 "M 3")都不是标题
        _FUNC_WORDS = {"a","an","the","of","to","in","on","for","with","and","or","as","by","at","from","into","than","that","this","these","those","is","are","was","were","be","been","has","have","had","will","would","can","could","may","might","should","shall","do","does","did","not","no","but","its","it","their","our","your","my","his","her","we","they","to"}
        # JSON/引号结构不是章节标题: 提示词工程论文的正文常嵌入
        # "1. The output format is {\"name\": [Name of function...}" 这类
        # 编号提示词模板行, 会被 _DECIMAL_HEADING 误判为章节标题
        if re.search(r"[{\[]", title_text):
            return None
        if pattern is _DECIMAL_HEADING and title_text.split()[-1].lower() in _FUNC_WORDS:
            return None
        if pattern is _DECIMAL_HEADING and re.fullmatch(r"[A-Za-z]\s*\d.*", title_text):
            return None
        if pattern is _DECIMAL_HEADING and re.fullmatch(r"[A-Za-z]\s*\d.*", title_text):
            return None
        # 编号标题内含完整句号句(如 "2. Attribute Manipulation. This category aims
        # to enhance" / "2. Since methods based on FT perform ...")是正文列举句,
        # 不是章节标题。真实标题不会在内部出现 "句号+空格+大写词" 的新句起点。
        if pattern is _DECIMAL_HEADING and re.search(r"\.\s+[A-Z]", title_text):
            return None
        # 编号标题体以句子连接词/从句引导词开头且很长(>8 词)是正文句
        # (如 "2. Since methods based on FT perform sampling from random ..."),
        # 不是章节标题。真实标题以名词短语开头。
        _CLAUSE_START = ("since", "although", "while", "when", "because", "if", "as",
                         "however", "moreover", "furthermore", "specifically", "notably",
                         "given", "though", "whereas", "unlike", "despite", "in addition")
        if (
            pattern is _DECIMAL_HEADING
            and len(title_text.split()) > 8
            and title_text.split()[0].lower() in _CLAUSE_START
        ):
            return None
        # 字母标题(如 A. xxx)若含逗号, 多半是参考文献条目(如 "L. Van Gool, ...")
        if pattern is _LETTER_HEADING and "," in title_text:
            return None
        # 字母标题若含年份(如 "G. E. 1991. Adaptive mixtures ..." 的续行), 是参考文献条目
        if pattern is _LETTER_HEADING and re.search(r"\b(?:19|20)\d{2}\b", title_text):
            return None
        return SectionHeading(
            title=f"{number}. {title_text}" if pattern is _LETTER_HEADING else candidate,
            normalized_title=normalized,
            level=2 if pattern is _LETTER_HEADING else _heading_level(number),
            numbered=True,
        )

    normalized = _normalize_section_title(candidate)
    if normalized:
        return SectionHeading(
            title=candidate,
            normalized_title=normalized,
            level=1,
            numbered=False,
        )
    return None


_TABLE_METRIC_HINT = re.compile(
    r"PSNR|FSIM|LPIPS|SSIM|Params|FLOPs|Accuracy|Precision|Recall|mAP|IoU|Dice|F1|MAE|RMSE|NDCG",
    re.I,
)


def _looks_like_table_row(line: str) -> bool:
    """表格表头/数据行特征: 多个小数、±符号+数字、或 ≥2 个不同指标词。

    用于把"表格列名"(如 Table 的 Method 列头)与真实章节标题区分开:
    表格列名后紧跟的往往是表头行/数据行, 而章节标题后跟正文。
    """
    if "±" in line and re.search(r"\d", line):
        return True
    if len(re.findall(r"\d+\.\d+", line)) >= 2:
        return True
    hits = _TABLE_METRIC_HINT.findall(line)
    return len(set(hits)) >= 2


def mineru_content_to_sections(
    data: Any, page_count: int
) -> dict[int, list[tuple[float, SectionHeading]]]:
    """从 MinerU content_list 提取标题块(带层级), 按页码分组。

    兼容两版 schema:
    - v1: type='text' 且带 text_level(标题块才有)
    - v2: type='title', content={'title_content': ..., 'level': N}
    返回 {page_number(1 基): [(阅读顺序键, SectionHeading), ...]}。
    阅读顺序键由 bbox 计算(左栏先右栏后, 栏内按 y), 与排版通道候选一致。
    标题块之外的正文(含提示词模板行)无 text_level, 天然被排除——这正是
    "1. The output format is {...}" 这类伪标题在 MinerU 通道不再出现的原因。
    """
    items = data if isinstance(data, list) else (data or {}).get("content_list") or []
    result: dict[int, list[tuple[float, SectionHeading]]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        level: Any = None
        text = ""
        if itype == "title":
            content = item.get("content") or {}
            text = str(content.get("title_content") or "").strip()
            level = content.get("level")
        elif itype == "text" and item.get("text_level"):
            text = str(item.get("text") or "").strip()
            level = item.get("text_level")
        if not text or not level or int(level) < 1:
            continue
        page_idx = int(item.get("page_idx", -1))
        if page_idx < 0 or page_idx >= page_count:
            continue
        text = re.sub(r"^#{1,6}\s+", "", text).strip()
        text = re.sub(r"\s+", " ", text).strip(" :：")
        if not text or len(text) > 120:
            continue
        # 启发式编号/层级判定优先(罗马顶层/字母/数字的判定优先级已打磨多年,
        # 如 "I. INTRODUCTION" 走 _TOP_LEVEL_IN_LINE 得一级, "C." 字母得二级,
        # 不能简单用 _heading_level("C") 把字母当罗马数字);
        # MinerU 的价值在于标题文本(带空格)与 text_level 过滤(正文不误判)
        heuristic = parse_section_heading(text)
        if heuristic is not None:
            heading = SectionHeading(
                title=text,
                normalized_title=heuristic.normalized_title,
                level=heuristic.level,
                numbered=heuristic.numbered,
            )
        else:
            # 无编号/非常规标题: 启发式无法判定时用 MinerU 模型层级
            heading = SectionHeading(
                title=text,
                normalized_title=_normalize_section_title(text) or "other",
                level=int(level),
                numbered=False,
            )
        bbox = item.get("bbox")
        if bbox and len(bbox) == 4:
            x0n = float(bbox[0]) / 1000.0
            y0n = float(bbox[1]) / 1000.0
            key = (1.0 if x0n >= 0.5 else 0.0) * 0.5 + y0n * 0.5
        else:
            key = 0.0
        result.setdefault(page_idx + 1, []).append((key, heading))
    for page_headings in result.values():
        page_headings.sort(key=lambda item: item[0])
    return result


def _looks_like_body_sentence(title: str) -> bool:
    """最确定的伪标题检测: 编号 + 句子结构词开头的长句(正文句被当标题)。

    这是规则层的唯一"识别"——只抓模型最不可能误标的确定性伪标题
    (如 "4. We can find that GFPose consistently outperforms..."),
    其余交给 LLM 审查 agent 兜底。
    """
    t = (title or "").strip()
    if not t:
        return True
    # 编号 + 句子结构词开头 + 长句
    if re.match(
        r"^\d+(?:\.\d+)*[.\s]+(We|This|The|These|Those|In|For|Since|However|Moreover|"
        r"Specifically|Notably|Given|Compared|Figure|Table|Our|Their|Its|A|An)\s",
        t,
        re.IGNORECASE,
    ) and len(t.split()) > 6:
        return True
    # 无编号完整句: 句号结尾且 > 10 词
    if (
        not re.match(r"^(?:\d+|[IVXLC]+|[A-Z])\.?\s", t, re.I)
        and t.endswith(".")
        and len(t.split()) > 10
    ):
        return True
    return False


def _fallback_text_sections(
    pages: list[PaperPage],
) -> list[tuple[int, int, SectionHeading]]:
    """MinerU 不可用时的最简文本通道: 逐行 parse_section_heading 识别标题。

    只保留最基础的识别(编号/已知词标题), 不再做三通道合并与复杂去重;
    这些由 LLM 审查 agent 在后续步骤兜底。
    """
    found: list[tuple[int, int, SectionHeading]] = []
    for page in pages:
        lines = page.text.splitlines()
        page_cands: list[tuple[int, int, SectionHeading]] = []
        for idx, line in enumerate(lines):
            heading = parse_section_heading(line.strip())
            if heading is None:
                continue
            if _looks_like_body_sentence(heading.title):
                continue
            # 表格列名守卫: 无编号已知顶层词, 下一行是表格表头/数据行 → 不是章节标题
            if (
                not heading.numbered
                and heading.normalized_title in _KNOWN_TOP_LEVEL
                and idx + 1 < len(lines)
                and _looks_like_table_row(lines[idx + 1])
            ):
                continue
            offset = max(0, page.text.find(heading.title))
            page_cands.append((page.page_number, offset, heading))
        # 异常检测: 页面已有编号标题时, 丢弃无编号的孤立已知顶层词
        # (如正文 "Methods" 混在 "II. RELATED WORK" 前的表格标签/正文词)
        has_numbered = any(h.numbered for _p, _o, h in page_cands)
        for _p, _o, h in page_cands:
            if has_numbered and not h.numbered and h.normalized_title not in {"abstract", "references"}:
                continue
            found.append((_p, _o, h))
    return found


def infer_sections(
    pages: list[PaperPage],
    layout_headings: dict[int, list[tuple[int, float, str]]] | None = None,
    mineru_sections: dict[int, list[tuple[float, SectionHeading]]] | None = None,
) -> list[PaperSectionData]:
    mineru_sections = mineru_sections or {}
    # ============ MinerU 为主 ============
    # MinerU 版面模型已输出结构化标题(带空格、层级、bbox 位置)。
    # 直接以它为权威来源, 只做轻量校验: 去重、过滤明显伪标题、页码对齐。
    # 规则不再参与"识别"(识别是模型的事), 只做"异常检测"。
    if mineru_sections:
        found: list[tuple[int, int, SectionHeading]] = []
        for page_number in sorted(mineru_sections):
            for _key, heading in mineru_sections[page_number]:
                title = (heading.title or "").strip()
                if not title:
                    continue
                # 过滤明显伪标题: 编号 + 正文句(规则层只保留这一条最确定的)
                if _looks_like_body_sentence(title):
                    continue
                # 页码对齐: MinerU 的 key 是 0~1 阅读序, 转成页内偏移量纲用于排序
                offset = int(_key * 1000) if _key < 1 else int(_key)
                found.append((page_number, offset, heading))
        if not found and pages:
            # MinerU 无结果(异常): 回退到最简文本通道
            found = _fallback_text_sections(pages)
    else:
        # MinerU 完全不可用: 回退文本通道
        found = _fallback_text_sections(pages)

    sections: list[PaperSectionData] = []
    last_page = pages[-1].page_number if pages else 1
    for index, (page_number, _heading_offset, heading) in enumerate(found):
        if index + 1 < len(found):
            next_page, next_heading_offset, _next_heading = found[index + 1]
            page_end = next_page if next_page > page_number and next_heading_offset > 0 else next_page - 1
        else:
            page_end = last_page
        level = heading.level
        # 无编号的 Discussion 若紧随 Experiments 顶层章节之后, 是实验部分的小节
        # (如 AAAI 论文的 4.x Discussion), 而不是独立顶层章节
        if heading.normalized_title == "discussion" and not heading.numbered:
            prev_top = next(
                (h for p, _o, h in reversed(found[:index]) if h.level == 1),
                None,
            )
            if prev_top is not None and prev_top.normalized_title in {"experiments", "results"}:
                level = 2
        sections.append(
            PaperSectionData(
                title=heading.title,
                normalized_title=heading.normalized_title,
                level=level,
                ordinal=index,
                page_start=page_number,
                page_end=max(page_number, page_end),
            )
        )
    return sections


def _split_authors(value: object) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in re.split(r";|,|\band\b", str(value)) if part.strip()]


_ABSTRACT_HEADING = re.compile(r"(?:^|\n)\s*(?:abstract|摘要)\b", re.I)
_ABSTRACT_SPACED = re.compile(r"\bA\s*B\s*S\s*T\s*R\s*A\s*C\s*T\b", re.I)
_ABSTRACT_STOP = re.compile(
    r"(?:^|\n)\s*(?:[IVXLC\d]+\.?\s+)?(?:introduction|keywords|index\s*terms|摘要|参考文献)\b",
    re.I,
)


def _extract_abstract(
    pdf_path: str | Path, pages: list[PaperPage], sections: list[PaperSectionData]
) -> str:
    """从首页/次页提取摘要, 兼容多种写法(Abstract? / A B S T R A C T / 摘要)。

    用 PyMuPDF 提取(对字体编码更鲁棒, 避免 pdfplumber 在部分期刊模板上
    把首栏字形交错读出); 取标题到下一个章节标题(Introduction/Keywords)之间的文字。
    """
    try:
        import pymupdf
    except ImportError:
        return ""
    head = ""
    try:
        with pymupdf.open(str(pdf_path)) as doc:
            for index in range(min(2, len(doc))):
                head += doc[index].get_text() + "\n"
    except Exception:
        return ""
    text = head
    match = _ABSTRACT_SPACED.search(text) or _ABSTRACT_HEADING.search(text)
    if not match:
        return ""
    start = match.end()
    stop = _ABSTRACT_STOP.search(text, start)
    end = stop.start() if stop else len(text)
    lines: list[str] = []
    for line in text[start:end].splitlines():
        line = line.strip()
        if not line:
            continue
        # 跳过期刊信息/关键词标签行(Elsevier 模板会把 Keywords 与摘要挤在同一块)
        if re.match(r"^(?:keywords?|article info|graphical abstract)\b", line, re.I):
            continue
        lines.append(line)
    cleaned = re.sub(r"\s+", " ", " ".join(lines)).strip()
    # 去掉开头残留的特殊字符(如 Abstract 后的 em-dash 被映射为 ?)
    cleaned = re.sub(r"^[^\w\u4e00-\u9fff]+", "", cleaned)
    return cleaned[:5000]


_TITLE_NOISE = re.compile(
    r"arXiv|DOI|ISSN|ISBN|Contents lists|journal homepage|www\.|"
    r"©|Copyright|Anonymous submission|^\d+$|^[IVXLC]+\s*$",
    re.I,
)

_YEAR_RE = re.compile(r"(?:copyright\s*\(c\)|©)?\s*\b(20\d{2})", re.I)
_ARXIV_RE = re.compile(r"arxiv:\s*(\d{2})\d{2}[.]", re.I)


def extract_publication_year(first_page_text: str, meta_year: str = "") -> int | None:
    """从元数据/首页文本提取发表年份, 提取不到返回 None(不抛异常)。"""
    for raw in (meta_year, first_page_text):
        if not raw:
            continue
        m = _ARXIV_RE.search(raw)
        if m:
            return 2000 + int(m.group(1))
        found = [int(y) for y in _YEAR_RE.findall(raw) if 1990 <= int(y) <= 2100]
        if found:
            return max(found)
    return None


def _extract_title_from_pdf(file_path: Path) -> str | None:
    """元数据 Title 缺失时, 用 pymupdf 字号启发式从第一页提取完整标题。

    论文标题字号通常是正文的 1.5~2.5 倍且常跨 2~3 行(如 AAAI/IEEE 匿名投稿
    元数据为空); 这里取第一页字号最大的文本块并按行拼接, 过滤页眉/页码/期刊名等噪音。
    """
    try:
        import pymupdf
    except Exception:
        return None
    spans: list[tuple[float, float, float, str]] = []
    try:
        with pymupdf.open(str(file_path)) as doc:
            data = doc[0].get_text("dict")
    except Exception:
        return None
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span["text"].strip()
                if not text:
                    continue
                _x0, y0, _x1, _y1 = span["bbox"]
                spans.append((float(span["size"]), y0, float(span["bbox"][0]), text))
    if not spans:
        return None
    # 标题候选必须位于页面上半部: 页面标题区在顶部, 而下沉首字母(drop cap,
    # 正文段落开头的超大字母如 "W")会出现在页面中部/下部, 且是孤立单字符
    try:
        with pymupdf.open(str(file_path)) as doc:
            page_height = float(doc[0].rect.height) or 792.0
    except Exception:
        page_height = 792.0
    top_half = [s for s in spans if s[1] < page_height * 0.45]
    if not top_half:
        top_half = spans
    # 按字号从大到小逐级找候选: 每级要求非噪音; 若某级全被噪音过滤
    # (如 arXiv 的 "arXiv:2211.09800v2 [cs.CV] 18 Jan 2023" 页眉行字号最大),
    # 下探到下一字号组, 而不是回退到含噪音的候选。
    sizes = sorted({item[0] for item in top_half}, reverse=True)
    title_spans: list[tuple[float, float, float, str]] = []
    for size in sizes:
        level = [
            item
            for item in top_half
            if item[0] >= size - 0.6 and not _TITLE_NOISE.search(item[3])
        ]
        # 丢弃孤立单字符候选(下沉首字母/装饰字母): 标题至少是单词级文本
        if level and not (len(level) == 1 and len(level[0][3]) <= 2):
            title_spans = level
            break
    # 按行聚类(同行按 x 排序), 行间按 y 排序拼接
    rows: dict[int, list[tuple[float, str]]] = {}
    for _size, y0, x0, text in title_spans:
        rows.setdefault(round(y0 / 4), []).append((x0, text))
    lines = []
    for key in sorted(rows):
        rows[key].sort()
        lines.append(" ".join(text for _x, text in rows[key]))
    title = re.sub(r"\s+", " ", " ".join(lines)).strip()
    title = title.strip(" \t.:;,-_")
    return title or None



_LAYOUT_NOISE = re.compile(
    r"arXiv|DOI|ISSN|ISBN|Anonymous|submission|www\\.|http|e-mail|Corresponding|"
    r"CRediT|Declaration of competing|Data availability|Reference",
    re.I,
)
_LAYOUT_FIGREF = re.compile(r"^(?:figure|fig\.?|table|tab\.?|algorithm|algo\.?)\b", re.I)
_MAX_LAYOUT_HEADING_WORDS = 14
_MAX_LAYOUT_SIZE_GROUP_LINES = 30


def _detect_layout_headings(pdf_path: str | Path) -> dict[int, list[tuple[int, float, str]]]:
    """pymupdf 排版通道: 检测字号显著大于正文、行数稀少的标题行。

    解决连写/无编号论文的小节识别(如 "FrameworkofTIFF-CEM" 在排版层是
    带空格、字号更大的 "Framework of TIFF-CEM"):
    - 正文字号 = 全文行字号众数(行数最多的字号组);
    - 候选: 字号 > 正文+0.4, 且该字号组全文行数 ≤ 30(标题行数少, 正文行数多);
    - 层级: 字号差 ≥1.5 → level 1(顶层), 否则 level 2(小节);
    - 过滤: 图注/表注、页眉/期刊名/页码、公式/纯符号、悬空文字(下方无正文)。

    返回 {page: [(level, y_frac, text), ...]}, y_frac ∈ [0,1] 用于与页文本偏移对齐。
    """
    all_lines: list[tuple[int, float, float, float, str]] = []
    try:
        import pymupdf
        with pymupdf.open(str(pdf_path)) as doc:
            for pno, page in enumerate(doc, start=1):
                data = page.get_text("dict")
                page_height = max(1.0, float(page.rect.height))
                page_width = max(1.0, float(page.rect.width))
                mid = page_width / 2.0
                for block in data.get("blocks", []):
                    for line in block.get("lines", []):
                        spans = [s for s in line.get("spans", []) if s["text"].strip()]
                        if not spans:
                            continue
                        size = max(s["size"] for s in spans)
                        text = " ".join(s["text"] for s in spans).strip()
                        if not text:
                            continue
                        x0 = float(line["bbox"][0])
                        y = float(line["bbox"][1])
                        all_lines.append((pno, float(size), y, x0, text))
    except Exception:
        return {}
    if not all_lines:
        return {}
    size_counts: dict[float, int] = {}
    for _p, size, _y, _x, _t in all_lines:
        size_counts[size] = size_counts.get(size, 0) + 1
    body = max(size_counts, key=size_counts.get)
    by_page: dict[int, list[tuple[int, float, int, str]]] = {}
    for pno, size, y, x0, text in all_lines:
        if pno < 2:
            continue  # 首页有标题/作者/期刊名区, 噪音多, 由文本通道负责
        if size < body + 0.4:
            continue
        if size_counts.get(size, 0) > _MAX_LAYOUT_SIZE_GROUP_LINES:
            continue
        t = text.strip()
        if not (3 <= len(t) <= 120):
            continue
        if len(re.findall(r"[A-Za-z]", t)) < 3:
            continue
        if not t[0].isupper() or re.search(r"[.,;:]$", t):
            continue
        if _LAYOUT_FIGREF.match(t):
            continue
        if _LAYOUT_NOISE.search(t):
            continue
        if len(t.split()) > _MAX_LAYOUT_HEADING_WORDS:
            continue
        # 标题下方必须紧跟正文字号行(标题不悬空; 图内文字/孤行下方无正文)
        has_body_below = any(
            pp == pno and ss == body and yy > y and yy - y < 60
            for pp, ss, yy, _xx, _tt in all_lines
        )
        if not has_body_below:
            continue
        level = 1 if size - body >= 1.5 else 2
        col = 1 if x0 >= mid else 0
        by_page.setdefault(pno, []).append((level, y / page_height, col, t))
    return by_page

def parse_pdf(path: str | Path, mineru_data: Any | None = None) -> ParsedPaper:
    """解析 PDF 为结构化论文(页面/章节/元数据)。

    mineru_data: 可选的 MinerU content_list 数据(由调用方先跑 MinerU 获得)。
    提供时, 章节标题以 MinerU 版面检测为主、启发式为兜底;
    不提供时退化为纯启发式(向后兼容)。
    """
    file_path = Path(path)
    with pdfplumber.open(file_path) as pdf:
        pages = [
            PaperPage(
                page_number=index,
                text=normalize_text(
                    extract_page_text(
                        page,
                        exclude_rects=(
                            mineru_exclude_rects(mineru_data, index, page)
                            if mineru_data
                            else None
                        ),
                    )
                ),
            )
            for index, page in enumerate(pdf.pages, start=1)
        ]
        raw_metadata = pdf.metadata or {}

    extractable_length = sum(len(page.text) for page in pages)
    if extractable_length < 80:
        raise UnsupportedScanError("PDF 中没有足够的可提取文本，当前版本暂不支持扫描版 PDF")

    layout_headings = _detect_layout_headings(file_path)
    mineru_sections = (
        mineru_content_to_sections(mineru_data, len(pages))
        if mineru_data
        else None
    )
    sections = infer_sections(
        pages,
        layout_headings=layout_headings,
        mineru_sections=mineru_sections,
    )
    title = str(raw_metadata.get("Title") or raw_metadata.get("title") or "").strip()
    if not title:
        # 元数据缺失(AAAI/IEEE 匿名投稿常见): 字号启发式提取完整标题
        title = _extract_title_from_pdf(file_path) or ""
    if not title:
        first_line = next((line for page in pages for line in page.text.splitlines() if line.strip()), file_path.stem)
        title = str(first_line).strip()
    title = re.sub(r"\s+", " ", title).strip(" \t.:;,-_")
    authors = _split_authors(raw_metadata.get("Author") or raw_metadata.get("author"))
    abstract = _extract_abstract(file_path, pages, sections)
    # 摘要章节兜底: 有摘要但没识别出 Abstract 章节时(如 "A B S T R A C T" 字母间空格排版),
    # 在顶部注入 Abstract 章节, 保证章节树里 Introduction 之上始终有摘要项
    if abstract and not any(item.normalized_title == "abstract" for item in sections):
        intro = next(
            (item for item in sections if item.normalized_title in {"introduction", "related_work"}),
            None,
        )
        abs_end = (intro.page_start - 1) if intro is not None else 1
        sections.insert(
            0,
            PaperSectionData(
                title="Abstract",
                normalized_title="abstract",
                level=1,
                ordinal=0,
                page_start=1,
                page_end=max(1, abs_end),
                summary="",
            ),
        )
        for offset, item in enumerate(sections):
            item.ordinal = offset
    metadata = PaperMetadata(
        title=title,
        authors=authors,
        abstract=abstract,
    )
    return ParsedPaper(
        pages=pages,
        sections=sections,
        metadata=metadata,
        language=detect_language(pages),
        page_count=len(pages),
        publication_year=extract_publication_year(
            pages[0].text if pages else "",
            str(raw_metadata.get("CreationDate") or raw_metadata.get("creationDate") or ""),
        ),
    )
