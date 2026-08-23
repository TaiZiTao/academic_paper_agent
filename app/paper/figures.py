"""论文图表区域检测与图片渲染。

- 检测:按页扫描图/表标题(Fig. N / Figure N / TABLE N / 图 N / 表 N),表取标题下方表格网格
  边界,图取标题上方图元(内嵌图片 + 矢量矩形)的并集区域。
- 渲染:用 PyMuPDF 按区域渲染为 PNG。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import os
import shutil
import subprocess
import tempfile

import pdfplumber
import pymupdf
from loguru import logger

_CAPTION = re.compile(
    r"^\s*(?:(?:fig(?:ure)?\.?|table)\s*(?:\d+[a-z]?|[IVXLCDM]+)\s*(?:[:：.])|(?:图|表)\s*(?:\d+[a-z]?|[IVXLCDM]+)\s*(?:[:：.]))",
    re.IGNORECASE,
)
# 空格风格表格标题(无标点, 如 "Table 1 Computational complexity of each module..."):
# 数字后必须是**大写词**(真实表注都是名词短语开头), 正文引用("TABLE II presents...")
# 以小写动词开头会被拒; 表格行内容校验(_region_has_table_rows)再做第二道防线。
_TABLE_CAPTION_LOOSE = re.compile(
    r"^\s*[Tt]able\s*(?:\d+[a-z]?|[IVXLCDM]+)(?:\s*[A-Z]|$)"
)
# 正文引用行误判为图注的过滤器: 标签后紧跟句子结构词(We find / Then we combine /
# In contrast / respectively 等)说明是正文句子而非图注(真实图注是名词短语)。
# 词在提取时被无空格拼接(如 "We evaluate" → "Weevaluate"), 词边界\b 失效,
# 用 we+动词表 / then+[,a-z] 等显式模式识别句子结构
_CAPTION_SENTENCE = re.compile(
    r"^\s*(?:fig(?:ure)?\.?|table)\s*(?:\d+[a-z]?|[IVXLCDM]+)\s*(?:[:：.])?\s*"
    r"(?:we\s*(?:evaluate|find|can|observe|present|show|compare|combine|summarize|report|list|give|achieve|obtain|conduct|perform|use|apply|train|test|also|will|have|are|were|conclude|demonstrate|illustrate|follow|adopt|set)"
    r"|then(?=[,a-z])|however\b|moreover\b|in\s*contrast|respectively\b|as\s*shown)",
    re.IGNORECASE,
)
_TABLE_PREFIX = re.compile(r"^\s*(?:Table|TABLE|表)", re.IGNORECASE)
_MIN_TABLE_AREA = 8000.0


def _restore_caption_spacing(text: str) -> str:
    """把无空格拼接的图注文本重建为可读形式(仅展示用途)。

    转换类 PDF 的文字层常丢失词间空格("Manga109datasetforupscalingfactor×4."),
    按 标签编号 / 数字↔字母 / 小写→大写 / 句点后 的边界插入空格。
    在 caption 收集完成后调用, 不影响行内匹配逻辑。
    """
    # 标签与编号: Table1 → Table 1, Fig.1 → Fig. 1, Figure2 → Figure 2
    text = re.sub(r"\b(table|fig(?:ure)?\.?)(\d+)", r"\1 \2", text, flags=re.IGNORECASE)
    # 字母数字混合串按 字母块/数字块 分组: Manga109dataset → Manga 109 dataset,
    # DF2K → DF 2K(数字块后的结尾短字母块如 "2K" 保持相连); 模型名
    # (ViT/SwinIR/AMCANet 等纯字母串)保持原样, 不做驼峰拆分
    text = re.sub(
        r"[A-Za-z0-9]+",
        lambda m: _split_alnum_run(m.group(0)),
        text,
    )
    # 句点后紧跟字母: "1.Manga" → "1. Manga"
    text = re.sub(r"\.(?=[A-Za-z])", ". ", text)
    return re.sub(r"\s+", " ", text).strip()


def _split_alnum_run(s: str) -> str:
    """把字母数字混合串按 字母块/数字块 拆分。

    数字块后若紧跟结尾的短字母块(≤2 字符, 如 "DF2K" 的 "2K")则保持相连,
    避免 "DF 2 K" 式误拆。
    """
    parts: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        j = i
        while j < n and s[j].isdigit() == s[i].isdigit():
            j += 1
        parts.append(s[i:j])
        i = j
    merged: list[str] = []
    k = 0
    while k < len(parts):
        p = parts[k]
        if (
            p
            and p[0].isdigit()
            and k + 1 < len(parts)
            and len(parts[k + 1]) <= 2
            and parts[k + 1].isalpha()
            and k + 2 == len(parts)
        ):
            merged.append(p + parts[k + 1])
            k += 2
        else:
            merged.append(p)
            k += 1
    return " ".join(merged)
_MAX_FIGURE_HEIGHT_RATIO = 0.60
# 图区域向上聚拢时允许的最大间距: 同一张图的图元紧贴图注, 页头装饰(logo/标题线)
# 与图之间隔着大段空白, 超过该间距即停止
_FIGURE_OBJECT_GAP = 40.0
_PAD = 12.0


@dataclass
class FigureRegion:
    page: int
    kind: str  # "figure" | "table"
    caption: str
    x0: float
    y0: float
    x1: float
    y1: float


_HEADING = re.compile(r"^\s*(?:[IVXLC]+|\d+(?:\.\d+)*|[A-Z])[.、]\s+[A-Za-z]")
_MAX_CAPTION_LINES = 6
_MAX_CAPTION_CHARS = 700
_MAX_CAPTION_GAP = 16.0


_HEADING = re.compile(r"^\s*(?:[IVXLC]+|\d+(?:\.\d+)*|[A-Z])[.、]\s+[A-Za-z]")
_MAX_CAPTION_LINES = 6
_MAX_CAPTION_CHARS = 700
_MAX_CAPTION_GAP = 16.0
_MAX_SKIP_ROWS = 4
_OTHER_FIG_REF = re.compile(r"(?:Fig(?:ure)?\.?\s*\d+|图\s*\d+|Table\s*[IVXLC\d]+|表\s*[IVXLC\d]+)")


def _is_table_content_row(
    line: str, words: list[dict[str, Any]] | None = None
) -> bool:
    """表格内容行(表头/数据)判定:引用、参数表头、数据集表头、数字开头、数字密集、多列词簇。

    注意:单独的缩放标记(如 \"ws on Urban100 (×4).\" 中的 ×4)不足以判定为表格行,
    它可能是图注续行;需要与引用、表头或数字密度等更强信号组合。
    """
    if re.search(r"\[\d+\]|#\s*Params", line):
        return True
    if re.match(r"^(?:Set\d+|BSD\d+|Urban\d+|Manga\d+)", line):
        return True
    if re.match(r"^\d+[A-Za-z]", line):
        return True
    if len(line) >= 15:
        digits = len(re.findall(r"\d", line))
        if digits / len(line) > 0.4:
            return True
    # 多列表头/数据行:词间间隙 >8pt 形成 ≥3 个词簇(如 Methods | Frequency Domain | ...)
    if words is not None:
        gaps = sum(
            1
            for i in range(1, len(words))
            if float(words[i]["x0"]) - float(words[i - 1]["x1"]) > 8
        )
        if gaps >= 2:
            return True
    return False


def _line_captions(page: Any) -> list[dict[str, Any]]:
    """找出图/表标题行,并收集同一栏内的续行组成完整标题文本。

    续行规则:跳过其他栏的行(有上限)、行距不超过阈值、跳过图内短碎片行、
    遇新标题/章节标题/页码停止;续行内遇到对其他图/表的引用时截断(通常表示图注结束)。
    """
    lines: dict[int, list[dict[str, Any]]] = {}
    for word in page.extract_words():
        key = round(word["top"] / 4)
        lines.setdefault(key, []).append(word)
    rows: list[dict[str, Any]] = []
    for _key, words in sorted(lines.items()):
        words = sorted(words, key=lambda w: w["x0"])
        rows.append(
            {
                "top": min(w["top"] for w in words),
                "bottom": max(w["bottom"] for w in words),
                "x0": min(w["x0"] for w in words),
                "x1": max(w["x1"] for w in words),
                "words": words,
                "text": "".join(w["text"] for w in words),
            }
        )
    captions: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        # 行聚类按 top 分桶会把同高度的左右栏文字合并成一行(如左栏 Table2 与右栏 Table4
        # 的标题), 合并行内可能内嵌多个图/表标签——逐标签生成 caption, 避免吞掉第二个
        label_offsets = [0] + [
            i
            for i, w in enumerate(row["words"])
            if i > 0
            and (
                _CAPTION.match(w["text"])
                or _TABLE_CAPTION_LOOSE.match(w["text"])
                # "Table 1 Computational..." 中 Table 与编号是两个独立词:
                # 标签词(Table/Fig)后紧跟编号词时也视为标签起点
                or (
                    re.match(r"^(?:fig(?:ure)?\.?|table)$", w["text"], re.IGNORECASE)
                    and i + 1 < len(row["words"])
                    and re.match(r"^\d+[a-z]?$|^[IVXLCDM]+$", row["words"][i + 1]["text"])
                )
            )
        ]
        for li, start in enumerate(label_offsets):
            end = label_offsets[li + 1] if li + 1 < len(label_offsets) else len(row["words"])
            sub_words = row["words"][start:end]
            sub_text = "".join(w["text"] for w in sub_words)
            # 首行可能混入同高度的另一栏文字(两栏排版),按词间间隙截断到第一列——
            # 宽松表格匹配按截断后的首行文本判定(标签行后紧跟另一栏小写词时,
            # 如 "Table 4 observed in existing..." 中 observed 来自右栏, 用 sub_text
            # 会误拒; 截断后只看本栏的 "Table4" 标签 + 续行标题)
            first_words: list[dict[str, Any]] = []
            for word in sub_words:
                if first_words and word["x0"] - first_words[-1]["x1"] > 8:
                    break
                first_words.append(word)
            first_line = "".join(w["text"] for w in first_words) or sub_text
            if not (_CAPTION.match(sub_text) or _TABLE_CAPTION_LOOSE.match(first_line)):
                continue
            text = first_line
            # 首行 x 范围用截断后的第一列(避免混入同高度另一栏文字, 如右栏单词)
            cap_x0 = sub_words[0]["x0"] if sub_words else row["x0"]
            cap_x1 = first_words[-1]["x1"] if first_words else row["x1"]
            last_bottom = row["bottom"]
            collected = 1
            skipped = 0
            for cursor in range(index + 1, len(rows)):
                nxt = rows[cursor]
                # 表格 caption: 某行以句号/问号结尾即视为 caption 完整, 停止收集——
                # 防止表格下方的正文/小节标题(Effectiveness of ... 等)混进表注,
                # 也避免表注 y1 下移导致表格区域定位到正文
                if _TABLE_PREFIX.match(text) and text.rstrip().endswith((".", "!", "?")):
                    break
                # 图注: 已收集以句号结束, 且下一行在本栏内以小写字母开头 → 正文段落延续, 图注结束。
                # 合并行(左右栏同高度)可能以另一栏的小写词开头(如左栏 Abstract 续行 + 右栏图注
                # (b) 行), 必须看本栏词的第一个字符, 否则图注被误截断成 (a)
                if not _TABLE_PREFIX.match(text):
                    _mid = page.width / 2.0
                    if cap_x1 <= _mid + 20:
                        _col_first = next(
                            (w for w in nxt["words"] if w["x0"] < _mid), None
                        )
                    else:
                        _col_first = next(
                            (w for w in nxt["words"] if w["x0"] >= _mid), None
                        )
                    # 图注续行以 "where" 开头(公式变量说明, 如 "where beta is the balancing...")
                    # → 图注结束, 即使前面没有句号(caption 的 (c) 部分直接接正文)
                    if _col_first and _col_first["text"].lower().startswith("where"):
                        break
                    # 已收集以句号结束, 且下一行在本栏内以小写字母开头 → 正文段落延续
                    if (
                        text.rstrip().endswith((".", "!", "?"))
                        and _col_first
                        and _col_first["text"][:1].islower()
                    ):
                        break
                if (
                    _CAPTION.match(nxt["text"])
                    or _TABLE_CAPTION_LOOSE.match(nxt["text"])
                    or _HEADING.match(nxt["text"])
                ):
                    break
                if re.search(r"\(Ours\)", nxt["text"]):
                    break
                if re.fullmatch(r"\s*\d+\s*", nxt["text"]):
                    break
                # 同栏判定:单栏表注要求续行与标题同列(起始 x0 ±40pt),跨栏正文行跳过;
                # 通栏表(标题跨中缝)用 x 范围重叠判定, 兼容错层排布的表头(列头 x 偏移);
                # 图注允许续行缩进(仅要求 x 范围有重叠)
                if _TABLE_PREFIX.match(text):
                    # 首行已按词间隙截断到第一列(cap_x1), 同栏判定以截断后的列为准:
                    # 若用未截断的 row["x1"](混入另一栏的 caption/文字), 左栏表注会被误判为右栏
                    if cap_x1 > page.width / 2.0 + 20:
                        # 右栏表注: 续行必须从右栏开始(避免左栏正文行因 x 范围重叠混入)
                        same_column = (
                            nxt["x0"] >= page.width / 2.0 - 4
                            and nxt["x1"] > row["x0"] + 20
                            and nxt["x0"] < row["x1"] - 20
                        )
                    else:
                        same_column = abs(nxt["x0"] - row["x0"]) <= 40
                else:
                    same_column = nxt["x0"] < cap_x1 - 5 and nxt["x1"] > cap_x0 + 5
                if not same_column:
                    skipped += 1
                    if skipped > _MAX_SKIP_ROWS:
                        break
                    continue
                skipped = 0
                # 表格表注续行: 行聚类可能把同高度另一栏的词合并进来
                # (如左栏 "minationinformation." 与右栏 "w/o(left)..." 同 key 合并),
                # 只保留本栏的词再判断/拼接
                mid = page.width / 2.0
                line_words = nxt["words"]
                # 表格/图注续行都按栏过滤词(合并行可能混入另一栏文字)
                if cap_x1 <= mid + 20:
                    line_words = [w for w in line_words if w["x0"] < mid]
                else:
                    line_words = [w for w in line_words if w["x0"] >= mid]
                if not line_words:
                    continue
                col_text = " ".join(w["text"] for w in line_words)
                # 同栏且为表格内容行(表头/数据)时停止(放在同栏判断之后,避免跨栏行误触发)
                if _TABLE_PREFIX.match(text) and _is_table_content_row(col_text, line_words):
                    break
                if nxt["top"] - last_bottom > _MAX_CAPTION_GAP:
                    break
                if len(col_text) < 16 and re.fullmatch(r"[A-Za-z ]+", col_text):
                    continue
                # 图注多子图(a)(b)(c)可能 8+ 行, 行数上限放宽; 表格保持 6 行
                if collected >= (_MAX_CAPTION_LINES + 4 if not _TABLE_PREFIX.match(text) else _MAX_CAPTION_LINES):
                    break
                # 截断:续行内出现对其他图/表的引用时,只取引用之前的部分并结束收集
                line_text = col_text
                ref = _OTHER_FIG_REF.search(line_text)
                if ref:
                    line_text = line_text[: ref.start()]
                    # 去掉句号后残留的连接词(如 "capability.As" -> "capability.")
                    line_text = re.sub(
                        r"(?<=[.!?])(?:As|The|We|While|However|Moreover|Additionally|Then)$",
                        "",
                        line_text,
                    )
                if len(text) + len(line_text) > _MAX_CAPTION_CHARS:
                    break
                text += " " + line_text
                cap_x1 = max(cap_x1, nxt["x1"])
                last_bottom = nxt["bottom"]
                collected += 1
                if ref:
                    break
            # 首行按词间隙截断后仍在单栏内(或首行本身混入了另一栏文字)时,
            # 把 x1 钳制在该栏内 —— 续行/合并行可能把 x1 撑到另一栏。
            # 仅当图注确实起始于左栏(cap_x0 < mid)时才钳制; 右栏图注(如首页
            # Figure 1, 首词被词间隙截断)若误钳会把 x1 压到中线, 导致区域反向
            mid = page.width / 2.0
            first_line_x1 = first_words[-1]["x1"] if first_words else cap_x1
            if cap_x0 < mid and (first_line_x1 <= mid + 20 or len(first_words) < len(sub_words)):
                cap_x1 = min(cap_x1, mid - 4.0)
            # 正文引用行误判为图注(如 "Fig. 5. Then, we combine..." / "Table V. We find...")
            # 在入列前丢弃, 避免生成垃圾图表区域
            if _CAPTION_SENTENCE.match(text):
                continue
            # 表格 caption 只有标签无标题(如孤立的 "Table7")时丢弃——没有标题的
            # 表格区域对前端无用, 且多为正文引用行的残留
            if _TABLE_PREFIX.match(text) and re.fullmatch(
                r"\s*table\s*\d+[a-z]?\s*", text, re.IGNORECASE
            ):
                continue
            # 入列前重建可读空格(展示用途)
            text = _restore_caption_spacing(text)
            captions.append(
                {
                    "text": text,
                    "x0": cap_x0,
                    "y0": row["top"],
                    "x1": cap_x1,
                    "y1": last_bottom,
                }
            )
    deduped: list[dict[str, Any]] = []
    for cap in captions:
        if (
            deduped
            and abs(cap["y0"] - deduped[-1]["y0"]) < 4
            and abs(cap["x0"] - deduped[-1]["x0"]) < 4
        ):
            continue
        deduped.append(cap)
    return deduped


def _hlines_in_band(page: Any, band_top: float, band_bot: float) -> list[tuple[float, float, float]]:
    """标题下方区间内的横向规则线(三线表的顶线/中隔线/底线)。

    pdfplumber 的 page.lines 坐标可能来自页面底部(bottom-origin, 与文字坐标相反),
    这里对两种解释都尝试, 取候选更多者; 平局时取 y 更接近标题底部(规则线通常紧贴标题)者。
    返回 [(y_top, x0, x1), ...] 按 y 排序。
    """
    raw = [
        l
        for l in page.lines
        if abs(l["y0"] - l["y1"]) < 1 and (l["x1"] - l["x0"]) >= 30.0
    ]
    best: list[tuple[float, float, float]] = []
    for flip in (False, True):
        cands: list[tuple[float, float, float]] = []
        for l in raw:
            y = page.height - l["y0"] if flip else l["y0"]
            if band_top <= y <= band_bot:
                cands.append((y, float(l["x0"]), float(l["x1"])))
        if len(cands) > len(best):
            best = cands
        elif len(cands) == len(best) and cands and best:
            if min(c[0] for c in cands) < min(b[0] for b in best):
                best = cands
    best.sort(key=lambda c: c[0])
    return best


def _column_bounds(page: Any, cap: dict[str, Any]) -> tuple[float, float]:
    """标题所在栏的水平边界(避免把另一栏的文字裁进表格图)。"""
    mid = page.width / 2.0
    if float(cap["x1"]) <= mid + 20:
        return 0.0, mid - 4.0
    if float(cap["x0"]) >= mid - 20:
        return mid + 4.0, page.width - 8.0
    return 0.0, page.width - 8.0



def _next_caption_top(caps: list[dict[str, Any]], index: int) -> float | None:
    """同一栏中下一个标题的顶部 y(用于钳制表格下边界, 防止把下一张表/正文裁进来)。"""
    for j in range(index + 1, len(caps)):
        if abs(caps[j]["x0"] - caps[index]["x0"]) <= 40 and caps[j]["y0"] > caps[index]["y1"]:
            return caps[j]["y0"]
    return None


def _table_body_bottom(
    page: Any, y0: float, band_bot: float, left_col: bool, right_col: bool
) -> float | None:
    """表格数据行(数字密集或多列含数字)最后一个连续行的底部; 用于收紧表格下边界。"""
    mid = page.width / 2.0
    lines: dict[int, list[dict[str, Any]]] = {}
    for word in page.extract_words():
        key = round(word["top"] / 4)
        lines.setdefault(key, []).append(word)
    last: float | None = None
    neutral = 0
    for _key, words in sorted(lines.items()):
        words = sorted(words, key=lambda w: w["x0"])
        top = min(w["top"] for w in words)
        if top < y0 - 2:
            continue
        if top > band_bot:
            break
        # 只保留表格所在栏的词: 避免另一栏的图注/标题混入同一 y 桶, 误触发停止/误判簇数
        if left_col:
            col_words = [w for w in words if w["x0"] < mid]
        elif right_col:
            col_words = [w for w in words if w["x0"] >= mid]
        else:
            col_words = words
        if not col_words:
            continue
        text = "".join(w["text"] for w in col_words)
        if _HEADING.match(text) or _CAPTION.match(text):
            break
        digits = len(re.findall(r"\d", text))
        gaps = sum(
            1
            for i in range(1, len(col_words))
            if float(col_words[i]["x0"]) - float(col_words[i - 1]["x1"]) > 8
        )
        # 多列词簇(表头/数据行)即视为表格行; 正文段落是连续长行(单簇)
        # 多列词簇(表头/数据行)即视为表格行; 公式密集行(无空格)靠引用/数字识别;
        # 数字密度高(≥30% 字符为数字, 如 "27.12?0.4690.9891?0.0013...")也是表格行,
        # 长方法名行可能只有 1 个词间大间隙, 靠密度兜底; 长行(≥45 字符)低数字密度视为
        # 正文段落, 避免把正文的 [N] 引用误当表格行
        digit_frac = digits / len(text) if text else 0.0
        is_row = (
            gaps >= 2
            or digit_frac > 0.30
            or (len(text) < 45 and (re.search(r"\[\d+\]", text) or digits >= 3))
        )
        if is_row:
            last = max(w["bottom"] for w in col_words)
            neutral = 0
        else:
            # 短行/符号行(勾选标记、空行、表头)可能是表格数据的一部分, 不立即终止;
            # 只有长正文行(≥45 字符且字母数字较多)才视为表格结束
            alnum = sum(1 for ch in text if ch.isalnum())
            if len(text) >= 45 and alnum >= 20:
                break
            neutral += 1
            if neutral > 20:
                break
    return last

_CAPTION_LABEL = re.compile(r"^(?:fig(?:ure)?\.?|table|图|表)\b", re.IGNORECASE)
_CAPTION_NUM = re.compile(r"^[0-9IVXLCDM]+")


def _caption_words_inside(page: Any, region: dict[str, float]) -> list[dict[str, Any]]:
    """区域内属于其他图表图注的词(如 Figure 10 / TABLE IV 的标签+编号)。"""
    words = page.extract_words()
    hits: list[dict[str, Any]] = []
    for w in words:
        if not _CAPTION_LABEL.match(w["text"]):
            continue
        if w["x0"] < float(region["x0"]) - 2 or w["x1"] > float(region["x1"]) + 2:
            continue
        if w["top"] < float(region["y0"]) - 2 or w["bottom"] > float(region["y1"]) + 2:
            continue
        # 同行内紧邻的下一词必须是编号(Figure 10 / TABLE IV / 图 3)
        nxt = next(
            (
                n
                for n in words
                if abs(n["top"] - w["top"]) < 5
                and n["x0"] > w["x1"]
                and n["x0"] < w["x1"] + 60
            ),
            None,
        )
        if nxt is not None and _CAPTION_NUM.match(nxt["text"]):
            hits.append(w)
    return hits




def _self_heal_region(
    page: Any, cap: dict[str, Any], region: dict[str, float], is_table: bool
) -> dict[str, float]:
    """检测期自愈: 区域内混入其他图表的图注文字或正文时自动收紧边界。

    覆盖两类常见缺陷: ① 图区域顶部吞进前一张图的图注文字;
    ② 表格区域底部伸进下方正文。对任何版式的论文都生效, 不依赖具体个案。
    """
    if is_table:
        mid = page.width / 2.0
        left_col = float(cap["x1"]) <= mid + 20
        right_col = float(cap["x0"]) >= mid - 20
        body_bottom = _table_body_bottom(
            page, float(region["y0"]), float(region["y1"]), left_col, right_col
        )
        if body_bottom is not None and body_bottom + 25 < float(region["y1"]):
            return {**region, "y1": body_bottom + 2.0}
        return region
    # 图: 区域内出现其他图表图注文字(Figure 10 / TABLE IV 等)时, 上界压到整个图注块之下
    y0 = float(region["y0"])
    cap_words = _caption_words_inside(page, region)
    if cap_words:
        label_top = min(w["top"] for w in cap_words)
        # 区域内图注是当前图自己的图注(如图注嵌在图内、图内容延伸到图注之下)时,
        # 不做"压到图注之下"的裁剪——那会把图的实际内容裁掉
        if not (
            float(cap["y0"]) - 5 <= label_top <= float(cap["y1"]) + 5
        ):
            # 图注可能有多行: 从标签行向下收集行距紧凑(<14pt)的同栏文字, 底部即图注块底
            words = page.extract_words()
            block_words = [
                w
                for w in words
                if w["top"] >= label_top - 2
                and w["top"] <= float(region["y1"]) + 2
                and w["x0"] >= float(region["x0"]) - 2
                and w["x1"] <= float(region["x1"]) + 2
            ]
            block_words.sort(key=lambda w: w["top"])
            prev_bottom = label_top
            block_bottom = label_top
            for w in block_words:
                if w["top"] - prev_bottom > 14:
                    break
                block_bottom = max(block_bottom, w["bottom"])
                prev_bottom = w["bottom"]
            y0 = max(y0, block_bottom + 2.0)
    return {**region, "y0": y0}

def _table_region_above(
    page: Any, cap: dict[str, Any], upper_bound: float, col_x0: float, col_x1: float
) -> dict[str, float] | None:
    """标题上方的表格区域(标题在表格下方的版式, 如 AAAI/部分期刊)。

    这类表格的三线底线通常在标题上方 10pt 左右, 网格表的底边也紧贴标题。
    只有找到紧贴标题上方的表格证据(≥2 条同栏规则线且底线距标题 ≤28pt,
    或底边距标题 ≤20pt 的网格)才启用, 避免把正文误当表格。
    """
    cap_top = float(cap["y0"])
    # 表格可能被其他表注/表格隔开(如 Table4 表格在上方 90pt 处), 上界放宽到标题上方 320pt,
    # 靠"表格行验证 + 跳过上方有表注的簇"来定位, 而不是依赖紧贴距离
    band_top = max(0.0, cap_top - 320.0)
    band_bot = cap_top - 1.0
    if band_bot <= band_top:
        return None
    mid = page.width / 2.0
    left_col = float(cap["x1"]) <= mid + 20
    right_col = float(cap["x0"]) >= mid - 20
    rules = _hlines_in_band(page, band_top, band_bot)
    # 只保留与标题同栏的规则线(避免另一栏表格的规则线干扰 y 定界):
    # 左栏保留左边界在左栏的线, 右栏保留右边界在右栏的线(含通栏表格, 如 Table4 跨两栏)
    if left_col:
        rules = [r for r in rules if r[1] < mid]
    elif right_col:
        rules = [r for r in rules if r[2] >= mid]
    if len(rules) < 2:
        return None
    # 用"表格表注标签(Table N)"的 y 位置把规则线分段: 每段属于同一张表。
    # (如 Table4 的表格上方 90pt 处有 Table3 的表注/表格, 标签 552 把规则线分成两段)
    words = page.extract_words()
    separators: list[float] = []
    for i, w in enumerate(words):
        if re.match(r"^(?:table|表)", w["text"], re.I) and band_top - 2 <= w["top"] <= band_bot + 2:
            nxt = next(
                (n for n in words if abs(n["top"] - w["top"]) < 5 and n["x0"] > w["x1"] and n["x0"] < w["x1"] + 60),
                None,
            )
            if nxt and re.match(r"^\d+[a-z]?$|^[IVXLCDM]+$", nxt["text"]):
                separators.append(w["top"])
    separators.sort()
    # 按分隔点把规则线切成若干段
    segments: list[list[tuple[float, float, float]]] = []
    current: list[tuple[float, float, float]] = []
    for r in sorted(rules):
        while separators and r[0] > separators[0]:
            if current:
                segments.append(current)
                current = []
            separators.pop(0)
        current.append(r)
    if current:
        segments.append(current)
    # 从下往上选段: 距标题 ≤120pt, 段内有表格数据行, 且段上方 100pt 内无其他表格表注标签
    for segment in reversed(segments):
        top = min(r[0] for r in segment)
        bottom = max(r[0] for r in segment)
        if cap_top - bottom > 120:
            continue
        if not _band_has_table_rows(page, top - 5, bottom + 5, left_col, right_col):
            continue
        probe = {
            "x0": col_x0,
            "y0": max(0.0, top - 100.0),
            "x1": col_x1,
            "y1": top,
        }
        cap_words = _caption_words_inside(page, probe)
        # 只把"表格表注"标签视为另一张表的障碍, 图注(Figure)不影响
        if any(w["text"].lower().startswith(("table", "表")) for w in cap_words):
            continue
        y0 = max(band_top, top - 1.0)
        y1 = min(cap_top - 2.0, bottom + 2.0)
        rx0 = min(r[1] for r in segment)
        rx1 = max(r[2] for r in segment)
        wx0, wx1 = rx0, rx1
        for w in words:
            if w["top"] < y0 - 1 or w["bottom"] > y1 + 1:
                continue
            if left_col and w["x0"] >= mid:
                continue
            if right_col and w["x1"] <= mid:
                continue
            wx0 = min(wx0, float(w["x0"]))
            wx1 = max(wx1, float(w["x1"]))
        return {
            "x0": max(col_x0, min(rx0, wx0) - 6.0),
            "y0": y0,
            "x1": min(col_x1, max(rx1, wx1) + 6.0),
            "y1": y1,
        }
    # 无规则线: 网格表(底边紧贴标题)
    for table in page.find_tables():
        x0, y0, x1, y1 = table.bbox
        if (x1 - x0) * (y1 - y0) < _MIN_TABLE_AREA:
            continue
        if y1 >= cap_top - 20 and y1 <= cap_top + 2:
            return {
                "x0": max(col_x0, x0),
                "y0": max(band_top, y0),
                "x1": min(col_x1, x1),
                "y1": min(cap_top - 2.0, y1),
            }
    return None




def _band_has_table_rows(
    page: Any, y0: float, y1: float, left_col: bool, right_col: bool
) -> bool:
    """区域内是否存在表格数据行(多列词簇或数字密集)。

    用于方向决策的内容验证: 表格下方/上方的规则线可能是正文段落线,
    只有区域内确有表格数据行才视为真正的表格证据, 避免正文误判为紧贴表格。
    """
    lines: dict[int, list[dict[str, Any]]] = {}
    for word in page.extract_words():
        key = round(word["top"] / 4)
        lines.setdefault(key, []).append(word)
    count = 0
    mid = page.width / 2.0
    for _key, words in sorted(lines.items()):
        words = sorted(words, key=lambda w: w["x0"])
        top = min(w["top"] for w in words)
        if top < y0 - 1 or top > y1:
            continue
        if left_col:
            col_words = [w for w in words if w["x0"] < mid]
        elif right_col:
            col_words = [w for w in words if w["x0"] >= mid]
        else:
            col_words = words
        if not col_words:
            continue
        text = "".join(w["text"] for w in col_words)
        digits = len(re.findall(r"\d", text))
        gaps = sum(
            1
            for i in range(1, len(col_words))
            if float(col_words[i]["x0"]) - float(col_words[i - 1]["x1"]) > 8
        )
        if gaps >= 2 or (len(text) < 45 and digits >= 3):
            count += 1
            if count >= 2:
                return True
    return False

def _table_full_width_extent(
    page: Any, band_top: float, band_bot: float
) -> tuple[float, float] | None:
    """检测通栏表格: 标题在单栏内但表格跨两栏(两栏排版常见)。

    返回表格实际 x 范围; 无跨栏证据返回 None(保持按标题所在栏钳制)。
    证据(任一): ① find_tables 网格跨两栏; ② 横向规则线跨两栏(三线表顶/底线);
    ③ 同一 y 带内在左右两栏都出现表格数据行。
    """
    mid = page.width / 2.0
    # ① 网格表跨两栏
    for table in page.find_tables():
        x0, y0, x1, y1 = table.bbox
        if (x1 - x0) * (y1 - y0) < _MIN_TABLE_AREA:
            continue
        if y0 >= band_top - 4 and y1 <= band_bot + 4:
            if x0 < mid - 40 and x1 > mid + 40:
                return float(x0), float(x1)
    # ② 横向规则线跨两栏(pdfplumber lines 可能是 bottom-origin, 两种 y 都试)
    for l in page.lines:
        if abs(l["y0"] - l["y1"]) < 1 and (l["x1"] - l["x0"]) >= 30.0:
            if l["x0"] < mid - 40 and l["x1"] > mid + 40:
                y_lo, y_hi = l["y0"], page.height - l["y0"]
                if (band_top - 2 <= y_lo <= band_bot + 2) or (
                    band_top - 2 <= y_hi <= band_bot + 2
                ):
                    return float(l["x0"]), float(l["x1"])
    # ③ 双栏同带都有表格数据行(数字密集或多列词簇)——无绘制边框的通栏表靠它识别。
    # 但左右两侧各有独立规则簇(如 AMCANet 并排两张单栏表)时不触发: 那是两表, 不是通栏。
    left_cluster = False
    right_cluster = False
    for l in page.lines:
        if abs(l["y0"] - l["y1"]) >= 1 or (l["x1"] - l["x0"]) < 30.0:
            continue
        y_lo, y_hi = l["y0"], page.height - l["y0"]
        if not ((band_top - 2 <= y_lo <= band_bot + 2) or (band_top - 2 <= y_hi <= band_bot + 2)):
            continue
        if l["x1"] < mid - 5:
            left_cluster = True
        elif l["x0"] > mid + 5:
            right_cluster = True
    if left_cluster and right_cluster:
        return None
    lines: dict[int, list[dict[str, Any]]] = {}
    for word in page.extract_words():
        key = round(word["top"] / 4)
        lines.setdefault(key, []).append(word)
    left_rows = 0
    right_rows = 0
    left_min: float | None = None
    right_max: float | None = None
    for _key, words in sorted(lines.items()):
        words = sorted(words, key=lambda w: w["x0"])
        top = min(w["top"] for w in words)
        if top < band_top - 1 or top > band_bot:
            continue
        left = [w for w in words if w["x1"] < mid - 10]
        right = [w for w in words if w["x0"] > mid + 10]
        for col_words, side in ((left, "l"), (right, "r")):
            if not col_words:
                continue
            text = "".join(w["text"] for w in col_words)
            digits = len(re.findall(r"\d", text))
            gaps = sum(
                1
                for i in range(1, len(col_words))
                if float(col_words[i]["x0"]) - float(col_words[i - 1]["x1"]) > 8
            )
            if not (gaps >= 2 or (len(text) < 45 and digits >= 3)):
                continue
            if side == "l":
                left_rows += 1
                x = min(w["x0"] for w in left)
                left_min = x if left_min is None else min(left_min, x)
            else:
                right_rows += 1
                x = max(w["x1"] for w in right)
                right_max = x if right_max is None else max(right_max, x)
    if left_rows >= 2 and right_rows >= 2 and left_min is not None and right_max is not None:
        return max(0.0, left_min - 6.0), min(page.width - 8.0, right_max + 6.0)
    return None


def _table_region(
    page: Any, cap: dict[str, Any], next_caption_top: float | None, upper_bound: float = 0.0
) -> dict[str, float]:
    """表格区域:优先用标题下方的三线表规则线定界。

    - 规则线簇给出 y 上下界(顶线到底线)与 x 范围, 并用表格文字微调 x;
    - 下边界硬性钳制在下一个同栏标题之前, 上边界在完整标题底部之下;
    - x 范围钳制在标题所在栏内, 避免裁入另一栏文字;
    - 无规则线时回退到表格网格检测 / 固定高度 + 结构钳制。
    """
    band_top = float(cap["y1"]) + 1.0
    band_bot = (float(next_caption_top) - 4.0) if next_caption_top else page.height - 8.0
    col_x0, col_x1 = _column_bounds(page, cap)
    # 通栏表格: 标题在单栏内但表格跨两栏——按标题所在栏钳制 x 会把表裁成一半,
    # 检测到跨栏证据时改用表格实际全宽范围
    full_extent = _table_full_width_extent(page, band_top, band_bot)
    is_full_width = full_extent is not None
    if is_full_width:
        col_x0, col_x1 = full_extent
    rules = _hlines_in_band(page, band_top, band_bot)
    # 表格网格检测(find_tables)作为候选: 对带单元格结构的网格表更准确
    grid: dict[str, float] | None = None
    for table in page.find_tables():
        x0, y0, x1, y1 = table.bbox
        if (x1 - x0) * (y1 - y0) < _MIN_TABLE_AREA:
            continue
        if y0 >= band_top - 4 and y0 <= band_top + 80:
            if grid is None or y0 < grid["y0"]:
                grid = {"x0": x0, "y0": y0, "x1": x1, "y1": y1}
    # 标题在表格下方(如 AAAI/部分期刊): 表格在标题上方, 下方只有正文/章节线。
    # 只有下方缺少紧贴标题的表格证据(网格紧贴或规则线首条距标题 ≤30pt)时才启用上方候选,
    # 避免 IEEE 常规版式(标题在表格上方)被误切。
    above_region = _table_region_above(page, cap, upper_bound, col_x0, col_x1)
    mid = page.width / 2.0
    left_col = float(cap["x1"]) <= mid + 20
    right_col = float(cap["x0"]) >= mid - 20
    # 下方规则线必须是真正的表格证据(区域内确有表格数据行), 正文段落线不算:
    # 正文里可能有下划线/分隔线紧贴标题下方, 若误判为紧贴表格会错过"标题在表格下方"的上方候选
    below_tight = (
        (grid is not None and abs(grid["y0"] - band_top) <= 20)
        or (
            len(rules) >= 2
            and min(r[0] for r in rules) - band_top <= 30
            and _band_has_table_rows(
                page, band_top, min(band_bot, band_top + 220), left_col, right_col
            )
        )
    )
    if above_region is not None and not below_tight:
        return {**above_region, "dir": "above"}
    # 网格结果紧贴图注下方(y0 距 band_top 20pt 内)时优先: 网格表的分行更准,
    # 避免规则线把下方正文/其他图表的内容吸进来
    if grid is not None and abs(grid["y0"] - band_top) <= 20:
        if is_full_width:
            return {
                "x0": grid["x0"],
                "y0": grid["y0"],
                "x1": grid["x1"],
                "y1": min(grid["y1"], band_bot),
            }
        return {
            "x0": max(col_x0, grid["x0"]),
            "y0": grid["y0"],
            "x1": min(col_x1, grid["x1"]),
            "y1": min(grid["y1"], band_bot),
        }
    if len(rules) >= 2:
        y0 = max(band_top, min(r[0] for r in rules) - 1.0)
        y1 = max(r[0] for r in rules) + 2.0
        rx0 = min(r[1] for r in rules)
        rx1 = max(r[2] for r in rules)
        # 网格结果与规则簇同起点、但更短(规则簇被下方其他内容污染越界)时, 优先网格结果
        if grid is not None and abs(grid["y0"] - y0) <= 12 and grid["y1"] < y1 - 5:
            return {
                "x0": max(col_x0, grid["x0"]),
                "y0": grid["y0"],
                "x1": min(col_x1, grid["x1"]),
                "y1": min(grid["y1"], band_bot),
            }
        # 规则线有时比内容窄, 用表格内文字扩展 x 范围(排除另一栏文字;
        # 通栏表格则不过滤另一栏——整个带宽内的词都属于这张表)
        mid = page.width / 2.0
        left_col = float(cap["x1"]) <= mid + 20
        right_col = float(cap["x0"]) >= mid - 20
        wx0, wx1 = rx0, rx1
        for w in page.extract_words():
            if w["top"] < y0 - 1 or w["bottom"] > y1 + 1:
                continue
            if not is_full_width:
                if left_col and w["x0"] >= mid:
                    continue
                if right_col and w["x1"] <= mid:
                    continue
            wx0 = min(wx0, float(w["x0"]))
            wx1 = max(wx1, float(w["x1"]))
        if is_full_width:
            # 通栏表格: 绘制规则线常比内容窄(内容左右溢出边框), 直接以文字内容
            # 扩展 x 范围, 不再按规则线/栏宽钳制
            return {
                "x0": max(0.0, min(rx0, wx0) - 6.0),
                "y0": y0,
                "x1": min(page.width - 8.0, max(rx1, wx1) + 6.0),
                "y1": y1,
            }
        return {
            "x0": max(col_x0, min(rx0, wx0) - 6.0),
            "y0": y0,
            "x1": min(col_x1, max(rx1, wx1) + 6.0),
            "y1": y1,
        }
    # 无规则线:使用表格网格检测
    if grid is not None:
        if is_full_width:
            return {
                "x0": grid["x0"],
                "y0": grid["y0"],
                "x1": grid["x1"],
                "y1": min(grid["y1"], band_bot),
            }
        return {
            "x0": max(col_x0, grid["x0"]),
            "y0": grid["y0"],
            "x1": min(col_x1, grid["x1"]),
            "y1": min(grid["y1"], band_bot),
        }
    # 最后回退:标题下方固定高度 + 结构钳制(不越过下一标题, 不越过所在栏)
    if is_full_width:
        return {
            "x0": col_x0,
            "y0": band_top,
            "x1": col_x1,
            "y1": min(band_top + 220.0, band_bot),
        }
    return {
        "x0": max(col_x0, float(cap["x0"]) - 6.0),
        "y0": band_top,
        "x1": min(col_x1, float(cap["x1"]) + 6.0),
        "y1": min(band_top + 220.0, band_bot),
    }


def _clamp_to_column(
    page: Any, cap: dict[str, Any], x0: float, x1: float
) -> tuple[float, float]:
    """把区域 x 范围钳制在标题所在栏内, 避免把另一栏的文字裁进来。

    要求图注的起止 x 都指向同一栏才钳制; 图注自身横跨中线或 x 异常时
    不钳制, 避免产生 x0>x1 的反向矩形。
    """
    mid = page.width / 2.0
    if float(cap["x1"]) <= mid + 20 and float(cap["x0"]) < mid:
        x1 = min(x1, mid - 2.0)
    elif float(cap["x0"]) >= mid - 20 and float(cap["x1"]) > mid:
        x0 = max(x0, mid + 2.0)
    return x0, x1

def _figure_region(page: Any, cap: dict[str, Any], upper_bound: float) -> dict[str, float]:
    """图标题上方图元(内嵌图片 + 矢量矩形)的并集;上界为同页上一个图表的标题底部。

    upper_bound 用于避免把同页上方其他图表的内容包进来(如 Fig.8 不能包含 Fig.7)。
    """
    objects: list[tuple[float, float, float, float]] = []
    for image in page.images:
        objects.append((image["x0"], image["top"], image["x1"], image["bottom"]))
    for rect in page.rects:
        objects.append((rect["x0"], rect["top"], rect["x1"], rect["bottom"]))
    # 矢量线/曲线(折线图、结构图等由路径绘制的图元):
    # pdfplumber 的 lines/curves 坐标与文字相反(自底向上), 翻转为 top-origin 后加入
    for raw in list(page.lines) + list(page.curves):
        y0 = page.height - raw["y1"]
        y1 = page.height - raw["y0"]
        if y0 < 0 or y1 > page.height or y1 <= 0:
            continue
        objects.append((raw["x0"], y0, raw["x1"], y1))
    above = [
        item
        for item in objects
        if item[1] < cap["y0"] - 2 and item[3] > 0 and item[3] > upper_bound
    ]
    if above:
        # 从图注向上聚拢相连图元: 同一张图的图元紧贴图注(间距 ≤ _FIGURE_OBJECT_GAP),
        # 页面头部装饰(logo/横幅/标题线)与图之间隔着大段空白, 在空隙处停止——
        # 避免首页图把整页(标题/摘要)裁进来(如 AMCANet Fig.1 曾渲染整页首页)。
        above.sort(key=lambda item: item[3], reverse=True)
        union_top = float(cap["y0"]) - 2
        union_bottom = union_top  # 吸收对象的最大底部: 图内容可能延伸到图注之下
        x0: float | None = None
        x1: float | None = None
        for item in above:
            if union_top - item[3] > _FIGURE_OBJECT_GAP:
                break
            union_top = min(union_top, item[1])
            union_bottom = max(union_bottom, item[3])
            x0 = item[0] if x0 is None else min(x0, item[0])
            x1 = item[2] if x1 is None else max(x1, item[2])
        if x0 is None or x1 is None:
            x0 = float(cap["x0"]) - _PAD
            x1 = float(cap["x1"]) + _PAD
        y0 = union_top
        # 图注嵌在图内(如图注下方还有图元/图框)时, 区域底边取内容实际底部,
        # 而不是停在图注处——否则下一张图的 upper_bound 偏低, 会吸入本图内容
        y1 = max(union_bottom, float(cap["y0"]) - 2)
        # upper_bound 用于避免包入前一图表, 但前一图表 bottom 含多行图注/跨栏干扰时
        # 可能深入当前图元(裁掉图的内容)。若 upper_bound 深入且该处无前一图表的图注文字,
        # 以当前图元上边界为准。
        if upper_bound > y0 + 4:
            probe = {
                "x0": max(0.0, min(x0, float(cap["x0"])) - _PAD),
                "y0": y0,
                "x1": min(page.width, max(x1, float(cap["x1"])) + _PAD),
                "y1": upper_bound + 8.0,
            }
            if not _caption_words_inside(page, probe):
                y0 = min(y0, upper_bound)
        else:
            y0 = max(y0, upper_bound)
        max_height = page.height * _MAX_FIGURE_HEIGHT_RATIO
        if y1 - y0 > max_height:
            y0 = y1 - max_height
        x0 = max(0.0, min(x0, float(cap["x0"])) - _PAD)
        x1 = min(page.width, max(x1, float(cap["x1"])) + _PAD)
        x0, x1 = _clamp_to_column(page, cap, x0, x1)
        return {
            "x0": x0,
            "y0": y0,
            "x1": x1,
            "y1": y1,
        }
    x0 = max(0.0, float(cap["x0"]) - _PAD)
    x1 = min(page.width, float(cap["x1"]) + _PAD)
    x0, x1 = _clamp_to_column(page, cap, x0, x1)
    return {
        "x0": x0,
        "y0": max(upper_bound, float(cap["y0"]) - page.height * 0.4),
        "x1": x1,
        "y1": float(cap["y0"]) - 2,
    }




def _region_has_table_rows(
    page: Any, region: dict[str, float], cap: dict[str, Any]
) -> bool:
    """表格区域内容验证: 区域内是否有 ≥2 行表格数据行。

    作为检测结果的运行时质量门槛——方向决策/规则线/网格无论怎么偏差,
    只要区域内没有真实表格行(把正文裁进来了), 就会被判定为不可信,
    触发内容自愈(尝试其他候选)。
    """
    mid = page.width / 2.0
    left_col = float(cap["x1"]) <= mid + 20
    right_col = float(cap["x0"]) >= mid - 20
    return _band_has_table_rows(
        page, float(region["y0"]), float(region["y1"]), left_col, right_col
    )

def detect_figures(pdf_path: str | Path) -> list[FigureRegion]:
    """按页检测图/表区域,按页序、页内纵向顺序返回。"""
    results: list[FigureRegion] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            previous: dict[str, Any] | None = None
            left_bottom: float = 0.0
            right_bottom: float = 0.0
            full_bottom: float = 0.0
            mid = page.width / 2.0
            caps = _line_captions(page)
            for index, cap in enumerate(caps):
                if previous and abs(cap["y0"] - previous["y0"]) < 4 and abs(cap["x0"] - previous["x0"]) < 4:
                    continue
                previous = cap
                # 所在栏: 左/右/通栏, 上界只受同栏(及通栏)前项约束, 避免跨栏互相顶掉
                if float(cap["x1"]) <= mid + 20:
                    col = "left"
                elif float(cap["x0"]) >= mid - 20:
                    col = "right"
                else:
                    col = "full"
                upper = max(
                    full_bottom,
                    left_bottom if col == "left" else 0.0,
                    right_bottom if col == "right" else 0.0,
                )
                is_table = bool(_TABLE_PREFIX.match(str(cap["text"])))
                region = (
                    _table_region(page, cap, _next_caption_top(caps, index), upper)
                    if is_table
                    else _figure_region(page, cap, upper)
                )
                # 区域钳制到页面边界内并规整顺序: 页面顶部的越界图元(如部分期刊
                # 的页眉图 top 为负)会让 y0 变成负数, 反向矩形也在此统一修正
                region = {
                    "x0": min(max(0.0, float(region["x0"])), float(page.width)),
                    "y0": min(max(0.0, float(region["y0"])), float(page.height)),
                    "x1": min(max(0.0, float(region["x1"])), float(page.width)),
                    "y1": min(max(0.0, float(region["y1"])), float(page.height)),
                }
                if region["x0"] > region["x1"]:
                    region["x0"], region["x1"] = region["x1"], region["x0"]
                if region["y0"] > region["y1"]:
                    region["y0"], region["y1"] = region["y1"], region["y0"]
                # 表格区域按方向钳制: 标题在表格上方时从完整图注底部之下开始;
                # 标题在表格下方时止于标题顶部之上(避免把图注文字裁进去)
                if is_table:
                    if region.get("dir") == "above":
                        region = {
                            **region,
                            "y1": min(float(region["y1"]), float(cap["y0"]) - 2),
                        }
                        region.pop("dir", None)
                    else:
                        region = {**region, "y0": max(float(region["y0"]), float(cap["y1"]) + 2)}
                # 通用自愈: 区域内混入其他图表图注/正文时自动收紧(对任意版式生效)
                region = _self_heal_region(page, cap, region, is_table)
                # 内容自愈(表格): 区域必须真的有表格数据行。方向决策/规则线/网格无论怎么
                # 偏差, 只要把正文裁进来了(区域内无表格行), 就尝试反向候选与网格表,
                # 全部失败才保留原区域(由 audit 打 warning 标记)。
                if is_table and not _region_has_table_rows(page, region, cap):
                    col_x0, col_x1 = _column_bounds(page, cap)
                    healed: dict[str, float] | None = None
                    above = _table_region_above(page, cap, upper, col_x0, col_x1)
                    if above is not None and _region_has_table_rows(page, above, cap):
                        healed = {
                            **above,
                            "y1": min(float(above["y1"]), float(cap["y0"]) - 2),
                        }
                    if healed is None:
                        for table in page.find_tables():
                            x0, y0, x1, y1 = table.bbox
                            if (x1 - x0) * (y1 - y0) < _MIN_TABLE_AREA:
                                continue
                            cand = {"x0": x0, "y0": y0, "x1": x1, "y1": y1}
                            if _region_has_table_rows(page, cand, cap):
                                healed = cand
                                break
                    if healed is not None:
                        region = healed
                # 同栏/通栏前项底部作为后续项上界, 避免把上方图表的内容包进来;
                # 上界要覆盖前项的完整图注(图注文字可能伸到区域底部之下)
                bottom = max(float(region["y1"]), float(cap["y1"]))
                if col == "left":
                    left_bottom = max(left_bottom, bottom)
                elif col == "right":
                    right_bottom = max(right_bottom, bottom)
                else:
                    full_bottom = max(full_bottom, bottom)
                previous_bottom = max(full_bottom, left_bottom, right_bottom)
                # 最终钳制到页面边界并规整顺序(自愈等步骤可能再产生越界/反向矩形)
                region = {
                    "x0": min(max(0.0, float(region["x0"])), float(page.width)),
                    "y0": min(max(0.0, float(region["y0"])), float(page.height)),
                    "x1": min(max(0.0, float(region["x1"])), float(page.width)),
                    "y1": min(max(0.0, float(region["y1"])), float(page.height)),
                }
                if region["x0"] > region["x1"]:
                    region["x0"], region["x1"] = region["x1"], region["x0"]
                if region["y0"] > region["y1"]:
                    region["y0"], region["y1"] = region["y1"], region["y0"]
                results.append(
                    FigureRegion(
                        page=page_number,
                        kind="table" if is_table else "figure",
                        caption=_restore_caption_spacing(str(cap["text"])),
                        x0=region["x0"],
                        y0=region["y0"],
                        x1=region["x1"],
                        y1=region["y1"],
                    )
                )
    return results


def _find_mineru_cmd() -> str:
    """定位 mineru CLI: 优先当前解释器所在环境的 Scripts 目录。

    不能依赖 PATH——系统 PATH 上可能有 base 等其它环境的 mineru.exe
    (torch 版本/可用性不符), 必须用本环境(有可用 torch)的 mineru。
    """
    import shutil
    import sys

    exe = Path(sys.executable).parent / "Scripts" / "mineru.exe"
    if exe.exists():
        return str(exe)
    which = shutil.which("mineru")
    return which or str(exe) or "mineru"


# 常驻 mineru API 服务端单例: 模型加载一次, 多篇论文复用;
# 每次调用都起新服务端会重复加载模型(数 GB GPU), 且生命周期易崩
_MINERU_SERVER: dict[str, Any] = {"proc": None, "port": None, "tmp": None}


def _mineru_server_base_url() -> str | None:
    """获取常驻 mineru 服务端地址(不存在则启动), 失败返回 None。"""
    import socket
    import sys
    import time
    import urllib.request

    global _MINERU_SERVER
    proc = _MINERU_SERVER.get("proc")
    if proc is not None and proc.poll() is None:
        return f"http://127.0.0.1:{_MINERU_SERVER['port']}"
    try:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        tmp = Path(tempfile.mkdtemp(prefix="mineru_srv_"))
        env = dict(os.environ)
        env["PATH"] = (
            str(Path(sys.executable).parent / "Scripts")
            + os.pathsep
            + str(Path(sys.executable).parent)
            + os.pathsep
            + env.get("PATH", "")
        )
        env["MINERU_API_OUTPUT_ROOT"] = str(tmp)
        # 限制单批处理页数, 降低大论文(15+ 页)在 8GB 显卡上的显存峰值, 减少服务端崩溃
        env["MINERU_PROCESSING_WINDOW_SIZE"] = "8"
        kwargs: dict[str, Any] = {"env": env}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        server = subprocess.Popen(
            [sys.executable, "-m", "mineru.cli.fast_api", "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs,
        )
        # 健康检查轮询(最多 ~120s; 首次含模型相关初始化)
        for _ in range(120):
            if server.poll() is not None:
                break
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/health", timeout=2
                ) as resp:
                    if resp.status == 200:
                        _MINERU_SERVER = {"proc": server, "port": port, "tmp": tmp}
                        import atexit

                        atexit.register(_shutdown_mineru_server)
                        return f"http://127.0.0.1:{port}"
            except Exception:
                pass
            time.sleep(1)
        try:
            server.kill()
        except Exception:
            pass
        logger.warning("MinerU 服务端健康检查超时")
        return None
    except Exception as exc:
        logger.warning(f"MinerU 服务端启动失败: {exc}")
        return None


def _shutdown_mineru_server() -> None:
    """进程退出时关闭常驻 mineru 服务端。"""
    global _MINERU_SERVER
    proc = _MINERU_SERVER.get("proc")
    if proc is not None:
        try:
            proc.kill()
        except Exception:
            pass
    tmp = _MINERU_SERVER.get("tmp")
    if tmp is not None:
        try:
            _shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass
    _MINERU_SERVER = {"proc": None, "port": None, "tmp": None}


def _run_mineru_inprocess(pdf_path: str | Path, out_dir: str | Path) -> Any | None:
    """在**当前进程**内运行 MinerU pipeline(不启动本地 HTTP 服务端)。

    与 CLI 客户端-服务端模式相比, 进程内模式:
    - 不依赖 localhost HTTP(健康检查/端口绑定/服务端生命周期全部消失);
    - 模型复用当前进程的 ModelSingleton 缓存, 多次调用不重复加载;
    - 规避了部分环境(沙箱/代理)拦截本机 HTTP 导致的"服务端健康检查超时"问题。

    失败返回 None, 由调用方回退 CLI 或启发式。
    """
    import os as _os

    try:
        from mineru.cli.common import do_parse
    except Exception as exc:
        logger.warning(f"MinerU 进程内解析不可用: {exc}")
        return None
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    # 限制单批处理页数, 降低大论文(15+ 页)在 8GB 显卡上的显存峰值
    _os.environ["MINERU_PROCESSING_WINDOW_SIZE"] = "8"
    try:
        do_parse(
            output_dir=str(out),
            pdf_file_names=[Path(pdf_path).name],
            pdf_bytes_list=[Path(pdf_path).read_bytes()],
            p_lang_list=["ch"],
            backend="pipeline",
            parse_method="auto",
            formula_enable=True,
            table_enable=True,
            f_draw_layout_bbox=False,
            f_draw_span_bbox=False,
            f_dump_md=False,
            f_dump_middle_json=True,
            f_dump_model_output=False,
            f_dump_orig_pdf=False,
            f_dump_content_list=True,
        )
    except Exception as exc:
        logger.warning(f"MinerU 进程内解析失败: {exc}")
        return None
    files = sorted(Path(out).rglob("*content_list.json"))
    if not files:
        logger.warning("MinerU 进程内解析未生成 content_list.json")
        return None
    import json as _json

    with open(files[0], encoding="utf-8") as f:
        return _json.load(f)


def run_mineru_content(
    pdf_path: str | Path,
    timeout: int = 1800,
) -> Any | None:
    """运行 MinerU 解析 PDF, 返回 content_list 数据(章节标题与图表区域共用)。

    优先**进程内** pipeline(不依赖本地 HTTP 服务端, 稳定), 失败回退 CLI 客户端-服务端
    模式; 再失败返回 None, 由调用方回退启发式。

    注意: 论文文件名可能超长(>100 字符), mineru 输出路径含文件名会突破
    Windows MAX_PATH(260)限制导致写入失败, 因此先把 PDF 复制为短文件名再解析。
    """
    import json as _json
    import os as _os
    import shutil as _shutil
    import subprocess as _subprocess
    import tempfile as _tempfile

    pdf = Path(pdf_path)
    if not pdf.exists():
        return None
    out = Path(_tempfile.mkdtemp(prefix="mineru_paper_"))
    work = Path(_tempfile.mkdtemp(prefix="mineru_in_"))
    try:
        # 短文件名副本: 规避 Windows MAX_PATH(260 字符)限制
        short_pdf = work / f"paper_{abs(hash(str(pdf))) % (10 ** 8)}.pdf"
        _shutil.copy2(pdf, short_pdf)
        # 1) 进程内模式(首选): 无 HTTP 依赖, 不受本机 HTTP 拦截影响
        data = _run_mineru_inprocess(short_pdf, out)
        if data is not None:
            return data
        # 2) CLI 客户端-服务端模式(回退): 自起常驻服务端, 客户端仅上传与取回
        # 必须确保本解释器所在环境(有可用 torch)优先, 避免落到 base 等 torch 损坏环境
        _sys = __import__("sys")
        _env = dict(_os.environ)
        _scripts = str(Path(_sys.executable).parent / "Scripts")
        _bindir = str(Path(_sys.executable).parent)
        _env["PATH"] = _scripts + _os.pathsep + _bindir + _os.pathsep + _env.get("PATH", "")
        for attempt in range(2):
            try:
                base_url = _mineru_server_base_url()
                if base_url is None:
                    return None
                result = _subprocess.run(
                    [
                        _find_mineru_cmd(),
                        "-p",
                        str(short_pdf),
                        "-o",
                        str(out),
                        "--api-url",
                        base_url,
                        "-m",
                        "auto",
                        "-b",
                        "pipeline",
                    ],
                    capture_output=True,
                    timeout=timeout,
                    env=_env,
                )
                if result.returncode == 0:
                    break
                tail = result.stderr.decode("utf-8", errors="replace")[-300:]
                logger.warning(f"MinerU 解析失败(exit {result.returncode}): {tail}")
                if attempt == 0:
                    _shutdown_mineru_server()
                    continue
                return None
            except Exception as exc:
                logger.warning(f"MinerU 调用失败: {exc}")
                if attempt == 0:
                    _shutdown_mineru_server()
                    continue
                return None
        files = sorted(out.rglob("*content_list.json"))
        if not files:
            logger.warning("MinerU 未生成 content_list.json")
            return None
        with open(files[0], encoding="utf-8") as f:
            return _json.load(f)
    except Exception as exc:
        logger.warning(f"MinerU content_list 解析失败: {exc}")
        return None
    finally:
        _shutil.rmtree(out, ignore_errors=True)
        _shutil.rmtree(work, ignore_errors=True)


def detect_figures_mineru(
    pdf_path: str | Path,
    out_dir: str | Path | None = None,
    timeout: int = 1800,
) -> list[FigureRegion]:
    """用 MinerU 版面检测提取图/表区域(对任意 PDF 版式鲁棒)。

    运行 mineru CLI 生成 content_list.json, 解析 type 为 image/table 的块;
    失败回退由调用方处理(返回空列表)。
    """
    data = run_mineru_content(pdf_path, timeout=timeout)
    if not data:
        return []
    return _mineru_content_to_regions(pdf_path, data)


# 图注标签模式(用于从 MinerU 的 image_caption 中挑出真正的图注)
_FIG_CAPTION_LABEL = re.compile(
    r"^(fig(?:ure)?\.?|table)\s*(\d+[a-z]?|[IVXLCDM]+)", re.IGNORECASE
)
# 同一张多子图(Figure 4 的多个面板)之间允许的最大 y 间隙(归一化 0-1);
# 不同图之间通常隔着一行以上图注文字(≥30pt), 此处按 24pt 左右分隔
_FIGURE_PANEL_GAP = 0.03


_FIG_NUM_RE = re.compile(r"^(fig(?:ure)?\.?|table)\s*(\d+[a-z]?|[IVXLCDM]+)", re.IGNORECASE)


def _split_merged_mineru_figure(
    pdf_path: str | Path,
    page_number: int,
    bbox_pts: tuple[float, float, float, float],
    caps: list[str],
) -> list[tuple[tuple[float, float, float, float], str]]:
    """把 MinerU 合并的并排多图拆成左右几份。

    场景: 论文同一行并排两张图(如 Figure 8 左 + Figure 9 右), MinerU 版面模型
    把它们检成同一个 image 块, image_caption 列表里挂着多个 "Figure N" 标签。
    这里用 pdfplumber 找到每个标签文本在 PDF 中的 x 位置, 按 x 排序后把块
    按 caption 的 x 中位线切成左右几份, 每份配对应 caption。

    返回 [(sub_bbox_pts, caption), ...]; 无法拆分时返回 [(原bbox, 原caption)]。
    """
    labeled = [c for c in caps if _FIG_NUM_RE.match(c)]
    if len(labeled) < 2:
        return [(bbox_pts, _pick_mineru_caption("", caps))]
    try:
        import pdfplumber as _pdfplumber

        with _pdfplumber.open(str(pdf_path)) as pdf:
            page = pdf.pages[page_number - 1]
            words = page.extract_words()
    except Exception:
        return [(bbox_pts, _pick_mineru_caption("", caps))]
    # 每个 label 找其 caption 文本首个词的 x 位置
    positions: list[tuple[float, str]] = []
    for label in labeled:
        m = _FIG_NUM_RE.match(label)
        num = m.group(2)
        # 找 "Figure 8:" 或 "Fig. 8" 所在词的 x0
        found = None
        for i, w in enumerate(words):
            wt = w["text"].strip()
            if wt.lower().startswith(("figure", "fig.")) or wt.lower() == "fig":
                nxt = ""
                if i + 1 < len(words):
                    nxt = words[i + 1]["text"].strip()
                # 数字可能在同一词(Figure8:)或下一词(Figure 8:)
                cur = wt + nxt
                if re.match(rf"^(?:fig(?:ure)?\.?|table)\s*{re.escape(num)}", cur, re.I):
                    found = (float(w["x0"]) + float(w["x1"])) / 2.0
                    break
        if found is not None:
            positions.append((found, label))
    if len(positions) < 2:
        return [(bbox_pts, _pick_mineru_caption("", caps))]
    positions.sort()
    x0, y0, x1, y1 = bbox_pts
    results: list[tuple[tuple[float, float, float, float], str]] = []
    for idx, (cx, label) in enumerate(positions):
        # 左边界: 前一个 caption 的中点, 右边界: 下一个 caption 的中点
        left = positions[idx - 1][0] if idx > 0 else x0
        right = positions[idx + 1][0] if idx + 1 < len(positions) else x1
        mid_left = (left + cx) / 2.0
        mid_right = (cx + right) / 2.0
        sub_x0 = max(x0, mid_left)
        sub_x1 = min(x1, mid_right)
        if sub_x1 - sub_x0 < 10:
            continue
        results.append(((sub_x0, y0, sub_x1, y1), label))
    if not results:
        return [(bbox_pts, _pick_mineru_caption("", caps))]
    return results


def _merge_same_number_figures(regions: list[FigureRegion]) -> list[FigureRegion]:
    """合并同页同编号且位置相邻的图块(连通分量聚类)。

    场景: MinerU 把同一张多子图(如 Figure 1 的 3x2 面板)拆成多个 image 块,
    只有部分块带 caption。按"位置相邻"把同页图块聚成连通分量:
    - 分量内所有块必须有兼容编号(空 caption 或相同编号);
    - 不同编号的块(如并排 Figure 6 / Figure 7)绝不进同一分量;
    - 分量 bbox 取并集, caption 取分量内带编号/带空格的块。
    """
    def _adjacent(a: FigureRegion, b: FigureRegion) -> bool:
        y_gap = max(0.0, a.y0 - b.y1) if a.y0 > b.y1 else max(0.0, b.y0 - a.y1)
        x_overlap = min(a.x1, b.x1) - max(a.x0, b.x0)
        x_gap = max(0.0, a.x0 - b.x1) if a.x0 > b.x1 else max(0.0, b.x0 - a.x1)
        y_overlap = min(a.y1, b.y1) - max(a.y0, b.y0)
        return (y_gap <= 40 and x_overlap > 5) or (x_gap <= 8 and y_overlap > 5)

    def _number(reg: FigureRegion) -> str | None:
        m = _FIG_CAPTION_LABEL.match(reg.caption)
        return m.group(2).lower() if m else None

    figs = sorted(
        [r for r in regions if r.kind == "figure"],
        key=lambda r: (r.page, r.y0, r.x0),
    )
    # 同页连通分量聚类(泛洪): 相邻且编号兼容的块聚成一组
    groups: list[list[FigureRegion]] = []
    for reg in figs:
        rg = _number(reg)
        placed = False
        for g in groups:
            if any(x.page != reg.page for x in g):
                continue
            gnum = _number(g[0])
            # 编号兼容: 组无编号 或 reg 无编号 或编号相同
            if gnum is not None and rg is not None and gnum != rg:
                continue
            if any(_adjacent(reg, x) for x in g):
                g.append(reg)
                placed = True
                break
        if not placed:
            groups.append([reg])
    # 分组内继续传播: 用循环直到没有新成员(链式吸收)
    changed = True
    while changed:
        changed = False
        i = 0
        while i < len(groups):
            g = groups[i]
            j = i + 1
            while j < len(groups):
                g2 = groups[j]
                gnum = _number(g[0])
                g2num = _number(g2[0])
                if gnum is not None and g2num is not None and gnum != g2num:
                    j += 1
                    continue
                if any(a.page == b.page and _adjacent(a, b) for a in g for b in g2):
                    g.extend(g2)
                    groups.pop(j)
                    changed = True
                    continue
                j += 1
            i += 1
    # 生成合并后的 region
    merged: list[FigureRegion] = []
    for g in groups:
        x0 = min(x.x0 for x in g)
        y0 = min(x.y0 for x in g)
        x1 = max(x.x1 for x in g)
        y1 = max(x.y1 for x in g)
        cap = ""
        for x in sorted(g, key=lambda r: (-1 if " " in r.caption else 0, -len(r.caption))):
            if x.caption.strip():
                cap = x.caption
                break
        merged.append(
            FigureRegion(
                page=g[0].page,
                kind="figure",
                caption=cap,
                x0=x0,
                y0=y0,
                x1=x1,
                y1=y1,
            )
        )
    table_list = [r for r in regions if r.kind != "figure"]
    return sorted(
        merged + table_list,
        key=lambda r: (r.page, r.y0, r.x0),
    )

def _match_mineru_caption_to_block(
    pdf_path: str | Path,
    page_number: int,
    px0: float,
    py0: float,
    px1: float,
    py1: float,
    caps: list[str],
) -> str:
    """从 image 块的多个 caption 里挑与块位置匹配的那个。

    MinerU 有时把版面上相邻的两张图合并成一个 image 块(如 Figure 8 的 caption
    在 x=59-155, Figure 9 在 x=183-540, 但 MinerU 只检出 x=163-555 一个块,
    两个 caption 都挂在上面)。此时按 caption 文本在 PDF 中的 x 位置判定:
    只有 caption 中心落在块 x 范围内的才算这个块的图注, 其余丢弃
    (被漏检的那张图由启发式兜底单独补检)。
    """
    labeled = [c for c in caps if _FIG_NUM_RE.match(c)]
    if not labeled:
        return _pick_mineru_caption("", caps)
    if len(labeled) == 1:
        return labeled[0]
    try:
        import pdfplumber as _pdfplumber

        with _pdfplumber.open(str(pdf_path)) as pdf:
            page = pdf.pages[page_number - 1]
            words = page.extract_words()
    except Exception:
        return _pick_mineru_caption("", caps)
    best: tuple[float, str] | None = None
    for label in labeled:
        m = _FIG_NUM_RE.match(label)
        num = m.group(2)
        cx = None
        for i, w in enumerate(words):
            wt = w["text"].strip()
            if wt.lower().startswith(("figure", "fig.")) or wt.lower() == "fig":
                nxt = words[i + 1]["text"].strip() if i + 1 < len(words) else ""
                if re.match(rf"^(?:fig(?:ure)?\.?|table)\s*{re.escape(num)}", wt + nxt, re.I):
                    cx = (float(w["x0"]) + float(w["x1"])) / 2.0
                    break
        if cx is None:
            continue
        # 只考虑 caption 中心落在块 x 范围内(±12pt 容差)的
        if px0 - 12 <= cx <= px1 + 12:
            dist = abs(cx - (px0 + px1) / 2.0)
            if best is None or dist < best[0]:
                best = (dist, label)
    if best is not None:
        return best[1]
    return _pick_mineru_caption("", caps)


def _fill_missing_mineru_regions(
    pdf_path: str | Path, regions: list[FigureRegion]
) -> list[FigureRegion]:
    """补检 MinerU 漏掉的图/表: 用启发式图注行扫描每页, 找出未被任何
    region 覆盖的 Figure/Table 标签, 调用启发式 _figure_region/_table_region 补检。

    场景: 论文同一行并排两张图时, MinerU 版面模型可能只检出其中一张
    (另一张 caption 挂在检出块的 caption 列表里), 需按 caption 位置补漏。
    """
    import pdfplumber as _pdfplumber

    def _covered(page_number: int, cap_y: float, cap_x: float, cap_text: str) -> bool:
        # 已存在 region 的 caption 编号与候选相同 → 已处理(避免重复补检)
        m = _FIG_CAPTION_LABEL.match(cap_text)
        if m:
            num = m.group(2).lower()
            for reg in regions:
                if reg.page != page_number:
                    continue
                rm = _FIG_CAPTION_LABEL.match(reg.caption)
                if rm and rm.group(2).lower() == num:
                    return True
        for reg in regions:
            if reg.page != page_number:
                continue
            # region 覆盖该 caption 位置(含 20pt 容差)即视为已处理
            if (
                reg.y0 - 20 <= cap_y <= reg.y1 + 20
                and reg.x0 - 40 <= cap_x <= reg.x1 + 40
            ):
                return True
        return False

    added: list[FigureRegion] = []
    try:
        with _pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                page_number = page.page_number
                caps = _line_captions(page)
                mid = page.width / 2.0
                upper = 0.0
                for index, cap in enumerate(caps):
                    cap_y = (float(cap["y0"]) + float(cap["y1"])) / 2.0
                    cap_x = (float(cap["x0"]) + float(cap["x1"])) / 2.0
                    if _covered(page_number, cap_y, cap_x, str(cap["text"])):
                        continue
                    is_table = bool(_TABLE_PREFIX.match(str(cap["text"])))
                    if is_table:
                        region = _table_region(page, cap, _next_caption_top(caps, index), upper)
                    else:
                        region = _figure_region(page, cap, upper)
                    if not region:
                        continue
                    region = _self_heal_region(page, cap, region, is_table)
                    region = {
                        "x0": min(max(0.0, float(region["x0"])), float(page.width)),
                        "y0": min(max(0.0, float(region["y0"])), float(page.height)),
                        "x1": min(max(0.0, float(region["x1"])), float(page.width)),
                        "y1": min(max(0.0, float(region["y1"])), float(page.height)),
                    }
                    if region["x0"] > region["x1"]:
                        region["x0"], region["x1"] = region["x1"], region["x0"]
                    if region["y0"] > region["y1"]:
                        region["y0"], region["y1"] = region["y1"], region["y0"]
                    if (region["x1"] - region["x0"]) * (region["y1"] - region["y0"]) < 800:
                        continue
                    # 同行右侧邻居收窄: 并排多图时(MinerU 只检出一张), 本图
                    # 区域若与右侧已检出图同 y 范围重叠, 把右边界收到邻居左缘,
                    # 避免把邻居的内容裁进来(如 Figure 8 不能包进 Figure 9 左列)
                    for other in regions:
                        if other.page != page_number:
                            continue
                        if other.x0 <= region["x0"]:
                            continue
                        y_overlap = min(region["y1"], other.y1) - max(region["y0"], other.y0)
                        if y_overlap > 10 and other.x0 < region["x1"] - 10:
                            region["x1"] = min(region["x1"], other.x0 - 2.0)
                    if region["x1"] - region["x0"] < 10:
                        continue
                    added.append(
                        FigureRegion(
                            page=page_number,
                            kind="table" if is_table else "figure",
                            caption=_restore_caption_spacing(str(cap["text"])),
                            x0=region["x0"],
                            y0=region["y0"],
                            x1=region["x1"],
                            y1=region["y1"],
                        )
                    )
                    upper = max(upper, float(region["y1"]), float(cap["y1"]))
    except Exception:
        return regions
    if not added:
        return regions
    # 按 (page, y0) 排序插入, 保持全局阅读顺序
    return sorted(
        regions + added,
        key=lambda r: (r.page, r.y0, r.x0),
    )


def _mineru_content_to_regions(
    pdf_path: str | Path, data: Any
) -> list[FigureRegion]:
    """把 MinerU content_list.json 数据转换为 FigureRegion 列表。

    - type 为 image/chart 的块是图(按 MinerU 的 bbox 直接转换);
    - type 为 table 的块是表;
    - bbox 为 0-1000 归一化坐标(相对页面宽高), 转回 PDF 点坐标;
    - caption 取 image_caption/table_caption(MinerU 输出真实词间空格文本);
    - page_idx(0 基)转 1 基页码。

    原则: 以 MinerU 的版面检测为权威, 不叠加启发式"猜测"规则
    (合并/补检/归属匹配会破坏 MinerU 的正确输出, 如并排图被误合并)。
    caption 粘连/伪图表等由审查 agent(LLM)在后续环节兜底。
    """
    content_list = data if isinstance(data, list) else (data or {}).get("content_list") or []
    fig_entries: list[tuple[int, float, float, float, float, list[str]]] = []
    table_entries: list[tuple[int, float, float, float, float, list[str]]] = []
    for item in content_list:
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        if itype not in ("image", "chart", "table"):
            continue
        bbox = item.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        page_idx = int(item.get("page_idx", -1))
        if page_idx < 0:
            continue
        key = "table_caption" if itype == "table" else "image_caption"
        caps = [str(c).strip() for c in (item.get(key) or []) if str(c).strip()]
        norm = [float(v) / 1000.0 for v in bbox]
        x0n, y0n, x1n, y1n = norm
        if x1n - x0n < 0.001 or y1n - y0n < 0.001:
            continue
        # 过滤微小图标/装饰(如期刊 logo): 归一化面积 < 0.002(约 35×35pt)不是真实图表
        if (x1n - x0n) * (y1n - y0n) < 0.002:
            continue
        entry = (page_idx, y0n, y1n, x0n, x1n, caps)
        if itype == "table":
            table_entries.append(entry)
        else:
            fig_entries.append(entry)

    regions: list[FigureRegion] = []
    with pymupdf.open(str(pdf_path)) as doc:
        # 图: 按 MinerU 的 bbox 直接转换(同一 image 块内的多子图面板
        # 保持 MinerU 的合并结果; 不再做启发式聚拢/拆分)
        fig_entries.sort(key=lambda e: (e[0], e[1], e[2]))
        for page_idx, y0n, y1n, x0n, x1n, caps in fig_entries:
            page = doc[page_idx]
            regions.append(
                FigureRegion(
                    page=page_idx + 1,
                    kind="figure",
                    caption=_pick_mineru_caption("", caps),
                    x0=x0n * page.rect.width,
                    y0=y0n * page.rect.height,
                    x1=x1n * page.rect.width,
                    y1=y1n * page.rect.height,
                )
            )
        # 同页同编号合并: MinerU 把同一张多子图拆成多个 image 块时,
        # 合并回一张(仅编号一致才合并, 不合并不同编号的并排图)
        regions = _merge_same_number_figures(regions)
        # 表: 每项一个区域
        for page_idx, y0n, y1n, x0n, x1n, caps in table_entries:
            page = doc[page_idx]
            regions.append(
                FigureRegion(
                    page=page_idx + 1,
                    kind="table",
                    caption=_pick_mineru_caption("", caps),
                    x0=x0n * page.rect.width,
                    y0=y0n * page.rect.height,
                    x1=x1n * page.rect.width,
                    y1=y1n * page.rect.height,
                )
            )
    # 图注兜底: 无 caption 的图/表, 用启发式图注(如首页 Figure 1)
    try:
        _fill_mineru_captions(pdf_path, regions)
    except Exception as exc:
        logger.warning(f"MinerU 图注兜底失败: {exc}")
    # 过滤无图注的图片区域(作者简介人像/期刊 logo/装饰图): 真正的图/表都带 "Fig./Table"
    # 图注, 补图注仍为空的说明是不可引用的装饰性图片, 不放进图库。
    kept: list[FigureRegion] = []
    for reg in regions:
        if reg.kind == "figure" and not reg.caption.strip():
            logger.debug(
                f"MinerU 丢弃无图注图片: pg{reg.page} "
                f"[{reg.x0:.0f},{reg.y0:.0f},{reg.x1:.0f},{reg.y1:.0f}]"
            )
            continue
        kept.append(reg)
    return kept

def _pick_mineru_caption(current: str, candidates: list[str]) -> str:
    """从多个 caption 候选中挑图注: 优先编号最大者(子图面板常混入相邻图注)。

    候选里没有真正的 "Fig./Table N" 图注时返回空串(不拿图内标签如 "GT"/"CIDNet"
    当图注), 由 _fill_mineru_captions 用启发式就近补真实图注。
    """
    labeled = [
        c for c in candidates if _FIG_CAPTION_LABEL.match(c)
    ]
    if labeled:
        best = max(
            labeled,
            key=lambda c: int(_FIG_CAPTION_LABEL.match(c).group(2) or 0)
            if _FIG_CAPTION_LABEL.match(c).group(2).isdigit() else 0,
        )
        return best
    return current or ""


def _norm_y(page: Any, y_points: float) -> float:
    return y_points / page.rect.height


def _fill_mineru_captions(pdf_path: str | Path, regions: list[FigureRegion]) -> None:
    """对无图注的区域, 用启发式 _line_captions 就近补图注(处理首页等特殊版式)。"""
    import pdfplumber as _pdfplumber

    need = [r for r in regions if not r.caption.strip()]
    if not need:
        return
    with _pdfplumber.open(str(pdf_path)) as pdf:
        for r in need:
            page = pdf.pages[r.page - 1]
            caps = _line_captions(page)
            if not caps:
                continue
            mid_y = (r.y0 + r.y1) / 2.0
            best = min(
                caps,
                key=lambda c: abs(c["y0"] - mid_y),
            )
            if best["text"]:
                r.caption = best["text"]


def audit_regions(
    pdf_path: str | Path, regions: list[FigureRegion], min_words: int = 2
) -> list[dict[str, Any]]:
    """审查裁剪区域:单栏(左/右)图表的区域里是否混入了另一栏的正文文字。

    返回有问题的区域列表: [{"index", "kind", "caption", "page", "leaked", "sample"}]
    通栏区域(宽度超过半页)不检查; 同栏图内自带的标签文字不计为泄漏。
    """
    issues: list[dict[str, Any]] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for idx, reg in enumerate(regions):
            page = pdf.pages[reg.page - 1]
            mid = page.width / 2.0
            width = reg.x1 - reg.x0
            if width > mid:
                continue
            left_overlap = max(0.0, min(reg.x1, mid) - reg.x0)
            right_overlap = max(0.0, reg.x1 - max(reg.x0, mid))
            is_left = left_overlap >= right_overlap
            leaked: list[str] = []
            for w in page.extract_words():
                if w["top"] < reg.y0 - 1 or w["bottom"] > reg.y1 + 1:
                    continue
                cx = (w["x0"] + w["x1"]) / 2.0
                if cx < reg.x0 or cx > reg.x1:
                    continue
                if is_left and cx < mid:
                    continue
                if not is_left and cx > mid:
                    continue
                leaked.append(w["text"])
            if len(leaked) >= min_words:
                issues.append({
                    "index": idx + 1,
                    "kind": reg.kind,
                    "caption": reg.caption[:48],
                    "page": reg.page,
                    "leaked": len(leaked),
                    "sample": " ".join(leaked[:8]),
                })
            # 单栏区域横向越界(伸入另一栏 >4pt)也算泄漏风险
            if is_left and reg.x1 > mid + 4:
                issues.append({
                    "index": idx + 1,
                    "kind": reg.kind,
                    "caption": reg.caption[:48],
                    "page": reg.page,
                    "leaked": 0,
                    "sample": "region x1=%.1f crosses mid %.1f" % (reg.x1, mid),
                })
            if not is_left and reg.x0 < mid - 4:
                issues.append({
                    "index": idx + 1,
                    "kind": reg.kind,
                    "caption": reg.caption[:48],
                    "page": reg.page,
                    "leaked": 0,
                    "sample": "region x0=%.1f crosses mid %.1f" % (reg.x0, mid),
                })
            # 图区域里出现其他图表的图注文字(Figure 10 / TABLE IV) → 混入其他图注
            if reg.kind == "figure":
                caption_words = _caption_words_inside(
                    page, {"x0": reg.x0, "y0": reg.y0, "x1": reg.x1, "y1": reg.y1},
                )
                if caption_words:
                    issues.append({
                        "index": idx + 1,
                        "kind": "figure",
                        "caption": reg.caption[:48],
                        "page": reg.page,
                        "leaked": len(caption_words),
                        "sample": "figure contains other caption text: " + " ".join(w["text"] for w in caption_words[:4]),
                    })
    return issues

def render_region(pdf_path: str | Path, region: FigureRegion, out_path: str | Path, dpi: int = 150) -> Path:
    """把指定区域渲染为 PNG,返回输出路径。

    区域坐标可能因表格检测钳制逻辑出现 x0>x1 / y0>y1 的非法矩形,
    归一化后再渲染; 宽高过小的退化区域抛 ValueError, 由调用方逐区域
    容错(跳过该区域, 避免整篇论文的图表提取全部失败)。
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(str(pdf_path)) as doc:
        page = doc[region.page - 1]
        x0, x1 = min(region.x0, region.x1), max(region.x0, region.x1)
        y0, y1 = min(region.y0, region.y1), max(region.y0, region.y1)
        clip = pymupdf.Rect(x0, y0, x1, y1)
        if clip.width < 1 or clip.height < 1:
            raise ValueError(f"退化区域不可渲染: {clip}")
        pix = page.get_pixmap(clip=clip, dpi=dpi)
        pix.save(str(out))
    return out
