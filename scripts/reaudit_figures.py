"""图表审查 agent 存量回填: 对已有论文的图表跑审查并更新 DB。

用法(项目根目录):
    python scripts/reaudit_figures.py [paper_id ...]

做什么:
    1. 读 DB 中每篇论文的 paper_figures;
    2. 规则层标记同页同编号重复(保留带空格/更长的 caption);
    3. LLM 层批量修复粘连 caption(补空格);
    4. 更新 DB: 删除重复行, 更新保留行的 caption。

不重跑 MinerU/渲染, 只改 caption 文本与删除重复记录。
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import io, sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.config.settings import settings
from app.paper.figure_audit import audit_figures_with_llm

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
                "SELECT id, page, kind, caption FROM paper_figures WHERE paper_id=? ORDER BY page, id",
                (pid,),
            )
            if not rows:
                print(f"paper {pid}: 无图表, 跳过")
                continue
            items = [(r[0], r[1], r[2], r[3] or "") for r in rows]
            try:
                audit = await audit_figures_with_llm(ainvoke, items)
            except Exception as exc:
                print(f"paper {pid}: 审查失败 {exc}")
                continue
            changed = False
            if audit.drop_indexes:
                ph = ",".join("?" * len(audit.drop_indexes))
                await db.execute(
                    f"DELETE FROM paper_figures WHERE id IN ({ph})",
                    tuple(sorted(audit.drop_indexes)),
                )
                print(f"paper {pid}: 删除重复 {len(audit.drop_indexes)} 个: {sorted(audit.drop_indexes)}")
                changed = True
            if audit.fixed_captions:
                for idx, cap in audit.fixed_captions.items():
                    await db.execute(
                        "UPDATE paper_figures SET caption=? WHERE id=?",
                        (cap, idx),
                    )
                print(f"paper {pid}: 修复粘连 {len(audit.fixed_captions)} 个(LLM)")
                changed = True
            if changed:
                await db.commit()
            else:
                print(f"paper {pid}: 无需修改")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
