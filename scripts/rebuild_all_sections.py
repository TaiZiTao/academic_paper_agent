"""批量重建所有论文的章节树/分块(应用 MinerU 为主的新逻辑)。

用法: python scripts/rebuild_all_sections.py

串行调用 rebuild_paper_chunks.py(继承 stdio, 实时输出), 每篇独立进程。
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = r"D:\anaconda3\envs\pytorch\python.exe"


def main() -> int:
    import sqlite3

    conn = sqlite3.connect(str(ROOT / "data" / "graphrag.db"))
    pids = [r[0] for r in conn.execute("SELECT id FROM papers ORDER BY id").fetchall()]
    conn.close()
    ok, fail = [], []
    for pid in pids:
        print(f"\n=== rebuild paper {pid} ===", flush=True)
        r = subprocess.run(
            [PY, str(ROOT / "scripts" / "rebuild_paper_chunks.py"), str(pid)],
            env=dict(os.environ, PYTHONIOENCODING="utf-8"),
            cwd=str(ROOT),
        )
        if r.returncode == 0:
            ok.append(pid)
            print(f"paper {pid} rebuild ok", flush=True)
        else:
            fail.append(pid)
            print(f"paper {pid} rebuild FAILED (exit {r.returncode})", flush=True)
    print(f"\n=== 完成: ok={len(ok)} failed={len(fail)} ===", flush=True)
    if fail:
        print(f"failed: {fail}", flush=True)
    return 0 if not fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
