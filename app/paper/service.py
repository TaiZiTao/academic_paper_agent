"""单篇论文从上传到精读产物的业务编排。"""

import asyncio
import inspect
import json
import re
import uuid
from collections.abc import AsyncIterator, Callable

from loguru import logger
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.paper import (
    Paper,
    PaperArtifact,
    PaperChunk,
    PaperFigure,
    PaperMessage,
    PaperPage,
    PaperSection,
    PaperTask,
    PaperTranslationBlock,
)
from app.paper.chunker import audit_chunks, chunk_pages
from app.paper.citations import CitationValidator
from app.paper.content_filter import strip_visual_regions
from app.paper.figure_audit import audit_figures_with_llm
from app.paper.figures import (
    _mineru_content_to_regions,
    audit_regions,
    detect_figures,
    run_mineru_content,
    render_region,
)
from app.paper.graph import build_paper_graph
from app.paper.library_graph import build_library_graph
from app.paper.nodes import _json_content
from app.paper.parser import UnsupportedScanError, parse_pdf
from app.paper.section_audit import audit_sections_with_llm
from app.paper.prompts import (
    build_caption_prompt,
    build_field_classification_prompt,
    build_task_prompt,
    build_translation_prompt,
    parse_field_response,
)
from app.paper.schemas import PaperChunkData, PaperCitation, ParsedPaper


