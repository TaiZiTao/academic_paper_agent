"""重新对所有论文跑 MinerU(进程内模式)并回填图表: 删除旧 figure 记录与 PNG, 重新检测/渲染/入库。

用法: python scripts/backfill_figures_mineru_all.py [paper_id ...]
"""

import asyncio, os, sys
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, r"E:/codex/GraphRAG--main")
import os as _os
_os.chdir(r"E:/codex/GraphRAG--main")
from pathlib import Path

from sqlalchemy import delete

from app.config.settings import settings
from app.database import async_session, init_db
from app.models.paper import Paper, PaperFigure
from app.paper.figures import (
    audit_regions,
    detect_figures,
    run_mineru_content,
    _mineru_content_to_regions,
    render_region,
)

FILES_DIR = Path(settings.data_dir) / 'papers' / 'files'


async def backfill(paper_id: int) -> None:
    async with async_session() as session:
        paper = await session.get(Paper, paper_id)
        if paper is None:
            print(f'paper {paper_id} 不存在')
            return
        pdf = FILES_DIR / paper.stored_filename
        print(f'=== paper {paper_id}: {paper.original_filename[:60]} ===')

        # 1) MinerU(进程内) → 无结果回退启发式
        mineru_data = await asyncio.to_thread(run_mineru_content, pdf)
        figure_regions = []
        if mineru_data:
            figure_regions = _mineru_content_to_regions(pdf, mineru_data)
            print(f'  MinerU 检出 {len(figure_regions)} 个图表区域')
        if not figure_regions:
            print('  MinerU 未检出, 回退启发式')
            figure_regions = await asyncio.to_thread(detect_figures, pdf)
        print(f'  最终 {len(figure_regions)} 个图表区域')
        if not figure_regions:
            return
        try:
            for issue in audit_regions(pdf, figure_regions):
                print(f"  [audit] #{issue['index']} {issue['kind']} {issue['caption'][:40]}: {issue['sample'][:60]}")
        except Exception as exc:
            print(f'  audit 失败: {exc}')

        figures_dir = FILES_DIR.parent / 'figures' / str(paper_id)
        figures_dir.mkdir(parents=True, exist_ok=True)

        # 清掉旧 PNG
        for old in figures_dir.glob('*.png'):
            old.unlink(missing_ok=True)

        def _render_all():
            rendered = []
            for index, region in enumerate(figure_regions):
                out = figures_dir / f'{index}.png'
                try:
                    render_region(pdf, region, out)
                except Exception as exc:
                    print(f'  图表 #{index} 渲染失败, 跳过: {exc}')
                    continue
                rendered.append((region, out))
            return rendered

        rendered = await asyncio.to_thread(_render_all)
        await session.execute(delete(PaperFigure).where(PaperFigure.paper_id == paper_id))
        for index, (region, out) in enumerate(rendered):
            session.add(PaperFigure(
                paper_id=paper_id,
                page=region.page,
                kind=region.kind,
                ordinal=index,
                caption=region.caption,
                bbox=f'{region.x0:.1f},{region.y0:.1f},{region.x1:.1f},{region.y1:.1f}',
                image_path=str(out),
            ))
        await session.commit()
        print(f'  已写入 {len(rendered)} 条图表记录')


async def main():
    import sys as _sys
    await init_db()
    pids = [int(x) for x in _sys.argv[1:]] or [1, 2, 3, 4, 5, 6, 7, 9]
    for pid in pids:
        try:
            await backfill(pid)
        except Exception as exc:
            print(f'paper {pid} 回填失败: {type(exc).__name__}: {exc}')


if __name__ == "__main__":
    asyncio.run(main())