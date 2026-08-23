"""ImportService 队列与入库交接测试。

覆盖: 批量建行 / 串行队列 / 失败记录 / 重试 / 脏数据隔离 / 崩溃 stale 恢复 /
解析交接(paper failed 透传) / 并发防重入。
"""

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.base import Base
from app.models.paper import Paper
from app.models.research import PaperImport
from app.research.downloader import DownloadResult
from app.research.service import ImportService
from app.research.schemas import ImportItem


class FakeDownloader:
    def __init__(self, ok=True, level="L1"):
        self.ok, self.level = ok, level
        self.calls = []

    async def download(self, item, dest_dir):
        self.calls.append(item)
        if not self.ok:
            return DownloadResult(ok=False, level="L4", message="手动下载")
        target = Path(dest_dir) / "paper.pdf"
        target.write_bytes(b"%PDF-fake")
        return DownloadResult(ok=True, level=self.level, path=str(target))


class FakePaperService:
    def __init__(self):
        self.created = []

    async def create_paper(self, filename, content):
        self.created.append((filename, content))
        return type("P", (), {"id": len(self.created)})()

    async def process_paper(self, paper_id):
        return None


class FailingPaperService:
    """process_paper 内部吞掉解析异常的场景: 写真实 Paper 行并置 failed。"""

    def __init__(self, session_factory):
        self.session_factory = session_factory
        self.created = []

    async def create_paper(self, filename, content):
        self.created.append((filename, content))
        async with self.session_factory() as session:
            paper = Paper(
                original_filename=filename,
                stored_filename=filename,
                title=filename,
                status="parsing",
            )
            session.add(paper)
            await session.commit()
            await session.refresh(paper)
            return paper

    async def process_paper(self, paper_id):
        async with self.session_factory() as session:
            paper = await session.get(Paper, paper_id)
            paper.status = "failed"
            paper.error_message = "解析失败: 版面识别超时"
            await session.commit()


class RaisingPaperService:
    """process_paper 抛非解析异常的 fake: 验证 paper_id 已先落库(无孤儿 paper)。"""

    def __init__(self):
        self.created = []

    async def create_paper(self, filename, content):
        self.created.append(filename)
        return type("P", (), {"id": 42})()

    async def process_paper(self, paper_id):
        raise RuntimeError("process_paper boom")


def make_service(session_factory, tmp_path, downloader, paper_service):
    return ImportService(
        session_factory=session_factory,
        downloader=downloader,
        paper_service=paper_service,
        files_dir=tmp_path / "files",
        delay=0.0,
    )


