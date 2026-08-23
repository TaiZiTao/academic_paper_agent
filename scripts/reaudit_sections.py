"""章节树审查 agent 存量回填: 对已有论文的章节树跑 LLM 审查并更新 DB。

用法(项目根目录):
    python scripts/reaudit_sections.py [paper_id ...]

只更新 paper_sections(标题/层级/页码)与 paper_chunks 的 section 归属;
不重跑 MinerU/解析(章节树已入库, 直接读 DB 审查)。
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import io, sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.config.settings import settings
from app.paper.schemas import PaperSectionData
from app.paper.section_audit import audit_sections_with_llm

DB_PATH = Path(settings.data_dir) / "graphrag.db"


async def main() -> int:
    import aiosqlite

    from app.api.dependencies import _get_llm

    pids = [int(x) for x in sys.argv[1:]] if len(sys.argv) > 1 else None
    llm = _get_llm()

    async def ainvoke(prompt: str) -> str:
        result = await llm.ainvoke(prompt)
        raw = result.content if hasattr(result, "content") else result
        return str(raw or "")

    async with aiosqlite.connect(str(DB_PATH)) as db:
        if pids is None:
            rows = await db.execute_fetchall("SELECT id FROM papers ORDER BY id")
            pids = [r[0] for r in rows]
        for pid in pids:
            rows = await db.execute_fetchall(
                "SELECT title, level, page_start, page_end FROM paper_sections WHERE paper_id=? ORDER BY ordinal",
                (pid,),
            )
            if not rows:
                print(f"paper {pid}: 无章节, 跳过"); continue
            sections = [
                PaperSectionData(
                    title=r[0], normalized_title="", level=r[1], ordinal=i,
                    page_start=r[2], page_end=r[3], summary="",
                )
                for i, r in enumerate(rows)
            ]
            before = [s.title for s in sections]
            try:
                audit = await audit_sections_with_llm(ainvoke, sections)
            except Exception as exc:
                print(f"paper {pid}: 审查失败 {exc}"); continue
            after = [s.title for s in audit.sections]
            if after != before:
                # 更新章节表(全删重插, 保持 ordinal)
                await db.execute("DELETE FROM paper_sections WHERE paper_id=?", (pid,))
                for s in audit.sections:
                    await db.execute(
                        "INSERT INTO paper_sections (paper_id, title, normalized_title, level, ordinal, page_start, page_end, summary) VALUES (?,?,?,?,?,?,?,?)",
                        (pid, s.title, s.normalized_title, s.level, s.ordinal, s.page_start, s.page_end, s.summary or ""),
                    )
                await db.commit()
                print(f"paper {pid}: 审查{' (LLM)' if audit.by_llm else ''}")
                before_set = set(before)
                after_set = set(after)
                removed = [t for t in before if t not in after_set]
                added = [t for t in after if t not in before_set]
                if removed:
                    print(f"    移除: {removed}")
                if added:
                    print(f"    新增: {added}")
                changed = [(a, b) for a, b in zip(before, after) if a != b][:10]
                for a, b in changed:
                    print(f"    {a!r} -> {b!r}")
            else:
                print(f"paper {pid}: 无需修改")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))