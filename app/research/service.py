"""导入任务服务: paper_imports 持久化 + 串行下载队列 + 解析交接。

队列策略: 串行处理所有 pending 任务, 每项之间按 delay 限速;
下载成功后调用现有 PaperService.create_paper + process_paper 完成入库。

task_timeout: 单个任务的总超时(默认 900s)。解析阶段由 MinerU 驱动,
首次运行需加载 PDF-Extract-Kit 模型(权重下载+加载), 单次可能远超 300s,
故默认放宽到 15 分钟; 超时后该行标 failed 注明"处理超时", 队列继续下一项。

部署假设: 单进程部署(进程内 asyncio.Lock 互斥防重入)。
跨进程/多实例部署时锁不共享, 需改为行级认领(如原子 UPDATE 抢占 pending 行)。
"""

import asyncio
import re
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.paper import Paper
from app.models.research import PaperImport
from app.research.schemas import ImportItem, ImportTaskOut

# ---- 状态常量 ----
STATUS_PENDING = "pending"
STATUS_DOWNLOADING = "downloading"
STATUS_PARSING = "parsing"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

# ---- 进度常量 ----
PROGRESS_DOWNLOADING = 10
PROGRESS_PARSING = 50
PROGRESS_DONE = 100

# 崩溃恢复: 进程中断时可能残留的非终态
STALE_STATUSES = (STATUS_DOWNLOADING, STATUS_PARSING)
STALE_RESET_NOTE = "上次运行中断, 已重置"


def _task_out(row: PaperImport) -> ImportTaskOut:
    """内部校验并转换为对外结构; 公开方法统一返回 model_dump() 的 dict。"""
    return ImportTaskOut(
        id=row.id,
        title=row.title,
        source=row.source,
        status=row.status,
        progress=row.progress,
        error_message=row.error_message,
        paper_id=row.paper_id,
        created_at=row.created_at.isoformat() if row.created_at else "",
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
    )


