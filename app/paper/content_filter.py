"""Conservative removal of figure/table text from translation input."""

from __future__ import annotations

import re


_CAPTION = re.compile(
    r"^\s*(?:(?:fig(?:ure)?\.?|table)\s*(?:\d+[a-z]?|[IVXLCDM]+)\s*[:：.]|(?:图|表)\s*(?:\d+[a-z]?|[IVXLCDM]+)\s*[:：.])",
    re.IGNORECASE,
)
_SECTION_HEADING = re.compile(
    r"^\s*(?:(?:[IVXLC]+|\d+(?:\.\d+)*|[A-Z])[.、]\s+)[A-Za-z\u4e00-\u9fff]"
)
_NUMBERED_EQUATION = re.compile(r"^\s*.+?=.+?[（(]\s*\d+[a-z]?\s*[）)]\s*$", re.IGNORECASE)
_BODY_START = re.compile(
    r"^\s*(?:The|We|This|These|After|Following|前文|后文|我们|随后|因此|其中|为此|该方法|这些)",
    re.IGNORECASE,
)
_VISUAL_WORDS = {
    "anchor",
    "attention",
    "categorize",
    "conv",
    "downscale",
    "pixel",
    "prompting",
    "shuffle",
    "uncategorized",
}


_HEADER_METRICS = re.compile(
    r"(?:PSNR|SSIM|LPIPS|FSIM|Params|FLOPs|Method|Scene|Thin|Moderate|Thick|RICE|RSID|SateHaze)",
    re.I,
)


def _look_like_figure_label(line: str) -> bool:
    """图内标签行: 短行(≤40 字符)、无等号(排除公式)、非正文句。"""
    text = line.strip()
    if not text or len(text) > 40:
        return False
    if "=" in text:
        return False
    if _looks_like_prose_or_heading(text):
        return False
    return True


def _looks_like_table_content_line(line: str) -> bool:
    """表格数据/表头行特征: 数字密集(≥2 个小数)、符号短行(+ 26.56 / ✓ / ±)、
    或指标词占比高的表头行(Method Params PSNR SSIM)。"""
    text = line.strip()
    if not text:
        return False
    if len(re.findall(r"\d+\.\d+", text)) >= 2:
        return True
    if len(text) < 40 and text[:1] in {"+", "-", "±", "✓", "✗", "•", "●"}:
        return True
    tokens = re.findall(r"[A-Za-z0-9]+", text)
    hits = _HEADER_METRICS.findall(text)
    if len(hits) >= 2 and len(hits) / max(1, len(tokens)) > 0.5:
        return True
    # 中文表头/短表格行(如 "算法 尺度 #参数"): 短行且含 #/×/数字, 且非正文句
    return len(text) <= 35 and re.search(r"[#××]|\d", text)


def _looks_like_prose_or_heading(line: str) -> bool:
    text = line.strip()
    if not text:
        return False
    if _SECTION_HEADING.match(text) or _NUMBERED_EQUATION.match(text):
        return True
    chinese_count = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin_words = len(re.findall(r"[A-Za-z]{2,}", text))
    if chinese_count >= 5 and re.search(r"[，。；！？]\s*$", text):
        return True
    if latin_words >= 7 and re.search(r"[.!?]\s*$", text):
        return True
    return len(text) >= 60 and (chinese_count >= 8 or latin_words >= 8)


def _looks_like_strong_boundary(line: str) -> bool:
    text = line.strip()
    if not text:
        return False
    if re.match(r"^(?:以及)?\s*[（(][a-z][）)]", text, re.IGNORECASE):
        return False
    if _SECTION_HEADING.match(text) or _NUMBERED_EQUATION.match(text) or _BODY_START.match(text):
        return True
    chinese_count = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin_words = len(re.findall(r"[A-Za-z]{2,}", text))
    return len(text) >= 60 and (chinese_count >= 8 or latin_words >= 8)


def _has_reversed_visual_word(line: str) -> bool:
    return any(
        token.lower()[::-1] in _VISUAL_WORDS
        for token in re.findall(r"[A-Za-z]{4,}", line)
    )


def _reversed_label_groups(lines: list[str]) -> list[tuple[int, int]]:
    indexes = [index for index, line in enumerate(lines) if _has_reversed_visual_word(line)]
    if len(indexes) < 2:
        return []
    groups: list[list[int]] = [[indexes[0]]]
    for index in indexes[1:]:
        if index - groups[-1][-1] <= 20:
            groups[-1].append(index)
        else:
            groups.append([index])
    return [(group[0], group[-1]) for group in groups if len(group) >= 2]


def strip_visual_regions(text: str) -> str:
    """Remove caption-anchored visual labels while preserving prose and equations.

    The filter deliberately does nothing when no explicit figure/table caption is
    present. This keeps ambiguous short lines intact instead of guessing.
    """

    lines = text.splitlines()
    caption_indexes = [index for index, line in enumerate(lines) if _CAPTION.match(line)]
    reversed_groups = _reversed_label_groups(lines)
    if not caption_indexes and not reversed_groups:
        return text

    removed: set[int] = set()
    for caption_index in caption_indexes:
        removed.add(caption_index)

        # Figure labels are commonly extracted immediately before a caption.
        # 只删"图内标签"(短行无 =), 公式/正文行(含 = 或长行)保留——
        # 图注嵌在两栏正文中间时, 前面的公式(6)(7)(8)不能被误删
        cursor = caption_index - 1
        scanned = 0
        while cursor >= 0 and scanned < 40:
            if _looks_like_prose_or_heading(lines[cursor]):
                break
            if not _look_like_figure_label(lines[cursor]):
                break
            removed.add(cursor)
            cursor -= 1
            scanned += 1

        # Captions may wrap; consume continuation lines until the caption sentence ends.
        caption_cursor = caption_index
        while caption_cursor < len(lines) - 1 and not re.search(r"[.!?。！？]\s*$", lines[caption_cursor].strip()):
            caption_cursor += 1
            removed.add(caption_cursor)

        # Table rows and diagrams whose caption is above them appear after the caption.
        # 注意: 只删"表格数据特征"行(数字密集/符号短行); 连写正文行(无标点、
        # 词数少)不能误判为非 prose 删除, 否则 caption 后的正文段落会被整个吞掉
        cursor = caption_cursor + 1
        scanned = 0
        while cursor < len(lines) and scanned < 60:
            if _looks_like_prose_or_heading(lines[cursor]):
                break
            if not _looks_like_table_content_line(lines[cursor]):
                break
            removed.add(cursor)
            cursor += 1
            scanned += 1

    for first_marker, last_marker in reversed_groups:
        cursor = first_marker - 1
        scanned = 0
        while cursor >= 0 and scanned < 40:
            if _looks_like_strong_boundary(lines[cursor]):
                break
            removed.add(cursor)
            cursor -= 1
            scanned += 1
        for index in range(first_marker, last_marker + 1):
            removed.add(index)
        cursor = last_marker + 1
        scanned = 0
        while cursor < len(lines) and scanned < 60:
            if _looks_like_strong_boundary(lines[cursor]):
                break
            removed.add(cursor)
            cursor += 1
            scanned += 1

    kept = [line for index, line in enumerate(lines) if index not in removed]
    cleaned = "\n".join(kept)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned
