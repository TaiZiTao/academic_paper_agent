"""论文分块与检索索引一键重建(章节树修复后使用)。

用法(在项目根目录):
    venv\Scripts\python.exe scripts/rebuild_paper_chunks.py <paper_id> [--purge-translations]

做什么:
    1. 重新解析 PDF → 新章节树;
    2. 按页内标题偏移重新分块(章节归属精确);
    3. 归属校验(audit_chunks), 发现问题打 warning;
    4. 替换 paper_chunks 表;
    5. 删除孤儿翻译块(章节已不存在的); --purge-translations 时清空该论文全部翻译块;
    6. 重建 FAISS+BM25 检索索引。

注意: 执行后需重启后端进程(内存中的索引/服务单例才会重新加载)。
"""

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

# 允许从任意工作目录运行: 把项目根加入模块搜索路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config.settings import settings
from app.paper.chunker import audit_chunks, chunk_pages
from app.paper.figures import run_mineru_content
from app.paper.parser import parse_pdf
from app.paper.retriever import PaperRetriever
from app.rag.embedding import OpenAIEmbedding

DB_PATH = Path(settings.data_dir) / "graphrag.db"
FILES_DIR = Path(settings.data_dir) / "papers" / "files"
INDEX_DIR = Path(settings.data_dir) / "papers" / "index"


def main() -> int:
    parser = argparse.ArgumentParser(description="重建论文分块与检索索引")
    parser.add_argument("paper_id", type=int)
    parser.add_argument("--purge-translations", action="store_true",
                        help="清空该论文全部翻译块(默认只删章节已不存在的孤儿块)")
    args = parser.parse_args()

    paper_id = args.paper_id
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        "SELECT stored_filename FROM papers WHERE id=?", (paper_id,)
    ).fetchone()
    if row is None:
        print(f"论文 {paper_id} 不存在"); conn.close(); return 1
    pdf_path = FILES_DIR / row[0]
    if not pdf_path.exists():
        print(f"PDF 不存在: {pdf_path}"); conn.close(); return 1

    print(f"[1/4] 重新解析 {pdf_path.name}")
    # 与服务端一致: MinerU 版面检测优先(章节标题), 失败回退启发式
    mineru_data = run_mineru_content(str(pdf_path))
    parsed = parse_pdf(str(pdf_path), mineru_data if mineru_data else None)
    if mineru_data:
        print("      已启用 MinerU 章节标题检测")
    new_sections = [s.title for s in parsed.sections]
    print(f"      章节树: {new_sections}")

    print("[2/4] 重新分块(按页内标题偏移)")
    chunks = chunk_pages(
        parsed.pages, paper_id=paper_id, chunk_size=settings.paper_chunk_size,
        overlap=settings.paper_chunk_overlap, sections=parsed.sections,
    )
    print(f"      分块 {len(chunks)} 个")
    issues = audit_chunks(parsed.pages, chunks, parsed.sections)
    if issues:
        print(f"      [警告] 归属校验发现 {len(issues)} 个问题:")
        for it in issues[:10]:
            print(f"        - {it}")
    else:
        print("      归属校验通过")

    print("[3/4] 替换 paper_pages + paper_sections + paper_chunks + 清理翻译块")
    # 页面文本: 必须与新 chunks 的 char 偏移一致, 否则翻译用旧文本会错位
    conn.execute("DELETE FROM paper_pages WHERE paper_id=?", (paper_id,))
    for page in parsed.pages:
        conn.execute(
            "INSERT INTO paper_pages (paper_id, page_number, text) VALUES (?,?,?)",
            (paper_id, page.page_number, page.text),
        )
    # 章节树: 删除旧行, 写入新解析结果(层级/标题已由排版通道修正)
    conn.execute("DELETE FROM paper_sections WHERE paper_id=?", (paper_id,))
    for section in parsed.sections:
        conn.execute(
            "INSERT INTO paper_sections (paper_id, title, normalized_title, level, ordinal, page_start, page_end, summary) VALUES (?,?,?,?,?,?,?,?)",
            (paper_id, section.title, section.normalized_title, section.level, section.ordinal,
             section.page_start, section.page_end, section.summary or ""),
        )
    conn.execute("DELETE FROM paper_chunks WHERE paper_id=?", (paper_id,))
    for ch in chunks:
        conn.execute(
            "INSERT INTO paper_chunks (paper_id, chunk_id, section, page_start, page_end, ordinal, char_start, char_end, content, metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ch.paper_id, ch.chunk_id, ch.section, ch.page_start, ch.page_end, ch.ordinal,
             ch.char_start, ch.char_end, ch.content, json.dumps(ch.metadata, ensure_ascii=False)),
        )
    if args.purge_translations:
        purged = conn.execute(
            "DELETE FROM paper_translation_blocks WHERE paper_id=?", (paper_id,)
        ).rowcount
        print(f"      清空翻译块 {purged} 个 (--purge-translations)")
    else:
        placeholders = ",".join("?" * len(new_sections))
        orphan = conn.execute(
            f"DELETE FROM paper_translation_blocks WHERE paper_id=? AND section NOT IN ({placeholders})",
            (paper_id, *new_sections),
        ).rowcount
        print(f"      删除孤儿翻译块 {orphan} 个")
        kept = conn.execute(
            "SELECT DISTINCT section FROM paper_translation_blocks WHERE paper_id=?", (paper_id,)
        ).fetchall()
        if kept:
            print(f"      以下章节仍有旧翻译块, 章节归属可能已变化, 建议重新翻译: {[r[0] for r in kept]}")
    conn.commit()
    dist = conn.execute(
        "SELECT section, count(*) FROM paper_chunks WHERE paper_id=? GROUP BY section ORDER BY count(*) DESC", (paper_id,)
    ).fetchall()
    print("      分块分布: " + ", ".join(f"{r[0]} x{r[1]}" for r in dist))
    conn.close()

    print("[4/4] 重建 FAISS+BM25 检索索引")

    async def rebuild():
        embedding = OpenAIEmbedding(
            model=settings.embedding_model, api_key=settings.embedding_api_key,
            base_url=settings.embedding_base_url,
        )
        retriever = PaperRetriever(
            embedding=embedding,
            root_dir=INDEX_DIR,
            vector_weight=settings.retrieval_vector_weight,
            keyword_weight=settings.retrieval_keyword_weight,
        )
        await retriever.build(paper_id, chunks)

    asyncio.run(rebuild())
    print("索引重建完成。请重启后端进程后生效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())