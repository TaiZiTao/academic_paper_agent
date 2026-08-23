"""存量论文 publication_year 回填脚本。

只补年份, 不重跑精读流程:
    1. 遍历 papers 表中 publication_year 为空的论文;
    2. 用 pdfplumber 读 PDF 第一页文本(data/papers/files/{stored_filename});
    3. 调 app.paper.parser.extract_publication_year(首页文本, "") 提取;
    4. 提取到 -> 写回 publication_year 并打印 id + 年份; 提取不到 -> 保持 NULL, 打印 skip。

用法(在项目根目录):
    venv\Scripts\python.exe -u scripts/backfill_years.py

说明:
    - 幂等: 已回填的论文跳过, 可重复执行;
    - 回填可逆: 把 publication_year 改回 NULL 即可;
    - Windows 控制台 GBK: 只往 stdout 打印 ASCII(id/年份/skip), 完整明细(含文件名)
      写入 data/papers/backfill_years.log(UTF-8)。
"""

import asyncio
import sys
from pathlib import Path

# 允许从任意工作目录运行: 把项目根加入模块搜索路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pdfplumber
from sqlalchemy import select

from app.config.settings import settings
from app.database.database import async_session
from app.models.paper import Paper
from app.paper.parser import extract_page_text, extract_publication_year, normalize_text

FILES_DIR = Path(settings.data_dir) / "papers" / "files"
LOG_PATH = Path(settings.data_dir) / "papers" / "backfill_years.log"


def read_first_page_text(pdf_path: Path) -> str:
    """用 pdfplumber 读 PDF 第一页文本(与精读解析同一通道)。"""
    with pdfplumber.open(pdf_path) as pdf:
        if not pdf.pages:
            return ""
        return normalize_text(extract_page_text(pdf.pages[0]))


async def main() -> int:
    log_lines: list[str] = []
    updated: list[tuple[int, int]] = []
    skipped: list[int] = []

    def log(msg: str) -> None:
        log_lines.append(msg)
        print(msg)

    async with async_session() as session:
        papers = (
            (await session.execute(
                select(Paper).where(Paper.publication_year.is_(None)).order_by(Paper.id)
            ))
            .scalars()
            .all()
        )
        log(f"papers with NULL publication_year: {len(papers)}")
        for paper in papers:
            pdf_path = FILES_DIR / paper.stored_filename
            if not pdf_path.exists():
                log(f"skip id={paper.id} reason=pdf_missing file={paper.stored_filename!r}")
                skipped.append(paper.id)
                continue
            try:
                first_page_text = read_first_page_text(pdf_path)
            except Exception as exc:
                log(f"skip id={paper.id} reason=pdf_read_error error={exc!r} file={paper.stored_filename!r}")
                skipped.append(paper.id)
                continue
            year = extract_publication_year(first_page_text, "")
            if year:
                paper.publication_year = year
                log(f"id={paper.id} year={year} file={paper.stored_filename!r}")
                updated.append((paper.id, year))
            else:
                log(f"skip id={paper.id} reason=no_year_found file={paper.stored_filename!r}")
                skipped.append(paper.id)
        await session.commit()

    log(f"done updated={len(updated)} skipped={len(skipped)}")
    # 完整明细(含中文文件名)落盘, 避免 GBK 控制台乱码
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    print(f"log written: {LOG_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
