
"""回填图表检测: 对论文 3/6/7 重新检测图表区域、渲染 PNG、写入 paper_figures。

修复前的 _CAPTION 正则只认冒号风格图注, 导致句号/空格风格图注的论文检测结果为 0。
修复后重新检测并回填, 不重跑解析/索引/报告(避免重复 LLM 调用)。
同时修复 6/7 的研究方向分类(此前因 AIMessage.strip 失败留空为未分类)。
"""

import asyncio, json, os, sys
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, r"E:/codex/GraphRAG--main")
from pathlib import Path

from sqlalchemy import delete

from app.config.settings import settings
from app.database import async_session, init_db
from app.models.paper import Paper, PaperFigure
from app.paper.figures import (
    audit_regions,
    detect_figures,
    detect_figures_mineru,
    render_region,
)
from app.paper.prompts import build_field_classification_prompt, parse_field_response

FILES_DIR = Path(settings.data_dir) / "papers" / "files"


async def backfill(paper_id: int) -> None:
    async with async_session() as session:
        paper = await session.get(Paper, paper_id)
        if paper is None:
            print(f"paper {paper_id} 不存在")
            return
        pdf = FILES_DIR / paper.stored_filename
        print(f"=== paper {paper_id}: {paper.original_filename[:60]} ===")

        # 1. 图表检测 + 渲染 + 入库(MinerU 优先, 失败回退启发式)
        figure_regions = await asyncio.to_thread(detect_figures_mineru, pdf)
        if not figure_regions:
            print("  MinerU 未检出, 回退启发式")
            figure_regions = await asyncio.to_thread(detect_figures, pdf)
        print(f"  检测到 {len(figure_regions)} 个图表区域")
        if not figure_regions:
            return
        try:
            for issue in audit_regions(pdf, figure_regions):
                print(f"  [audit] #{issue['index']} {issue['kind']} {issue['caption'][:40]}: {issue['sample'][:60]}")
        except Exception as exc:
            print(f"  audit 失败: {exc}")

        figures_dir = FILES_DIR.parent / "figures" / str(paper_id)
        figures_dir.mkdir(parents=True, exist_ok=True)

        def _render_all():
            rendered = []
            for index, region in enumerate(figure_regions):
                out = figures_dir / f"{index}.png"
                try:
                    render_region(pdf, region, out)
                except Exception as exc:
                    print(f"  图表 #{index} 渲染失败, 跳过: {exc}")
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
                bbox=f"{region.x0:.1f},{region.y0:.1f},{region.x1:.1f},{region.y1:.1f}",
                image_path=str(out),
            ))
        await session.commit()
        print(f"  已写入 {len(rendered)} 条图表记录")

        # 2. 研究方向分类修复(6/7 此前失败留空为未分类)
        if paper_id in (6, 7) and not (paper.research_field or "").strip():
            try:
                from app.api.dependencies import _get_llm
                llm = _get_llm()
                prompt = build_field_classification_prompt(
                    paper.title, paper.abstract, json.loads(paper.keywords_json or "[]")
                )
                result = await llm.ainvoke(prompt)
                raw = result.content if hasattr(result, "content") else result
                field = parse_field_response(str(raw or ""))
                paper.research_field = field
                await session.commit()
                print(f"  研究方向分类: {field}")
            except Exception as exc:
                print(f"  研究方向分类失败: {exc}")


async def main():
    import sys as _sys
    await init_db()
    pids = [int(x) for x in _sys.argv[1:]] or [3, 6, 7]
    for pid in pids:
        try:
            await backfill(pid)
        except Exception as exc:
            print(f"paper {pid} 回填失败: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