@pytest.mark.asyncio
async def test_create_imports_and_run_queue(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'svc.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    service = make_service(session_factory, tmp_path, FakeDownloader(ok=True), FakePaperService())
    tasks = await service.create_imports([ImportItem(source="arxiv", title="Paper X", pdf_url="https://x/p.pdf")])
    assert len(tasks) == 1
    assert tasks[0]["status"] == "pending"

    await service.run_pending()

    async with session_factory() as session:
        row = (await session.execute(select(PaperImport).where(PaperImport.id == tasks[0]["id"]))).scalar_one()
        assert row.status == "done"
        assert row.paper_id == 1
        assert row.progress == 100


@pytest.mark.asyncio
async def test_create_imports_persists_year(tmp_path):
    """create_imports 带 year → paper_imports 行 year 落库正确。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'svc_year.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    service = make_service(session_factory, tmp_path, FakeDownloader(ok=True), FakePaperService())
    tasks = await service.create_imports(
        [ImportItem(source="openalex", title="CV Paper", year=2023, doi="10.1109/CVPR.2023.1")]
    )
    async with session_factory() as session:
        row = (await session.execute(select(PaperImport).where(PaperImport.id == tasks[0]["id"]))).scalar_one()
        assert row.year == 2023
    await engine.dispose()


@pytest.mark.asyncio
async def test_run_pending_reconstructs_item_year(tmp_path):
    """run_pending 从 DB 行重建 ImportItem 时带回 year(队列路径不再丢失, CVF 可精准按年查找)。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'svc_year2.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    downloader = FakeDownloader(ok=True)
    service = make_service(session_factory, tmp_path, downloader, FakePaperService())
    tasks = await service.create_imports(
        [ImportItem(source="openalex", title="CV Paper", year=2023, doi="10.1109/CVPR.2023.1", page_url="https://x.org")]
    )
    await service.run_pending()

    assert len(downloader.calls) == 1
    assert downloader.calls[0].year == 2023  # 重建后的 ImportItem 带 year
    await engine.dispose()


@pytest.mark.asyncio
async def test_failed_import_keeps_error(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'svc2.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    service = make_service(session_factory, tmp_path, FakeDownloader(ok=False), FakePaperService())
    tasks = await service.create_imports([ImportItem(source="arxiv", title="Paper Y", page_url="https://y/p")])
    await service.run_pending()

    async with session_factory() as session:
        row = (await session.execute(select(PaperImport).where(PaperImport.id == tasks[0]["id"]))).scalar_one()
        assert row.status == "failed"
        assert row.error_message != ""
        assert row.progress == 10  # 下载阶段失败, 进度停在 downloading 阶段


@pytest.mark.asyncio
async def test_retry_resets_status(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'svc3.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    downloader = FakeDownloader(ok=False)
    service = make_service(session_factory, tmp_path, downloader, FakePaperService())
    tasks = await service.create_imports([ImportItem(source="arxiv", title="Paper Z", page_url="https://z/p")])
    await service.run_pending()
    downloader.ok = True
    await service.retry(tasks[0]["id"])
    await service.run_pending()

    async with session_factory() as session:
        row = (await session.execute(select(PaperImport).where(PaperImport.id == tasks[0]["id"]))).scalar_one()
        assert row.status == "done"
        assert row.paper_id == 1


# ---- Important 1: 脏数据不毒化队列 ----

@pytest.mark.asyncio
async def test_dirty_row_does_not_poison_queue(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'svc_dirty.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    service = make_service(session_factory, tmp_path, FakeDownloader(ok=True), FakePaperService())
    # 脏行: title 为空, ImportItem(title="") 构造必抛 ValidationError(先插入, 排在队列最前)
    async with session_factory() as session:
        dirty = PaperImport(title="", source="arxiv", status="pending", progress=0)
        session.add(dirty)
        await session.commit()
        await session.refresh(dirty)
    tasks = await service.create_imports([ImportItem(source="arxiv", title="Good Paper", pdf_url="https://g/p.pdf")])

    await service.run_pending()

    async with session_factory() as session:
        dirty_row = await session.get(PaperImport, dirty.id)
        assert dirty_row.status == "failed"
        assert dirty_row.error_message != ""
        good_row = (await session.execute(select(PaperImport).where(PaperImport.id == tasks[0]["id"]))).scalar_one()
        assert good_row.status == "done"
        assert good_row.paper_id == 1


# ---- Important 2: 崩溃 stale 恢复 ----

@pytest.mark.asyncio
@pytest.mark.parametrize("stale_status", ["downloading", "parsing"])
async def test_stale_nonterminal_row_is_reset_and_processed(tmp_path, stale_status):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / f'svc_stale_{stale_status}.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    service = make_service(session_factory, tmp_path, FakeDownloader(ok=True), FakePaperService())
    async with session_factory() as session:
        stale = PaperImport(title="Stale Paper", source="arxiv", status=stale_status, progress=50)
        session.add(stale)
        await session.commit()
        await session.refresh(stale)

    await service.run_pending()

    async with session_factory() as session:
        row = await session.get(PaperImport, stale.id)
        assert row.status == "done"
        assert row.progress == 100
        assert row.paper_id == 1


# ---- Important 3: 解析交接状态一致 + 无孤儿 paper ----

@pytest.mark.asyncio
async def test_import_failed_when_paper_failed(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'svc_paperfail.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    service = make_service(session_factory, tmp_path, FakeDownloader(ok=True), FailingPaperService(session_factory))
    tasks = await service.create_imports([ImportItem(source="arxiv", title="Paper F", pdf_url="https://f/p.pdf")])
    await service.run_pending()

    async with session_factory() as session:
        row = (await session.execute(select(PaperImport).where(PaperImport.id == tasks[0]["id"]))).scalar_one()
        assert row.status == "failed"           # 论文解析失败 → import 不能标 done
        assert row.paper_id is not None          # paper 已创建并关联, 非孤儿
        assert "解析失败" in row.error_message    # 透传 paper.error_message
        assert row.progress == 50                # parsing 阶段失败, 进度停在 50


@pytest.mark.asyncio
async def test_process_paper_exception_keeps_paper_id(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'svc_paperraise.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    service = make_service(session_factory, tmp_path, FakeDownloader(ok=True), RaisingPaperService())
    tasks = await service.create_imports([ImportItem(source="arxiv", title="Paper R", pdf_url="https://r/p.pdf")])
    await service.run_pending()

    async with session_factory() as session:
        row = (await session.execute(select(PaperImport).where(PaperImport.id == tasks[0]["id"]))).scalar_one()
        assert row.status == "failed"
        assert row.paper_id == 42               # create_paper 后立刻落 paper_id, 无孤儿
        assert "process_paper boom" in row.error_message
        assert row.progress == 50               # parsing 阶段失败, 进度停在 50


# ---- Important 5: 并发防重入 ----

@pytest.mark.asyncio
async def test_concurrent_run_pending_no_double_process(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'svc_conc.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    downloader = FakeDownloader(ok=True)
    paper_service = FakePaperService()
    service = make_service(session_factory, tmp_path, downloader, paper_service)
    tasks = await service.create_imports(
        [
            ImportItem(source="arxiv", title="Paper A", pdf_url="https://a/p.pdf"),
            ImportItem(source="arxiv", title="Paper B", pdf_url="https://b/p.pdf"),
        ]
    )
    await asyncio.gather(service.run_pending(), service.run_pending())

    assert len(downloader.calls) == 2
    assert len(paper_service.created) == 2
    async with session_factory() as session:
        rows = (await session.execute(select(PaperImport).order_by(PaperImport.id))).scalars().all()
        assert [r.status for r in rows] == ["done", "done"]
        assert sorted(r.paper_id for r in rows) == [1, 2]
# ============================================================
# 修复项 3: retry 状态守卫(非终态不得重置为 pending)
# ============================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("nonterminal", ["pending", "downloading", "parsing"])
async def test_retry_nonterminal_status_keeps_status_and_returns_none(tmp_path, nonterminal):
    """retry 只允许终态(failed/done)重置: 非终态返回 None 且状态保持不变。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'svc_retry_guard.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    service = make_service(session_factory, tmp_path, FakeDownloader(ok=True), FakePaperService())
    async with session_factory() as session:
        row = PaperImport(title="Busy Paper", source="arxiv", status=nonterminal, progress=10)
        session.add(row)
        await session.commit()
        await session.refresh(row)
        import_id = row.id

    result = await service.retry(import_id)
    assert result is None  # 非终态 retry → None(router 对 None 返回 404)
    async with session_factory() as session:
        row = await session.get(PaperImport, import_id)
        assert row.status == nonterminal  # 状态不得被重置
        assert row.progress == 10
        assert row.error_message == ""


# ============================================================
# 修复项 2: run_pending 单任务超时(head-of-line blocking)
# ============================================================


class HangingOnceDownloader:
    """第一次 download 永久挂起(模拟慢速/卡死下载), 之后正常。"""

    def __init__(self):
        self.calls = []

    async def download(self, item, dest_dir):
        self.calls.append(item)
        if len(self.calls) == 1:
            await asyncio.Event().wait()  # 永不 set → 由 run_pending 的任务超时取消
        target = Path(dest_dir) / "paper.pdf"
        target.write_bytes(b"%PDF-fake")
        return DownloadResult(ok=True, level="L1", path=str(target))


@pytest.mark.asyncio
async def test_run_pending_task_timeout_marks_failed_and_continues(tmp_path):
    """卡死的单任务在 task_timeout 后被标 failed(注明超时), 队列继续处理下一项。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'svc_timeout.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    downloader = HangingOnceDownloader()
    service = ImportService(
        session_factory=session_factory,
        downloader=downloader,
        paper_service=FakePaperService(),
        files_dir=tmp_path / "files",
        delay=0.0,
        task_timeout=0.1,  # 测试用小超时(生产默认 900s, 覆盖 MinerU 首次模型加载)
    )
    tasks = await service.create_imports(
        [
            ImportItem(source="arxiv", title="Hang Paper", pdf_url="https://h/p.pdf"),
            ImportItem(source="arxiv", title="Good Paper", pdf_url="https://g/p.pdf"),
        ]
    )
    assert len(tasks) == 2
    # 修复前 run_pending 会无限挂起: 外层 wait_for 兜底, 让 RED 阶段快速失败而非卡死
    await asyncio.wait_for(service.run_pending(), timeout=5.0)

    assert len(downloader.calls) == 2  # 两个任务都被尝试处理
    async with session_factory() as session:
        rows = (await session.execute(select(PaperImport).order_by(PaperImport.id))).scalars().all()
        assert len(rows) == 2
        assert sorted(r.status for r in rows) == ["done", "failed"]
        failed = next(r for r in rows if r.status == "failed")
        assert "处理超时" in failed.error_message  # 超时行标 failed 且注明原因
        done = next(r for r in rows if r.status == "done")
        assert done.paper_id == 1  # 卡死行未建 paper, 正常行拿到 paper#1


@pytest.mark.asyncio
async def test_default_task_timeout_is_900(tmp_path):
    """生产默认单任务超时应为 900s: 覆盖 MinerU 首次加载 PDF-Extract-Kit 模型 + 解析。"""
    service = ImportService(
        session_factory=object(),
        downloader=object(),
        paper_service=object(),
        files_dir=tmp_path / "files",
    )
    assert service.task_timeout == 900.0


# ============================================================
# 修复项 1: facade 注入共享 searchers + 共享 client 由 aclose 关闭
# ============================================================


@pytest.mark.asyncio
async def test_research_facade_injects_shared_searchers_and_closes_client(monkeypatch):
    """SearchAgent 必须复用已创建的 searchers(共享同一 client), facade.aclose 关闭共享 client。"""
    import app.api.dependencies as deps
    import app.research.searchers as searchers_mod

    monkeypatch.setattr(deps, "_research_service", None)  # 重置单例, 避免跨测试污染
    monkeypatch.setattr(deps, "_get_llm", lambda: object())
    monkeypatch.setattr(deps, "get_paper_service", lambda: FakePaperService())

    created_clients = []

    class FakeClient:
        def __init__(self):
            self.closed = False
            created_clients.append(self)

        async def aclose(self):
            self.closed = True

    def fake_make_client(proxy, timeout):
        return FakeClient()

    monkeypatch.setattr(searchers_mod, "_make_client", fake_make_client)

    facade = deps.get_research_service()
    try:
        # 只应创建 1 个共享 client(修复前 agent 自建 searcher 会额外再建 2 个)
        assert len(created_clients) == 1
        # agent 使用注入的 searchers: arxiv/openalex/S2 三个 searcher 共享同一 client 且都不拥有它
        assert len(facade.agent.searchers) == 3
        assert {s.SOURCE_NAME for s in facade.agent.searchers} == {"arxiv", "openalex", "semantic_scholar"}
        assert all(s.client is facade.agent.searchers[0].client for s in facade.agent.searchers)
        assert all(s._owns_client is False for s in facade.agent.searchers)
    finally:
        await facade.aclose()
    # 注入的共享 client(searcher 不拥有)必须由 facade.aclose 负责关闭
    assert created_clients[0].closed


# ============================================================
# 修复项 4: downloader client 注入 research_proxy
# ============================================================


@pytest.mark.asyncio
async def test_facade_downloader_vpn_disabled_by_default(monkeypatch):
    """facade 装配的 downloader 默认 enable_vpn_download=False(L3 浏览器下载停用)。"""
    import app.api.dependencies as deps

    monkeypatch.setattr(deps, "_research_service", None)
    monkeypatch.setattr(deps, "_get_llm", lambda: object())
    monkeypatch.setattr(deps, "get_paper_service", lambda: FakePaperService())

    facade = deps.get_research_service()
    try:
        assert facade.imports.downloader.enable_vpn_download is False
        assert facade.imports.downloader.browser is not None  # browser 仍注入, 供未来启用
    finally:
        await facade.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("proxy", "expected"),
    [("", None), ("http://proxy.local:8080", "http://proxy.local:8080")],
)
async def test_downloader_client_proxy_from_settings(monkeypatch, proxy, expected):
    """Downloader 的 httpx client 应携带 research_proxy(空串不设置)。"""
    import app.api.dependencies as deps
    import httpx

    monkeypatch.setattr(deps, "_research_service", None)
    monkeypatch.setattr(deps, "_get_llm", lambda: object())
    monkeypatch.setattr(deps, "get_paper_service", lambda: FakePaperService())
    monkeypatch.setattr(deps.settings, "research_proxy", proxy)

    client_kwargs = []
    real_client = httpx.AsyncClient

    def spy(*args, **kwargs):
        client_kwargs.append(kwargs)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("httpx.AsyncClient", spy)

    facade = deps.get_research_service()
    try:
        # downloader client 特征: follow_redirects=True(searcher 的 client 无此参数)
        dl_calls = [kw for kw in client_kwargs if kw.get("follow_redirects") is True]
        assert dl_calls, "未创建 downloader client"
        assert dl_calls[0].get("proxy") == expected
    finally:
        await facade.aclose()