class PaperNotFoundError(LookupError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _report_to_markdown(report: dict[str, Any]) -> str:
    labels = {
        "background": "研究背景与方向",
        "motivation": "论文动机",
        "existing_problems": "现有方法存在的问题",
        "solution": "解决方案与创新点",
        "contributions": "论文主要贡献",
        "terms": "关键术语",
    }
    blocks: list[str] = []
    for key, label in labels.items():
        value = report.get(key, "原文未提供充分证据")
        if key == "terms" and isinstance(value, list):
            rendered = "\n".join(
                f"- {item.get('en', '')}：{item.get('zh', '')}" if isinstance(item, dict) else f"- {item}"
                for item in value
            ) or "原文未提供充分证据"
        else:
            rendered = str(value)
        blocks.append(f"## {label}\n\n{rendered}")
    return "\n\n".join(blocks)


class PaperService:
    TERMINAL_STATUSES = {"ready", "failed"}

    def __init__(
        self,
        session_factory: async_sessionmaker,
        retriever,
        llm,
        files_dir: str | Path,
        graph=None,
        library_graph=None,
        parser_fn: Callable[[Path], ParsedPaper] = parse_pdf,
        chunk_size: int = 1000,
        chunk_overlap: int = 120,
    ) -> None:
        self.session_factory = session_factory
        self.retriever = retriever
        self.llm = llm
        self.files_dir = Path(files_dir).resolve()
        self.graph = graph or build_paper_graph()
        self.library_graph = library_graph or build_library_graph()
        self.parser_fn = parser_fn
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.translation_block_size = max(8000, chunk_size)
        self._event_history: dict[int, list[dict[str, Any]]] = {}
        self._event_queues: dict[int, list[asyncio.Queue]] = {}

    def _publish(self, paper_id: int, stage: str, status: str, message: str = "") -> None:
        event = {"event": "progress", "stage": stage, "status": status, "message": message}
        self._event_history.setdefault(paper_id, []).append(event)
        for queue in self._event_queues.get(paper_id, []):
            queue.put_nowait(event)

    async def progress_events(self, paper_id: int) -> AsyncIterator[dict[str, Any]]:
        for event in self._event_history.get(paper_id, []):
            yield event
        paper = await self.get_paper(paper_id)
        if paper is None:
            yield {"event": "error", "stage": "", "status": "failed", "message": "论文不存在"}
            return
        if paper.status in self.TERMINAL_STATUSES:
            yield {"event": "done", "stage": paper.status, "status": paper.status, "message": paper.error_message}
            return

        queue: asyncio.Queue = asyncio.Queue()
        self._event_queues.setdefault(paper_id, []).append(queue)
        try:
            while True:
                event = await queue.get()
                yield event
                if event["status"] in self.TERMINAL_STATUSES:
                    yield {"event": "done", **event}
                    return
        finally:
            self._event_queues.get(paper_id, []).remove(queue)

    async def create_paper(self, original_filename: str, content: bytes) -> Paper:
        if not original_filename.lower().endswith(".pdf"):
            raise ValueError("仅支持 PDF 文件")
        if not content:
            raise ValueError("PDF 文件为空")
        self.files_dir.mkdir(parents=True, exist_ok=True)
        stored_filename = f"{uuid.uuid4().hex}_{Path(original_filename).name}"
        target = (self.files_dir / stored_filename).resolve()
        if target.parent != self.files_dir:
            raise ValueError("非法文件名")
        target.write_bytes(content)

        paper = Paper(
            original_filename=Path(original_filename).name,
            stored_filename=stored_filename,
            title=Path(original_filename).stem,
            status="uploaded",
        )
        async with self.session_factory() as session:
            session.add(paper)
            await session.commit()
            await session.refresh(paper)
        self._publish(paper.id, "uploaded", "uploaded", "PDF 已保存")
        return paper

    def file_path(self, paper: Paper) -> Path:
        path = (self.files_dir / paper.stored_filename).resolve()
        if path.parent != self.files_dir:
            raise ValueError("论文文件路径越界")
        return path

    async def get_paper(self, paper_id: int) -> Paper | None:
        async with self.session_factory() as session:
            return await session.get(Paper, paper_id)

    async def _set_failure(self, paper_id: int, code: str, message: str) -> None:
        async with self.session_factory() as session:
            paper = await session.get(Paper, paper_id)
            if paper:
                paper.status = "failed"
                paper.error_code = code
                paper.error_message = message
                await session.commit()
        self._publish(paper_id, code, "failed", message)

    async def process_paper(self, paper_id: int) -> None:
        paper = await self.get_paper(paper_id)
        if paper is None:
            raise PaperNotFoundError(f"论文 {paper_id} 不存在")

        if paper.status == "failed" and paper.page_count:
            async with self.session_factory() as session:
                for model in [PaperArtifact, PaperTask, PaperChunk, PaperSection, PaperPage, PaperFigure]:
                    await session.execute(delete(model).where(model.paper_id == paper_id))
                stored = await session.get(Paper, paper_id)
                stored.page_count = 0
                await session.commit()
            self.retriever.delete(paper_id)

        try:
            async with self.session_factory() as session:
                stored = await session.get(Paper, paper_id)
                stored.status = "parsing"
                stored.error_code = ""
                stored.error_message = ""
                await session.commit()
            self._publish(paper_id, "parsing", "parsing", "正在提取逐页文本和章节")
            # MinerU 版面检测一次(MinerU 优先): 章节标题与图表区域共用同一份输出;
            # 失败返回 None, 章节/图表各自回退启发式
            mineru_data = await asyncio.to_thread(
                run_mineru_content, self.file_path(paper)
            )
            # parser_fn 默认 parse_pdf 已支持 mineru_data 参数; 自定义解析器
            # (测试 mock)可能只接受单参, 签名兼容处理
            if mineru_data is not None and len(
                inspect.signature(self.parser_fn).parameters
            ) >= 2:
                parsed = await asyncio.to_thread(
                    self.parser_fn, self.file_path(paper), mineru_data
                )
            else:
                parsed = await asyncio.to_thread(self.parser_fn, self.file_path(paper))
        except UnsupportedScanError as exc:
            await self._set_failure(paper_id, "unsupported_scan", str(exc))
            return
        except Exception as exc:
            await self._set_failure(paper_id, "parse_failed", str(exc))
            return

        # 章节树审查 agent: 解析后自动检查并修正异常章节标题
        # (正文句被当标题/标题拆行/编号跳变/粘连)。失败不阻断主流程。
        try:
            audit = await audit_sections_with_llm(self._ainvoke_with_retry, parsed.sections)
            if audit.dropped or audit.fixed or audit.by_llm:
                parsed.sections = audit.sections
                for _i, _s in enumerate(parsed.sections):
                    _s.ordinal = _i
                logger.info(
                    f"论文 {paper_id} 章节审查: 丢弃 {len(audit.dropped)} 个伪标题"
                    f"{audit.dropped[:3]}, 修正 {len(audit.fixed)} 处"
                    f"{' (LLM)' if audit.by_llm else ''}"
                )
        except Exception as exc:
            logger.warning(f"论文 {paper_id} 章节审查失败(不影响解析): {exc}")

        chunks = chunk_pages(
            parsed.pages,
            paper_id=paper_id,
            chunk_size=self.chunk_size,
            overlap=self.chunk_overlap,
            sections=parsed.sections,
        )
        if not chunks:
            await self._set_failure(paper_id, "unsupported_scan", "PDF 中没有可索引文本")
            return
        # 自动审查分块章节归属: 前一章结尾被划给后一章等错位在此暴露, 不影响主流程
        try:
            for issue in audit_chunks(parsed.pages, chunks, parsed.sections):
                logger.warning(f"论文 {paper_id} 分块章节归属可疑: {issue}")
        except Exception:
            pass

        async with self.session_factory() as session:
            stored = await session.get(Paper, paper_id)
            stored.title = parsed.metadata.title or stored.title
            stored.authors_json = _json(parsed.metadata.authors)
            stored.abstract = parsed.metadata.abstract
            stored.keywords_json = _json(parsed.metadata.keywords)
            stored.language = parsed.language
            stored.page_count = parsed.page_count
            # 发表年份(提取失败为 None 时保留旧值)
            if parsed.publication_year:
                stored.publication_year = parsed.publication_year
            # 研究方向自动分类(失败不阻断: 留空, 用户可在库管理页手动设置)
            try:
                field_prompt = build_field_classification_prompt(
                    stored.title, stored.abstract, parsed.metadata.keywords
                )
                field_raw = await self._ainvoke_with_retry(field_prompt)
                stored.research_field = parse_field_response(field_raw)
            except Exception as exc:
                logger.warning(f"论文 {paper_id} 研究方向分类失败(不影响精读): {exc}")
            session.add_all(
                [PaperPage(paper_id=paper_id, page_number=page.page_number, text=page.text) for page in parsed.pages]
            )
            session.add_all(
                [
                    PaperSection(
                        paper_id=paper_id,
                        title=section.title,
                        normalized_title=section.normalized_title,
                        level=section.level,
                        ordinal=section.ordinal,
                        page_start=section.page_start,
                        page_end=section.page_end,
                        summary=section.summary,
                    )
                    for section in parsed.sections
                ]
            )
            session.add_all(
                [
                    PaperChunk(
                        paper_id=paper_id,
                        chunk_id=chunk.chunk_id,
                        section=chunk.section,
                        page_start=chunk.page_start,
                        page_end=chunk.page_end,
                        ordinal=chunk.ordinal,
                        char_start=chunk.char_start,
                        char_end=chunk.char_end,
                        content=chunk.content,
                        metadata_json=_json(chunk.metadata),
                    )
                    for chunk in chunks
                ]
            )
            stored.status = "indexing"
            await session.commit()

        # 图表区域检测、图片渲染与入库(失败不阻断精读主流程)
        try:
            # 复用解析阶段已跑的 MinerU 结果; 不可用/未检出时回退启发式
            if mineru_data:
                figure_regions = _mineru_content_to_regions(
                    self.file_path(paper), mineru_data
                )
            else:
                figure_regions = []
            if not figure_regions:
                logger.warning(f"论文 {paper_id} MinerU 未检出图表, 回退启发式检测")
                figure_regions = await asyncio.to_thread(
                    detect_figures, self.file_path(paper)
                )
            if not figure_regions:
                # 检测到 0 图表: 可能是论文确实无图, 也可能是图注风格/版式未被识别。
                # 显式打日志, 避免"静默漏检"——旧版这里无任何输出, 漏检难发现。
                logger.warning(
                    f"论文 {paper_id} 图表检测结果为 0 ({paper.page_count} 页, "
                    f"{paper.original_filename[:60]}); 若论文确有图表请检查图注格式"
                )
            if figure_regions:
                # 自动审查裁剪质量: 混入他栏文字/图注/越界等可疑项打日志, 便于复核
                try:
                    for issue in audit_regions(self.file_path(paper), figure_regions):
                        logger.warning(
                            f"论文 {paper_id} 图表裁剪可疑(#{issue['index']} {issue['kind']} "
                            f"{issue['caption']}): {issue['sample']}"
                        )
                except Exception:
                    pass
                # 图表审查 agent: 修 caption 粘连(LLM 补空格)、标记同页同编号重复。
                # 失败不阻断; 重复项删除, 粘连 caption 用 LLM 修正后的文本。
                try:
                    _items = [
                        (index, r.page, r.kind, r.caption or "")
                        for index, r in enumerate(figure_regions)
                    ]
                    faudit = await audit_figures_with_llm(self._ainvoke_with_retry, _items)
                    if faudit.drop_indexes or faudit.fixed_captions:
                        # 保留原索引: 删除重复后, 按原索引映射修正后的 caption
                        _kept = [
                            (i, r) for i, r in enumerate(figure_regions)
                            if i not in faudit.drop_indexes
                        ]
                        figure_regions = []
                        for _orig, _r in _kept:
                            if _orig in faudit.fixed_captions:
                                _r.caption = faudit.fixed_captions[_orig]
                            figure_regions.append(_r)
                        logger.info(
                            f"论文 {paper_id} 图表审查: 删除重复 {len(faudit.drop_indexes)} 个, "
                            f"修复粘连 {len(faudit.fixed_captions)} 个"
                        )
                except Exception as exc:
                    logger.warning(f"论文 {paper_id} 图表审查失败(不影响入库): {exc}")
                figures_dir = self.files_dir.parent / "figures" / str(paper_id)

                def _render_all() -> list[tuple[Any, Path]]:
                    rendered: list[tuple[Any, Path]] = []
                    for index, region in enumerate(figure_regions):
                        out = figures_dir / f"{index}.png"
                        try:
                            render_region(self.file_path(paper), region, out)
                        except Exception as exc:
                            # 单个区域渲染失败(如非法裁剪矩形)只跳过该区域,
                            # 不拖垮整篇论文的图表提取
                            logger.warning(f"论文 {paper_id} 图表 #{index} 渲染失败, 跳过: {exc}")
                            continue
                        rendered.append((region, out))
                    return rendered

                rendered = await asyncio.to_thread(_render_all)
                async with self.session_factory() as session:
                    for index, (region, out) in enumerate(rendered):
                        session.add(
                            PaperFigure(
                                paper_id=paper_id,
                                page=region.page,
                                kind=region.kind,
                                ordinal=index,
                                caption=region.caption,
                                bbox=f"{region.x0:.1f},{region.y0:.1f},{region.x1:.1f},{region.y1:.1f}",
                                image_path=str(out),
                            )
                        )
                    await session.commit()
        except Exception as exc:
            logger.warning(f"论文 {paper_id} 图表区域检测失败(不影响精读): {exc}")

        self._publish(paper_id, "indexing", "indexing", f"正在建立 {len(chunks)} 个页码分块的索引")
        try:
            await self.retriever.build(paper_id, chunks)
        except Exception as exc:
            await self._set_failure(paper_id, "index_failed", str(exc))
            return

        async with self.session_factory() as session:
            stored = await session.get(Paper, paper_id)
            stored.status = "reporting"
            task = PaperTask(paper_id=paper_id, task_type="report", status="running", input_json="{}")
            session.add(task)
            await session.commit()
            await session.refresh(task)
            task_id = task.id
        self._publish(paper_id, "reporting", "reporting", "正在生成并校验精读报告")

        state = {
            "paper_id": paper_id,
            "paper_title": parsed.metadata.title,
            "metadata": parsed.metadata.model_dump(),
            "sections": [section.model_dump() for section in parsed.sections],
            "chunks": [chunk.model_dump() for chunk in chunks],
            "retry_count": 0,
            "completed_nodes": [],
        }
        try:
            result = await self.graph.ainvoke(state, {"configurable": {"llm": self.llm}})
        except Exception as exc:
            async with self.session_factory() as session:
                task = await session.get(PaperTask, task_id)
                task.status = "failed"
                task.error_code = "llm_failed"
                task.error_message = str(exc)
                await session.commit()
            await self._set_failure(paper_id, "llm_failed", str(exc))
            return

        report = result.get("report", {})
        citations = result.get("citations", [])
        artifact_data = result.get("artifact", {})
        async with self.session_factory() as session:
            task = await session.get(PaperTask, task_id)
            task.status = "completed"
            artifact = PaperArtifact(
                paper_id=paper_id,
                task_id=task_id,
                artifact_type="report",
                title=artifact_data.get("title", "单篇论文精读报告"),
                content_json=_json(report),
                content_text=_report_to_markdown(report),
                citations_json=_json(citations),
            )
            session.add(artifact)
            stored = await session.get(Paper, paper_id)
            stored.status = "ready"
            stored.error_code = ""
            stored.error_message = ""
            await session.commit()
        self._publish(paper_id, "ready", "ready", "精读报告已生成")

    async def list_papers(
        self,
        search: str = "",
        page: int = 1,
        page_size: int = 20,
        field: str = "",
        group: bool = False,
    ) -> dict[str, Any]:
        page = max(1, page)
        page_size = min(100, max(1, page_size))
        filters = []
        if search.strip():
            token = f"%{search.strip()}%"
            # 搜索扩展到标题/摘要/作者
            filters.append(
                or_(
                    Paper.title.ilike(token),
                    Paper.original_filename.ilike(token),
                    Paper.abstract.ilike(token),
                    Paper.authors_json.ilike(token),
                )
            )
        if field.strip() and field != "未分类":
            filters.append(Paper.research_field == field.strip())
        if field == "未分类":
            filters.append(Paper.research_field == "")
        async with self.session_factory() as session:
            count_stmt = select(func.count()).select_from(Paper)
            query = select(Paper)
            if filters:
                count_stmt = count_stmt.where(*filters)
                query = query.where(*filters)
            total = await session.scalar(count_stmt) or 0
            rows = (
                await session.execute(
                    query.order_by(Paper.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
                )
            ).scalars().all()
            # 方向清单: 全量(不随筛选变化), 分组/平铺都返回, 供前端筛选栏使用
            all_fields = sorted(
                {
                    (row or "未分类")
                    for row in (
                        await session.execute(select(Paper.research_field))
                    ).scalars().all()
                }
            )
            if not group:
                return {"items": list(rows), "fields": all_fields, "total": total}
            # 分组视图: 按研究方向分组, 组内时间倒序; 未分类最后
            field_rows = (
                await session.execute(
                    select(Paper)
                    .where(*filters)
                    .order_by(Paper.research_field, Paper.created_at.desc())
                )
            ).scalars().all()
        groups_map: dict[str, list[Paper]] = {}
        for paper in field_rows:
            key = paper.research_field or "未分类"
            groups_map.setdefault(key, []).append(paper)
        ordered = sorted(
            groups_map.items(),
            key=lambda kv: (kv[0] == "未分类", kv[0]),
        )
        groups = [
            {"field": key, "count": len(items), "items": items}
            for key, items in ordered
        ]
        return {"groups": groups, "fields": all_fields, "total": total}

    async def update_field(self, paper_id: int, field: str) -> Paper | None:
        """更新论文研究方向(手动修正/新增方向)。"""
        field = (field or "").strip()
        async with self.session_factory() as session:
            paper = await session.get(Paper, paper_id)
            if paper is None:
                return None
            paper.research_field = field
            await session.commit()
            await session.refresh(paper)
            return paper

    async def get_detail(self, paper_id: int) -> dict[str, Any] | None:
        async with self.session_factory() as session:
            paper = await session.get(Paper, paper_id)
            if paper is None:
                return None
            sections = (
                await session.execute(
                    select(PaperSection)
                    .where(PaperSection.paper_id == paper_id)
                    .order_by(PaperSection.ordinal)
                )
            ).scalars().all()
            artifacts = (
                await session.execute(
                    select(PaperArtifact)
                    .where(PaperArtifact.paper_id == paper_id)
                    .order_by(PaperArtifact.created_at.desc(), PaperArtifact.id.desc())
                )
            ).scalars().all()
            messages = (
                await session.execute(
                    select(PaperMessage)
                    .where(PaperMessage.paper_id == paper_id)
                    .order_by(PaperMessage.created_at, PaperMessage.id)
                )
            ).scalars().all()
            translation_blocks = (
                await session.execute(
                    select(PaperTranslationBlock)
                    .where(PaperTranslationBlock.paper_id == paper_id)
                    .order_by(
                        PaperTranslationBlock.section,
                        PaperTranslationBlock.block_index,
                    )
                )
            ).scalars().all()
            figures = (
                await session.execute(
                    select(PaperFigure)
                    .where(PaperFigure.paper_id == paper_id)
                    .order_by(PaperFigure.page, PaperFigure.ordinal)
                )
            ).scalars().all()
        return {
            "paper": paper,
            "sections": list(sections),
            "artifacts": list(artifacts),
            "messages": list(messages),
            "translation_blocks": list(translation_blocks),
            "figures": [
                {
                    "id": figure.id,
                    "page": figure.page,
                    "kind": figure.kind,
                    "ordinal": figure.ordinal,
                    "caption": figure.caption,
                    "caption_translated": figure.caption_translated,
                    "image_url": f"/api/v1/papers/{paper_id}/figures/{figure.id}/image",
                }
                for figure in figures
            ],
        }

    async def figure_image_path(self, paper_id: int, figure_id: int) -> Path | None:
        async with self.session_factory() as session:
            row = await session.get(PaperFigure, figure_id)
            if row is None or row.paper_id != paper_id:
                return None
            path = Path(row.image_path)
            return path if path.exists() else None

    async def _load_chunks(self, paper_id: int) -> list[PaperChunkData]:
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(PaperChunk)
                    .where(PaperChunk.paper_id == paper_id)
                    .order_by(PaperChunk.ordinal)
                )
            ).scalars().all()
        return [
            PaperChunkData(
                paper_id=row.paper_id,
                chunk_id=row.chunk_id,
                section=row.section,
                page_start=row.page_start,
                page_end=row.page_end,
                ordinal=row.ordinal,
                char_start=row.char_start,
                char_end=row.char_end,
                content=row.content,
                metadata=json.loads(row.metadata_json or "{}"),
            )
            for row in rows
        ]

    async def _load_history(self, paper_id: int, session_id: str, limit: int = 10) -> list[dict[str, str]]:
        if not session_id:
            return []
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(PaperMessage)
                    .where(PaperMessage.paper_id == paper_id, PaperMessage.session_id == session_id)
                    .order_by(PaperMessage.created_at.desc(), PaperMessage.id.desc())
                    .limit(limit)
                )
            ).scalars().all()
        return [{"role": row.role, "content": row.content} for row in reversed(rows)]

    @staticmethod
    def _normalize_presentation(payload: dict[str, Any]) -> dict[str, Any]:
        """把汇报提纲 LLM 输出规整为 slides 结构 + markdown 纯文本。"""
        raw_slides = payload.get("slides", [])
        slides: list[dict[str, Any]] = []
        if isinstance(raw_slides, list):
            for item in raw_slides:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title", "") or "").strip()
                if not title:
                    continue
                bullets_raw = item.get("bullets", [])
                bullets = [
                    str(b).strip()
                    for b in (bullets_raw if isinstance(bullets_raw, list) else [])
                    if str(b).strip()
                ]
                notes = str(item.get("notes", "") or "").strip()
                slides.append({"title": title, "bullets": bullets, "notes": notes})
        lines: list[str] = []
        for index, slide in enumerate(slides, start=1):
            lines.append(f"## Slide {index}: {slide['title']}")
            for bullet in slide["bullets"]:
                lines.append(f"- {bullet}")
            if slide["notes"]:
                lines.append(f"  (备注) {slide['notes']}")
        return {
            "slides": slides,
            "markdown": "\n".join(lines),
        }

    @staticmethod
    def _normalize_review(payload: dict[str, Any]) -> dict[str, Any]:
        """把审稿 LLM 输出规整为结构化字段(缺省为空, 列表去空)。"""
        def _list(key: str) -> list[str]:
            value = payload.get(key, [])
            if not isinstance(value, list):
                return []
            return [str(item).strip() for item in value if str(item).strip()]

        ratings = payload.get("ratings")
        if not isinstance(ratings, dict):
            ratings = {}

        def _rating(key: str) -> str:
            value = ratings.get(key)
            return str(value).strip() if value else ""

        score = payload.get("score")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            score = None
        return {
            "summary": str(payload.get("summary", "") or "").strip(),
            "contributions": _list("contributions"),
            "strengths": _list("strengths"),
            "major_issues": _list("major_issues"),
            "minor_issues": _list("minor_issues"),
            "ratings": {
                "novelty": _rating("novelty"),
                "correctness": _rating("correctness"),
                "experiments": _rating("experiments"),
                "writing": _rating("writing"),
            },
            "suggestions": _list("suggestions"),
            "recommendation": str(payload.get("recommendation", "") or "").strip(),
            "score": score,
        }

    async def _ainvoke_with_retry(self, prompt: str, retries: int = 3) -> str:
        """LLM 调用带重试:连接错误/超时等瞬时故障最多重试 3 次(退避)。

        langchain 1.x 的 llm.ainvoke 返回 AIMessage 对象而非字符串, 这里统一
        提取文本后返回, 避免调用点直接对 AIMessage 做字符串操作(.strip() 等)报错。
        """
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                result = await self.llm.ainvoke(prompt)
                raw = result.content if hasattr(result, "content") else result
                if isinstance(raw, list):
                    raw = "".join(
                        str(item.get("text", "")) if isinstance(item, dict) else str(item)
                        for item in raw
                    )
                return str(raw or "")
            except Exception as exc:
                last_exc = exc
                if attempt < retries - 1:
                    await asyncio.sleep(1.5 * (attempt + 1))
        raise last_exc if last_exc is not None else RuntimeError("LLM 调用失败")
    @staticmethod
    def _translation_source(chunk: PaperChunk, previous_end: int | None) -> str:
        source = chunk.content
        if previous_end is not None and chunk.char_start < previous_end:
            source = source[min(previous_end - chunk.char_start, len(source)) :]
        return source.strip()

    @staticmethod
    def _translation_response_text(response: Any) -> str:
        raw = response.content if hasattr(response, "content") else response
        if isinstance(raw, list):
            raw = "".join(
                str(item.get("text", "")) if isinstance(item, dict) else str(item)
                for item in raw
            )
        text = str(raw or "").strip()
        if text.startswith("{"):
            payload = _json_content(text)
            return str(payload.get("content", "")).strip()
        return text

    @staticmethod
    def _strip_table_rows(text: str) -> str:
        """翻译源中剔除表格数据行与指标表头行, 避免译文带出折行的数字数据。

        表格数据行特征: 数字占比高(≥25%)且含 ≥3 个小数(如 "31.55±4.932 0.9730±0.0236...");
        指标表头行: PSNR/FSIM/LPIPS 等指标词出现 ≥2 次。正文句子数字稀疏, 不受影响。
        """
        # 注意: 不含 Method(正文 "method" 常见); 表头靠指标词+数据集词识别,
        # 占比规则(>50% 词)保护含数据集名的正文长句
        metric_words = re.compile(
            r"(?:PSNR|FSIM|LPIPS|SSIM|Params|FLOPs|Accuracy|Scene|Thin|Moderate|Thick|"
            r"RICE|RSID|SateHaze|Urban|BSD|Manga|Set)",
            re.I,
        )
        kept: list[str] = []
        for line in text.splitlines():
            s = line.strip()
            if not s:
                kept.append(line)
                continue
            digits = len(re.findall(r"\d", s))
            decimals = len(re.findall(r"\d+\.\d+", s))
            if len(s) >= 15 and decimals >= 3 and digits / len(s) > 0.25:
                continue  # 表格数据行
            # 指标表头行(如 "PSNR FSIM LPIPS PSNR FSIM LPIPS" / "RICE1 RICE2"):
            # 指标/数据集词占该行词数一半以上才删——长句(如 "on RICE1 and RICE2...")保留
            metric_hits = metric_words.findall(s)
            tokens = re.findall(r"[A-Za-z0-9]+", s)
            if (
                len(metric_hits) >= 2
                and len(metric_hits) / max(1, len(tokens)) > 0.5
            ):
                continue
            kept.append(line)
        return "\n".join(kept)

    @staticmethod
    def _clean_translation_page_prefix(source: str, starts_at_page_top: bool) -> str:
        """移除续页页顶的页码、图内标签和图注，保留其后的正文。"""
        if not starts_at_page_top:
            return strip_visual_regions(source.strip())
        lines = source.splitlines()
        if lines and re.fullmatch(r"\s*\d+\s*", lines[0]):
            lines = lines[1:]
        # 只处理页首附近的图注(前 6 行): 两栏正文中段的图注(如 "Figure3:t-SNE..." 嵌在
        # 正文中间)不是页顶元素, 不能删——否则会把前面的公式(6)(7)(8)一起吞掉
        caption_index = next(
            (
                index
                for index, line in enumerate(lines[:6])
                if re.match(r"\s*(?:Fig(?:ure)?\.?)[ ]*\d+[.:]", line, re.IGNORECASE)
            ),
            None,
        )
        if caption_index is not None:
            caption_end = caption_index
            while caption_end + 1 < len(lines) and not re.search(
                r"[.!?]\s*$",
                lines[caption_end],
            ):
                caption_end += 1
            lines = lines[caption_end + 1 :]
        return strip_visual_regions("\n".join(lines).strip())

    async def _run_translation(
        self,
        paper: Paper,
        task_id: int,
        section: str | None,
    ) -> AsyncIterator[dict[str, Any]]:
        if not section:
            raise ValueError("请选择要翻译的章节")
        filtered_chunks: list[str] = []
        async with self.session_factory() as session:
            section_rows = (
                await session.execute(
                    select(PaperSection)
                    .where(PaperSection.paper_id == paper.id)
                    .order_by(PaperSection.ordinal)
                )
            ).scalars().all()
            selected_index = next(
                (index for index, item in enumerate(section_rows) if item.title == section),
                None,
            )
            # 翻译范围:父章节 = 自身(含引言) + 全部后代章节;叶子章节 = 自身
            work_sections = [section]
            if selected_index is not None:
                selected_level = section_rows[selected_index].level
                work_sections = [section]
                for item in section_rows[selected_index + 1 :]:
                    if item.level <= selected_level:
                        break
                    work_sections.append(item.title)
            chunks = (
                await session.execute(
                    select(PaperChunk)
                    .where(
                        PaperChunk.paper_id == paper.id,
                        PaperChunk.section.in_(work_sections),
                    )
                    .order_by(PaperChunk.ordinal)
                )
            ).scalars().all()
            pages = (
                await session.execute(
                    select(PaperPage)
                    .where(PaperPage.paper_id == paper.id)
                    .order_by(PaperPage.page_number)
                )
            ).scalars().all()
        abstract_row = next(
            (
                item
                for item in section_rows
                if item.title == section and item.normalized_title == "abstract"
            ),
            None,
        )
        if abstract_row is not None and (paper.abstract or "").strip():
            # Abstract 翻译始终用 pymupdf 提取的干净摘要: 首页两栏排版下 page chunk
            # 可能被图注/右栏文字穿插(如 Figure1 图注从摘要中间切开), 不用 chunk 作源
            translation_units = [
                {
                    "section": section,
                    "source": paper.abstract.strip(),
                    "page_start": abstract_row.page_start,
                    "page_end": abstract_row.page_end,
                }
            ]
        elif not chunks:
            raise ValueError("所选章节没有可翻译的正文")
        else:
            page_text = {page.page_number: page.text for page in pages}
            sources: list[str] = []
            previous_page: int | None = None
            previous_end: int | None = None
            for chunk in chunks:
                if previous_page != chunk.page_start:
                    previous_end = None
                raw_page = page_text.get(chunk.page_start, "")
                source_start = chunk.char_start
                if previous_end is not None:
                    source_start = max(source_start, previous_end)
                raw_source = raw_page[source_start : chunk.char_end].strip() or self._translation_source(
                    chunk,
                    previous_end,
                )
                source = self._clean_translation_page_prefix(
                    raw_source,
                    starts_at_page_top=source_start == 0,
                )
                source = self._strip_table_rows(source)
                sources.append(source)
                previous_page = chunk.page_start
                previous_end = chunk.char_end
            # 翻译源过滤审计: 原本有正文、清理后为空的 chunk 说明可能被误删
            # (如 strip_visual_regions 把 caption 后正文吞掉), 打 warning 并上报到 done
            filtered_chunks = []
            for chunk, source in zip(chunks, sources):
                raw_c = (page_text.get(chunk.page_start, "") or "")[chunk.char_start : chunk.char_end].strip()
                if (source or "").strip() == "" and len(raw_c) >= 40:
                    logger.warning(
                        f"论文 {paper.id} 翻译源被清理为空: chunk {chunk.chunk_id} "
                        f"(p{chunk.page_start}, 原 {len(raw_c)} 字符), 该段可能被误删"
                    )
                    filtered_chunks.append(
                        f"p{chunk.page_start} 有 {len(raw_c)} 字符被过滤, 可能遗漏正文"
                    )

            translation_units: list[dict[str, Any]] = []
            for chunk, source in zip(chunks, sources):
                if not source:
                    continue
                current = translation_units[-1] if translation_units else None
                if (
                    current
                    and current["section"] == chunk.section
                    and len(current["source"]) + len(source) + 1 <= self.translation_block_size
                ):
                    current["source"] = f'{current["source"]}\n{source}'
                    current["page_end"] = chunk.page_end
                else:
                    translation_units.append(
                        {
                            "section": chunk.section,
                            "source": source,
                            "page_start": chunk.page_start,
                            "page_end": chunk.page_end,
                            "chunk_id": chunk.chunk_id,
                        }
                    )
        # 每个 section 内部独立编号,便于按 (section, block_index) 持久化
        section_counters: dict[str, int] = {}
        for unit in translation_units:
            section_counters[unit["section"]] = section_counters.get(unit["section"], 0)
            unit["block_index"] = section_counters[unit["section"]]
            section_counters[unit["section"]] += 1

        rendered: list[dict[str, Any]] = []
        citations: list[dict[str, Any]] = []
        total = len(translation_units)
        yield {"event": "progress", "stage": "translation", "status": "running", "current": 0, "total": total, "task_id": task_id}
        for index, unit in enumerate(translation_units):
            source = unit["source"]
            unit_section = unit["section"]
            prompt = build_translation_prompt(
                paper.title,
                unit_section,
                unit["page_start"],
                unit["page_end"],
                source,
            )
            try:
                translated = self._translation_response_text(await self.llm.ainvoke(prompt))
                if not translated:
                    raise ValueError("模型返回了空译文")
            except Exception as exc:
                async with self.session_factory() as session:
                    row = await session.scalar(
                        select(PaperTranslationBlock).where(
                            PaperTranslationBlock.paper_id == paper.id,
                            PaperTranslationBlock.section == unit_section,
                            PaperTranslationBlock.block_index == unit["block_index"],
                        )
                    )
                    if row is None:
                        row = PaperTranslationBlock(
                            paper_id=paper.id,
                            task_id=task_id,
                            section=unit_section,
                            block_index=unit["block_index"],
                            page_start=unit["page_start"],
                            page_end=unit["page_end"],
                            source_text=source,
                        )
                        session.add(row)
                    row.status = "failed"
                    row.error_message = str(exc)
                    await session.commit()
                raise
            async with self.session_factory() as session:
                row = await session.scalar(
                    select(PaperTranslationBlock).where(
                        PaperTranslationBlock.paper_id == paper.id,
                        PaperTranslationBlock.section == unit_section,
                        PaperTranslationBlock.block_index == unit["block_index"],
                    )
                )
                if row is None:
                    row = PaperTranslationBlock(
                        paper_id=paper.id,
                        section=unit_section,
                        block_index=unit["block_index"],
                    )
                    session.add(row)
                row.task_id = task_id
                row.page_start = unit["page_start"]
                row.page_end = unit["page_end"]
                row.source_text = source
                row.translated_text = translated
                row.status = "completed"
                row.error_message = ""
                await session.commit()

            block_payload = {
                "section": unit_section,
                "block_index": unit["block_index"],
                "page_start": unit["page_start"],
                "page_end": unit["page_end"],
                "content": translated,
                "status": "completed",
            }
            rendered.append(block_payload)
            citation = {
                "paper_id": paper.id,
                "paper_title": paper.title,
                "page": unit["page_start"],
                "section": unit_section,
                "chunk_id": unit.get("chunk_id", ""),
                "quote": source[:240],
                "verified": True,
                "reason": "translation_source",
            }
            citations.append(citation)
            yield {"event": "block", **block_payload}
            yield {"event": "progress", "stage": "translation", "status": "running", "current": index + 1, "total": total, "task_id": task_id}

        # 翻译章节范围内图表的标题(仅未翻译的),并推送 figure 事件
        figure_events: list[dict[str, Any]] = []
        if work_sections:
            page_min = min(unit["page_start"] for unit in translation_units) if translation_units else None
            page_max = max(unit["page_end"] for unit in translation_units) if translation_units else None
        else:
            page_min = page_max = None
        if page_min is not None:
            async with self.session_factory() as session:
                figure_rows = (
                    await session.execute(
                        select(PaperFigure)
                        .where(
                            PaperFigure.paper_id == paper.id,
                            PaperFigure.page >= page_min,
                            PaperFigure.page <= page_max,
                        )
                        .order_by(PaperFigure.page, PaperFigure.ordinal)
                    )
                ).scalars().all()
            for figure in figure_rows:
                if not figure.caption_translated:
                    try:
                        prompt = build_caption_prompt(figure.caption)
                        translated = self._translation_response_text(await self.llm.ainvoke(prompt)).strip()
                    except Exception as exc:
                        translated = ""
                        logger.warning(f"图表 {figure.id} 标题翻译失败: {exc}")
                    if translated:
                        async with self.session_factory() as session:
                            row = await session.get(PaperFigure, figure.id)
                            if row is not None:
                                row.caption_translated = translated
                                await session.commit()
                figure_events.append(
                    {
                        "figure_id": figure.id,
                        "page": figure.page,
                        "kind": figure.kind,
                        "caption": figure.caption,
                        "caption_translated": figure.caption_translated or "",
                        "image_url": f"/api/v1/papers/{paper.id}/figures/{figure.id}/image",
                    }
                )
                yield {"event": "figure", **figure_events[-1]}

        content = "\n\n".join(item["content"] for item in rendered)
        artifact_id = 0
        async with self.session_factory() as session:
            stored_task = await session.get(PaperTask, task_id)
            stored_task.status = "completed"
            # 父章节(多 section)不单独建产物,整章视图动态聚合各子章节的译文块
            if len(work_sections) == 1:
                artifact = PaperArtifact(
                    paper_id=paper.id,
                    task_id=task_id,
                    artifact_type="translation",
                    title=f"章节翻译：{section}",
                    content_json=_json({"content": content, "section": section, "blocks": rendered}),
                    content_text=content,
                    citations_json=_json(citations),
                )
                session.add(artifact)
                await session.commit()
                await session.refresh(artifact)
                artifact_id = artifact.id
            else:
                await session.commit()
        yield {
            "event": "done",
            "task_id": task_id,
            "artifact_id": artifact_id,
            "content": content,
            "citations": citations,
            "blocks": rendered,
            # 翻译覆盖校验: 若部分段落被过滤(表格数据/图注清理), 提醒可能不完整
            "warnings": list(filtered_chunks),
        }

    async def run_task(
        self,
        paper_id: int,
        task_type: str,
        input_text: str,
        session_id: str = "",
        section: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        valid_types = {"qa", "translation", "presentation", "review"}
        if task_type not in valid_types:
            yield {"event": "error", "detail": f"不支持的任务类型: {task_type}"}
            return
        paper = await self.get_paper(paper_id)
        if paper is None:
            yield {"event": "error", "detail": "论文不存在"}
            return
        if paper.status != "ready":
            yield {"event": "error", "detail": "论文尚未处理完成"}
            return

        task = PaperTask(
            paper_id=paper_id,
            task_type=task_type,
            status="running",
            session_id=session_id,
            input_json=_json({"input_text": input_text, "section": section}),
        )
        async with self.session_factory() as session:
            session.add(task)
            await session.commit()
            await session.refresh(task)
            task_id = task.id

        if task_type == "translation":
            try:
                async for event in self._run_translation(paper, task_id, section):
                    yield event
            except Exception as exc:
                async with self.session_factory() as session:
                    stored_task = await session.get(PaperTask, task_id)
                    stored_task.status = "failed"
                    stored_task.error_code = "translation_failed"
                    stored_task.error_message = str(exc)
                    await session.commit()
                yield {"event": "error", "task_id": task_id, "detail": str(exc)}
            return

        yield {"event": "progress", "stage": "retrieval", "status": "running", "task_id": task_id}
        try:
            # 前端留空时会把任务标题作为输入兜底(如"论文审稿"), 这类泛化输入不适合做检索词,
            # 退化为用论文标题检索, 保证审稿/笔记/提纲能看到全文证据
            generic_inputs = {"与论文对话", "论文审稿", "汇报提纲", "章节翻译"}
            query_input = input_text.strip()
            if not query_input or query_input in generic_inputs:
                query_input = paper.title
            search_query = query_input or section or paper.title
            results = await self.retriever.search(
                paper_id,
                search_query,
                k=20 if task_type == "review" else (12 if task_type == "translation" else (8 if task_type == "presentation" else 8)),
                section=section if task_type == "translation" else None,
            )
            chunks = [result.chunk for result in results]
            if not chunks:
                chunks = await self._load_chunks(paper_id)
                if section and task_type == "translation":
                    chunks = [chunk for chunk in chunks if chunk.section == section]
            if not chunks:
                raise ValueError("当前论文没有可用的检索证据")

            history = await self._load_history(paper_id, session_id) if task_type == "qa" else []
            prompt = build_task_prompt(task_type, paper.title, input_text, chunks, history)
            content = ""
            safe_citations: list[dict[str, Any]] = []
            validation_errors: list[str] = []
            for attempt in range(2):
                response = await self._ainvoke_with_retry(prompt)
                payload = _json_content(response.content if hasattr(response, "content") else response)
                content = str(payload.get("content", "")).strip()
                review_payload: dict[str, Any] | None = None
                if task_type == "review":
                    review_payload = self._normalize_review(payload)
                    content = review_payload.get("summary") or "论文审稿意见已生成。"
                presentation_payload: dict[str, Any] | None = None
                if task_type == "presentation":
                    presentation_payload = self._normalize_presentation(payload)
                    content = presentation_payload.get("markdown") or "汇报提纲已生成。"
                suggestions = [
                    str(item).strip()
                    for item in payload.get("suggestions", [])
                    if isinstance(item, str) and str(item).strip()
                ][:3]
                raw_citations = []
                for item in payload.get("citations", []):
                    if not isinstance(item, dict):
                        continue
                    # 当前论文上下文由路由参数与数据库记录决定，不信任 LLM 返回值。
                    item["paper_id"] = paper_id
                    item["paper_title"] = paper.title
                    try:
                        raw_citations.append(PaperCitation.model_validate(item))
                    except Exception:
                        validation_errors.append("invalid_citation_shape")
                checked = CitationValidator(chunks).validate_many(raw_citations, paper_id)
                validation_errors.extend(result.reason for result in checked if not result.valid)
                safe_citations = [result.citation.model_dump() for result in checked]
                if not validation_errors:
                    break
                if attempt == 0:
                    prompt += "\n上次引用校验失败：" + "、".join(sorted(set(validation_errors))) + "。请仅使用给定证据重答。"
                    validation_errors = []

            if validation_errors:
                # 只要有引用核实通过, 就不再追加整段降级提示(失败的单条由前端逐条标注);
                # 全部未核实时才提示证据不足, 避免误伤证据充分的回答
                verified_count = sum(1 for c in safe_citations if c.get("verified"))
                if verified_count == 0:
                    content = f"{content}\n\n原文未提供充分证据，相关引用已降级。".strip()
            if not content:
                content = "原文未提供充分证据。"

            title_map = {
                "qa": input_text[:60] or "论文问答",
                "translation": f"章节翻译：{section or input_text}",
                "presentation": "论文汇报提纲",
                "review": "论文审稿意见",
            }
            async with self.session_factory() as session:
                stored_task = await session.get(PaperTask, task_id)
                stored_task.status = "completed"
                if task_type == "presentation" and presentation_payload is not None:
                    artifact_content: dict[str, Any] = presentation_payload
                elif task_type == "review" and review_payload is not None:
                    artifact_content = review_payload
                else:
                    artifact_content = {"content": content}
                review_json = artifact_content
                artifact = PaperArtifact(
                    paper_id=paper_id,
                    task_id=task_id,
                    artifact_type=task_type,
                    title=title_map[task_type],
                    content_json=_json(review_json),
                    content_text=content,
                    citations_json=_json(safe_citations),
                )
                session.add(artifact)
                if task_type == "qa" and session_id:
                    session.add_all(
                        [
                            PaperMessage(
                                paper_id=paper_id,
                                session_id=session_id,
                                role="user",
                                content=input_text,
                            ),
                            PaperMessage(
                                paper_id=paper_id,
                                session_id=session_id,
                                role="assistant",
                                content=content,
                                citations_json=_json(safe_citations),
                                suggestions_json=_json(suggestions),
                            ),
                        ]
                    )
                await session.commit()
                await session.refresh(artifact)
                artifact_id = artifact.id

            for offset in range(0, len(content), 80):
                yield {"event": "token", "content": content[offset : offset + 80]}
            yield {
                "event": "done",
                "task_id": task_id,
                "artifact_id": artifact_id,
                "content": content,
                "citations": safe_citations,
                "suggestions": suggestions,
            }
        except Exception as exc:
            async with self.session_factory() as session:
                stored_task = await session.get(PaperTask, task_id)
                stored_task.status = "failed"
                stored_task.error_code = "task_failed"
                stored_task.error_message = str(exc)
                await session.commit()
            yield {"event": "error", "task_id": task_id, "detail": str(exc)}

    async def run_library_qa(
        self,
        input_text: str,
        session_id: str = "",
    ) -> AsyncIterator[dict[str, Any]]:
        """全库问答: LangGraph 图驱动(节点事件 + 完成后重放 token 流)。"""
        history = await self._load_history(0, session_id)
        initial = {
            "session_id": session_id,
            "input_text": input_text,
            "history": history,
            "query": input_text.strip() or "论文综述",
            "retry_count": 0,
            "candidates": [],
            "evidence": [],
            "relevance_scores": [],
        }
        config = {"configurable": {
            "session_factory": self.session_factory,
            "retriever": self.retriever,
            "llm": self.llm,
        }}

        final_content = ""
        final_citations: list[dict[str, Any]] = []
        try:
            # 节点用 ainvoke 不产 messages 流, 只收 updates(节点完成后一次性取 content)
            async for _mode, event in self.library_graph.astream(
                initial, config=config, stream_mode=["updates"],
            ):
                for node_name, node_output in (event or {}).items():
                    # generate/chat_node/catalog_node/general_chat_node 都把最终回答写入 state.content
                    if node_name in ("generate", "chat_node", "catalog_node", "general_chat_node"):
                        if isinstance(node_output, dict) and node_output.get("content"):
                            final_content = node_output.get("content", final_content)
                    if node_name == "cite_verify":
                        if isinstance(node_output, dict):
                            final_citations = node_output.get("citations", [])
                    extra = {
                        k: v for k, v in (node_output or {}).items()
                        if k not in ("candidates", "evidence", "raw_citations")
                    }
                    yield {"event": "node", "node": node_name, "status": "completed", **extra}
        except Exception as exc:
            yield {"event": "error", "detail": f"全库问答失败: {exc}"}
            return

        if not final_content:
            final_content = "未能在论文库中找到充分证据回答该问题。"
        if session_id:
            async with self.session_factory() as session:
                session.add_all([
                    PaperMessage(paper_id=0, session_id=session_id, role="user", content=input_text),
                    PaperMessage(paper_id=0, session_id=session_id, role="assistant", content=final_content,
                                 citations_json=_json(final_citations)),
                ])
                await session.commit()
        # 图节点用 ainvoke 不产逐字 token: 完成后按 80 字符切片重放, 恢复打字机效果
        for offset in range(0, len(final_content), 80):
            yield {"event": "token", "content": final_content[offset:offset + 80]}
        yield {"event": "done", "content": final_content, "citations": final_citations}

    async def get_library_history(self, session_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """读取全库问答会话历史(paper_id=0 的 paper_messages)。"""
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(PaperMessage)
                    .where(PaperMessage.paper_id == 0, PaperMessage.session_id == session_id)
                    .order_by(PaperMessage.created_at.desc(), PaperMessage.id.desc())
                    .limit(limit)
                )
            ).scalars().all()
        rows = list(reversed(rows))
        return [
            {
                "role": row.role,
                "content": row.content,
                "citations": json.loads(row.citations_json or "[]"),
                "timestamp": row.created_at.isoformat() if row.created_at else "",
            }
            for row in rows
        ]

    async def delete_paper(self, paper_id: int) -> bool:
        paper = await self.get_paper(paper_id)
        if paper is None:
            return False
        source = self.file_path(paper)
        child_models = [
            PaperMessage,
            PaperTranslationBlock,
            PaperArtifact,
            PaperTask,
            PaperChunk,
            PaperSection,
            PaperPage,
            PaperFigure,
        ]
        async with self.session_factory() as session:
            for model in child_models:
                await session.execute(delete(model).where(model.paper_id == paper_id))
            await session.execute(delete(Paper).where(Paper.id == paper_id))
            await session.commit()
        figures_dir = self.files_dir.parent / "figures" / str(paper_id)
        if figures_dir.exists():
            import shutil

            shutil.rmtree(figures_dir, ignore_errors=True)
        if source.exists():
            source.unlink()
        self.retriever.delete(paper_id)
        self._event_history.pop(paper_id, None)
        return True