def _safe_filename(title: str, source: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", title)[:80] or "paper"
    return f"{source}_{name}.pdf"


class ImportService:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        downloader,
        paper_service,
        files_dir: Path,
        delay: float = 4.0,
        # 单任务总超时(秒): 默认 900s(15 分钟), 覆盖 MinerU 首次加载
        # PDF-Extract-Kit 模型(下载模型权重可达数百 MB) + 解析整篇论文;
        # 生产按需调大, 测试传显式小值(如 0.1s)验证超时行为
        task_timeout: float = 900.0,
    ):
        self.session_factory = session_factory
        self.downloader = downloader
        self.paper_service = paper_service
        self.files_dir = files_dir
        self.delay = delay
        self.task_timeout = task_timeout
        self._queue_lock = asyncio.Lock()

    async def create_imports(self, items: list[ImportItem]) -> list[dict]:
        async with self.session_factory() as session:
            rows = [
                PaperImport(
                    title=item.title,
                    year=item.year,
                    venue=item.venue,
                    source=item.source,
                    external_id=item.external_id,
                    doi=item.doi,
                    pdf_url=item.pdf_url,
                    page_url=item.page_url,
                    status=STATUS_PENDING,
                    progress=0,
                )
                for item in items
            ]
            session.add_all(rows)
            await session.commit()
            for row in rows:
                await session.refresh(row)
            return [_task_out(r).model_dump() for r in rows]

    async def list_imports(self) -> list[dict]:
        async with self.session_factory() as session:
            rows = (await session.execute(select(PaperImport).order_by(PaperImport.created_at.desc()))).scalars().all()
            return [_task_out(r).model_dump() for r in rows]

    async def get_import(self, import_id: int) -> dict | None:
        async with self.session_factory() as session:
            row = await session.get(PaperImport, import_id)
            return _task_out(row).model_dump() if row else None

    async def _update(self, import_id: int, **fields) -> None:
        async with self.session_factory() as session:
            row = await session.get(PaperImport, import_id)
            if row is None:
                return
            for key, value in fields.items():
                setattr(row, key, value)
            await session.commit()

    async def retry(self, import_id: int) -> dict | None:
        """仅终态(failed/done)可重置为 pending; 非终态返回 None, 防止并发二次处理。"""
        async with self.session_factory() as session:
            row = await session.get(PaperImport, import_id)
            if row is None:
                return None
            if row.status not in (STATUS_FAILED, STATUS_DONE):
                # 正在处理中的行(pending/downloading/parsing)不得重置
                return None
            row.status = STATUS_PENDING
            row.progress = 0
            row.error_message = ""
            await session.commit()
        return await self.get_import(import_id)

    async def _reset_stale(self) -> None:
        """崩溃恢复: 把上次中断遗留的非终态行(downloading/parsing)重置为 pending。"""
        async with self.session_factory() as session:
            rows = (
                await session.execute(select(PaperImport).where(PaperImport.status.in_(STALE_STATUSES)))
            ).scalars().all()
            for row in rows:
                row.status = STATUS_PENDING
                row.progress = 0
                note = (row.error_message + " | " if row.error_message else "") + STALE_RESET_NOTE
                row.error_message = note
            await session.commit()
            if rows:
                logger.info(f"重置 {len(rows)} 个中断任务 -> pending")

    async def run_pending(self) -> None:
        """串行处理所有 pending 任务(进程内并发安全)。"""
        if self._queue_lock.locked():
            logger.info("导入队列已在运行, 跳过本次触发")
            return
        async with self._queue_lock:
            await self._reset_stale()
            while True:
                async with self.session_factory() as session:
                    row = (
                        await session.execute(
                            select(PaperImport)
                            .where(PaperImport.status == STATUS_PENDING)
                            .order_by(PaperImport.created_at.asc())
                            .limit(1),
                        )
                    ).scalar_one_or_none()
                if row is None:
                    break
                import_id = row.id
                try:
                    item = ImportItem(
                        source=row.source or "arxiv",
                        title=row.title,
                        year=row.year,
                        venue=row.venue or "",
                        doi=row.doi,
                        pdf_url=row.pdf_url,
                        page_url=row.page_url,
                        external_id=row.external_id,
                    )
                    await asyncio.wait_for(self._process_one(import_id, item), timeout=self.task_timeout)
                except asyncio.TimeoutError:
                    # 单任务超时(慢速下载/卡死解析): 标 failed 注明超时, 队列继续下一项,
                    # 避免 head-of-line blocking(进度保留在超时发生的阶段)
                    logger.warning(f"导入处理超时 import#{import_id}: 超过 {self.task_timeout:.0f}s")
                    await self._update(import_id, status=STATUS_FAILED, error_message=f"处理超时(>{self.task_timeout:.0f}s)")
                except Exception as exc:
                    # 脏数据(空 title / 非法 source 等)导致 ImportItem 构造失败:
                    # 单行标 failed, 不中断队列, 避免该行永久 pending 毒化整条队列
                    logger.exception(f"构造导入项失败 import#{import_id}: {exc}")
                    await self._update(import_id, status=STATUS_FAILED, progress=0, error_message=str(exc))
                if self.delay > 0:
                    await asyncio.sleep(self.delay)

    async def _process_one(self, import_id: int, item: ImportItem) -> None:
        phase_progress = 0
        try:
            self.files_dir.mkdir(parents=True, exist_ok=True)  # 幂等, 确保下载目录存在
            await self._update(import_id, status=STATUS_DOWNLOADING, progress=PROGRESS_DOWNLOADING, error_message="")
            phase_progress = PROGRESS_DOWNLOADING
            result = await self.downloader.download(item, self.files_dir)
            if not result.ok:
                await self._update(import_id, status=STATUS_FAILED, progress=phase_progress, error_message=result.message)
                return
            await self._update(import_id, status=STATUS_PARSING, progress=PROGRESS_PARSING, error_message="")
            phase_progress = PROGRESS_PARSING
            content = await asyncio.to_thread(lambda: open(result.path, "rb").read())
            filename = _safe_filename(item.title, item.source)
            paper = await self.paper_service.create_paper(filename, content)
            # 先落 paper_id: 即使后续 process_paper 抛异常, 也不会产生孤儿 paper
            await self._update(import_id, paper_id=paper.id)
            await self.paper_service.process_paper(paper.id)
            paper_error = await self._paper_failed(paper.id)
            if paper_error is not None:
                # 论文解析失败(process_paper 内部吞掉的异常) → import 不得标 done
                await self._update(
                    import_id, status=STATUS_FAILED, progress=phase_progress, error_message=paper_error
                )
                logger.warning(f"论文解析失败: {item.title} -> paper#{paper.id}: {paper_error}")
                return
            await self._update(import_id, status=STATUS_DONE, progress=PROGRESS_DONE, paper_id=paper.id)
            logger.info(f"导入完成: {item.title} -> paper#{paper.id}")
        except Exception as exc:
            logger.exception(f"导入失败 import#{import_id}: {exc}")
            await self._update(import_id, status=STATUS_FAILED, progress=phase_progress, error_message=str(exc))

    async def _paper_failed(self, paper_id: int) -> str | None:
        """process_paper 后核对论文状态: paper 为 failed 时返回其 error_message, 否则 None。"""
        async with self.session_factory() as session:
            paper = await session.get(Paper, paper_id)
            if paper is None:
                return None  # 论文行不存在(如测试/外部 service)视为解析成功
            if paper.status == STATUS_FAILED:
                return paper.error_message or "论文解析失败"
            return None
