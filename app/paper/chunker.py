"""保留真实页码与字符位置的论文分块。"""

import re

from app.paper.schemas import PaperChunkData, PaperPage, PaperSectionData


def _section_for_page(page_number: int, sections: list[PaperSectionData]) -> str:
    for section in reversed(sections):
        if section.page_start <= page_number <= section.page_end:
            return section.title
    return "全文"


def _page_segments(
    page: PaperPage,
    sections: list[PaperSectionData],
) -> list[tuple[str, int, str]]:
    """按同一页中的章节标题切开正文，返回章节、原始偏移和文本。"""
    starts: list[tuple[int, PaperSectionData]] = []
    search_from = 0
    for section in sections:
        if section.page_start != page.page_number:
            continue
        position = page.text.find(section.title, search_from)
        match_len = len(section.title)
        if position < 0:
            # 排版层标题带空格(如 "Proposed Method"), 而页文本可能是连写("ProposedMethod"):
            # 用去空格后的标题定位, 兼容单复数/连字符的细微差异(子串搜索)
            compact = re.sub(r"\s+", "", section.title)
            if compact:
                position = page.text.find(compact, search_from)
                match_len = len(compact)
        if position < 0:
            position = page.text.find(section.title)
        if position >= 0:
            starts.append((position, section))
            search_from = position + match_len
    starts.sort(key=lambda item: item[0])

    if not starts:
        return [(_section_for_page(page.page_number, sections), 0, page.text)]

    result: list[tuple[str, int, str]] = []
    first_position = starts[0][0]
    previous = next(
        (item for item in reversed(sections) if item.page_start < page.page_number <= item.page_end),
        None,
    )
    if first_position > 0 and previous is not None:
        result.append((previous.title, 0, page.text[:first_position]))
    for index, (position, section) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(page.text)
        result.append((section.title, position, page.text[position:end]))
    return result


def audit_chunks(
    pages: list[PaperPage],
    chunks: list[PaperChunkData],
    sections: list[PaperSectionData] | None = None,
) -> list[str]:
    """校验分块章节归属: 按页内标题偏移重算每段的归属区间, 与 chunk 的 section 对照。

    返回问题描述列表(空 = 全部正确)。用于入库前自动审查, 发现"章节归属错位"
    (如前一章结尾被划给后一章)时打 warning, 避免翻译/问答引用错误的章节内容。
    """
    paper_sections = sections or []
    issues: list[str] = []
    for page in pages:
        segments = _page_segments(page, paper_sections)
        for title, seg_off, raw in segments:
            seg_end = seg_off + len(raw)
            for chunk in chunks:
                if chunk.page_start != page.page_number:
                    continue
                # 去掉首尾空白后的内容偏移与原始段偏移对齐(段是原始文本, chunk 内容已 strip)
                in_seg = (
                    chunk.char_start >= seg_off - 1
                    and chunk.char_end <= seg_end + 1
                )
                if in_seg and chunk.section != title:
                    issues.append(
                        "p%d %s: 偏移 [%d,%d) 落在段 [%s](%d-%d), 但归属 %s"
                        % (page.page_number, chunk.chunk_id, chunk.char_start, chunk.char_end,
                           title, seg_off, seg_end, chunk.section)
                    )
        # 覆盖检查: 该页 chunk 的章节必须属于该页的段集合(或整页回退)
        seg_titles = {title for title, _off, _raw in segments}
        for chunk in chunks:
            if chunk.page_start != page.page_number:
                continue
            if chunk.section not in seg_titles and chunk.section != "全文":
                issues.append(
                    "p%d %s: 归属 %s 不在该页段集合 %s"
                    % (page.page_number, chunk.chunk_id, chunk.section, sorted(seg_titles))
                )
    return issues



def chunk_pages(
    pages: list[PaperPage],
    paper_id: int,
    chunk_size: int = 1000,
    overlap: int = 120,
    sections: list[PaperSectionData] | None = None,
) -> list[PaperChunkData]:
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须大于 0")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap 必须在 0 和 chunk_size 之间")

    paper_sections = sections or []
    result: list[PaperChunkData] = []
    ordinal = 0
    step = chunk_size - overlap
    for page in pages:
        for section_title, segment_offset, raw_segment in _page_segments(page, paper_sections):
            text = raw_segment.strip()
            if not text:
                continue
            leading_trim = len(raw_segment) - len(raw_segment.lstrip())
            start = 0
            while start < len(text):
                end = min(start + chunk_size, len(text))
                content = text[start:end].strip()
                absolute_start = segment_offset + leading_trim + start
                absolute_end = segment_offset + leading_trim + end
                if content:
                    result.append(
                        PaperChunkData(
                            paper_id=paper_id,
                            chunk_id=f"paper-{paper_id}-chunk-{ordinal}",
                            section=section_title,
                            page_start=page.page_number,
                            page_end=page.page_number,
                            ordinal=ordinal,
                            char_start=absolute_start,
                            char_end=absolute_end,
                            content=content,
                            metadata={"page": page.page_number},
                        )
                    )
                    ordinal += 1
                if end >= len(text):
                    break
                start += step
    return result
