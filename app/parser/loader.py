"""
文件读取器

负责将磁盘文件读取为文本内容，生成 Document 对象。
支持的格式：.txt .md .pdf .html
"""

import re
from pathlib import Path

from loguru import logger

from app.parser.models import Document


def load_text(file_path: str | Path) -> str:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    return path.read_text(encoding="utf-8")


def load_markdown(file_path: str | Path) -> str:
    return load_text(file_path)


async def load_pdf(file_path: str | Path) -> str:
    """
    使用 MinerU 解析 PDF → Markdown，保留页码映射。

    调用 `mineru` CLI，输出到临时目录，读取生成的 .md 文件。
    """
    import asyncio, subprocess, tempfile, shutil

    path = Path(file_path)
    out_dir = Path(tempfile.mkdtemp(prefix="mineru_"))

    try:
        logger.info(f"开始解析: {path.name}")
        # 在线程池中运行同步 subprocess，不阻塞事件循环
        result = await asyncio.to_thread(
            subprocess.run,
            ["mineru", "-p", str(path), "-o", str(out_dir), "-m", "auto", "-b", "pipeline"],
            capture_output=False,
            timeout=3600,
        )
        if result.returncode != 0:
            raise RuntimeError(f"MinerU failed (exit {result.returncode})")

        stem = path.stem
        md_dir = out_dir / stem / "auto" / "pipeline"
        md_files = list(md_dir.glob("*.md")) if md_dir.exists() else []

        if not md_files:
            md_files = list(out_dir.rglob("*.md"))

        if not md_files:
            logger.warning("未生成 .md 文件，降级为 pdfplumber")
            return _load_pdf_fallback(str(path))

        md_path = md_files[0]
        content = md_path.read_text(encoding="utf-8")
        content = _clean_mineru_output(content)
        # 注入页码标记（用 pdfplumber 对齐）
        content = _inject_page_markers(str(path), content)
        logger.info(f"解析成功 → {md_path.name} ({len(content)} 字符)")
        return content

    except Exception as e:
        logger.warning(f"解析失败: {e}，降级为 pdfplumber")
        return _load_pdf_fallback(str(path))

    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def _inject_page_markers(pdf_path: str, content: str) -> str:
    """用 pdfplumber 提取页级文本，对齐后注入 [Page N] 标记到 MinerU 输出"""
    try:
        import pdfplumber

        markers: list[tuple[int, str]] = []  # [(position, marker)]
        with pdfplumber.open(pdf_path) as pdf:
            prev_end = 0
            for i, page in enumerate(pdf.pages, start=1):
                page_text = page.extract_text()
                if not page_text:
                    continue
                # 取每页前 80 个非空字符作为锚点
                anchor = re.sub(r"\s+", "", page_text)[:80]
                if len(anchor) < 10:
                    continue
                # 在 MinerU 输出中模糊匹配（找 anchor 出现位置）
                clean_content = re.sub(r"\s+", "", content)
                pos = clean_content.find(anchor, prev_end)
                if pos >= 0:
                    # 反向映射回原始 content 的位置
                    orig_pos = _map_clean_to_original(content, pos)
                    markers.append((orig_pos, f"[Page {i}]"))
                    prev_end = pos + len(anchor)

        # 按位置降序插入（从后往前避免偏移）
        markers.sort(reverse=True)
        for pos, marker in markers:
            content = content[:pos] + marker + " " + content[pos:]

    except Exception:
        pass
    return content


def _map_clean_to_original(original: str, clean_pos: int) -> int:
    """将去空白后的位置映射回原始文本位置"""
    count = 0
    for i, ch in enumerate(original):
        if not ch.isspace():
            if count == clean_pos:
                return i
            count += 1
    return min(clean_pos, len(original))


def _clean_mineru_output(text: str) -> str:
    """清洗 MinerU 输出的常见噪声"""
    # 移除连续空行（3+ → 2）
    text = re.sub(r"\n{3,}", "\n\n", text)
    # 移除行首行尾空格
    text = "\n".join(line.strip() for line in text.split("\n"))
    return text


def _load_pdf_fallback(file_path: str) -> str:
    """pdfplumber 作为 MinerU 失败时的降级方案"""
    import pdfplumber

    pages: list[str] = []
    with pdfplumber.open(file_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text()
            if text:
                text = re.sub(r" {2,}", " ", text)
                text = re.sub(r"\n{3,}", "\n\n", text)
                pages.append(f"[Page {i}]\n{text}\n[Page {i} end]")
    return "\n\n".join(pages)


def load_html(file_path: str | Path) -> str:
    """
    将 HTML 转换为 Markdown 文本，表格自动序列化为 Markdown table。

    使用 html2text 进行转换。
    """
    from html2text import HTML2Text

    path = Path(file_path)
    html = path.read_text(encoding="utf-8")

    converter = HTML2Text()
    converter.ignore_links = False
    converter.ignore_images = True
    converter.body_width = 0  # 不自动换行
    converter.tables = True   # 表格转 Markdown

    return converter.handle(html)


async def load_file(file_path: str | Path) -> Document:
    """
    根据文件扩展名分发到对应的读取器，返回 Document 对象。

    支持: .txt .md .pdf .html
    """
    path = Path(file_path)
    suffix = path.suffix.lower()

    loaders = {
        ".txt": load_text,
        ".md": load_markdown,
        ".pdf": load_pdf,
        ".html": load_html,
        ".htm": load_html,
    }

    if suffix not in loaders:
        supported = ", ".join(loaders.keys())
        raise ValueError(
            f"Unsupported file type: '{suffix}'. Supported: {supported}"
        )

    loader = loaders[suffix]
    content = await loader(file_path) if suffix == ".pdf" else loader(file_path)

    return Document(
        filename=path.name,
        content=content,
        metadata={
            "source": str(path.absolute()),
            "file_type": suffix,
            "file_size": path.stat().st_size,
        },
    )
