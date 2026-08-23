"""
数据库基础设施测试

验证：
- Engine 创建成功
- Session 可以正常获取
- SQLite 数据库文件正常创建
- check_database_connection 返回正确状态
- init_db 不报错（当前无 Model 定义）
"""

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.database import (
    async_session,
    check_database_connection,
    engine,
    get_session,
    init_db,
)


class TestEngine:
    """Engine 创建相关测试"""

    def test_engine_is_created(self):
        """验证 engine 实例存在"""
        assert engine is not None

    def test_engine_url_configured(self):
        """验证 engine 使用了正确的数据库 URL"""
        url = str(engine.url)
        assert "aiosqlite" in url


class TestSession:
    """Session 相关测试"""

    @pytest.mark.asyncio
    async def test_async_session_is_configured(self):
        """验证 async_sessionmaker 已配置"""
        assert async_session is not None

    @pytest.mark.asyncio
    async def test_get_session_yields_async_session(self):
        """验证 get_session 返回 AsyncSession 实例"""
        async for session in get_session():
            assert isinstance(session, AsyncSession)
            break

    @pytest.mark.asyncio
    async def test_session_can_execute_sql(self):
        """验证 Session 可以执行 SQL"""
        async with async_session() as session:
            result = await session.execute(text("SELECT 1"))
            row = result.scalar()
            assert row == 1


class TestConnectionCheck:
    """数据库连接检查测试"""

    @pytest.mark.asyncio
    async def test_check_database_connection_connected(self):
        """验证连接检查返回 connected 状态"""
        result = await check_database_connection()
        assert result["database"] == "connected"


class TestInitDb:
    """数据库初始化测试"""

    @pytest.mark.asyncio
    async def test_init_db_does_not_raise(self):
        """验证 init_db 正常执行（当前无 Model，不会创建表）"""
        await init_db()

    def test_init_db_startup_path_registers_paper_imports(self):
        """
        生产启动路径回归(关键): main.py 的 lifespan 只 import app.database 后调用 init_db()。
        database 模块自身必须注册全部 ORM 模型, 否则 create_all 不会创建 paper_imports 表。
        用全新解释器验证, 避免 pytest 收集顺序导致其他测试文件已 import app.models 的干扰。
        """
        import subprocess
        import sys

        project_root = Path(__file__).resolve().parents[1]
        code = (
            "import app.database; "
            "from app.database.base import Base; "
            "assert 'paper_imports' in Base.metadata.tables"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, (
            "生产启动路径未注册 paper_imports 表(init_db 将不会创建它), stderr: "
            + result.stderr.strip()
        )

    @pytest.mark.asyncio
    async def test_publication_year_migration_is_idempotent(self, tmp_path):
        """
        旧版 papers 表(无 publication_year 列)经迁移逻辑后补列并建索引,
        重复迁移幂等(不重复 ALTER、无异常)。
        """
        from app.database.database import _migrate_legacy_papers

        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
        async with engine.begin() as conn:
            # 旧版 schema: 没有 publication_year 列
            await conn.execute(
                text(
                    "CREATE TABLE papers ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "title VARCHAR(512) NOT NULL DEFAULT '')"
                )
            )
            rows = (await conn.execute(text("PRAGMA table_info(papers)"))).fetchall()
            assert [r[1] for r in rows] == ["id", "title"]

            # 首次迁移: 补列 + 建索引
            await _migrate_legacy_papers(conn)
            rows = (await conn.execute(text("PRAGMA table_info(papers)"))).fetchall()
            names = [r[1] for r in rows]
            assert "publication_year" in names
            types = {r[1]: r[2] for r in rows}
            assert types["publication_year"] == "INTEGER"

            # 新列可插入 NULL
            await conn.execute(
                text("INSERT INTO papers (title, publication_year) VALUES ('t', NULL)")
            )

            # 索引已创建(与 SQLAlchemy 自动索引名一致)
            idx = (
                await conn.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'index' AND name = 'ix_papers_publication_year'"
                    )
                )
            ).scalar()
            assert idx == "ix_papers_publication_year"

            # 再次迁移: 幂等, 不重复 ALTER、无异常
            # (SQLite 对重复 ADD COLUMN 会抛错, 若迁移逻辑重复 ALTER 此处将失败)
            await _migrate_legacy_papers(conn)
            rows = (await conn.execute(text("PRAGMA table_info(papers)"))).fetchall()
            assert [r[1] for r in rows].count("publication_year") == 1
        await engine.dispose()


class TestPaperImportsMigration:
    """存量 paper_imports 表的 year 列迁移(幂等)。"""

    @pytest.mark.asyncio
    async def test_paper_imports_year_migration_is_idempotent(self, tmp_path):
        """存量 paper_imports 表(无 year 列)经迁移逻辑补列, 重复迁移幂等。"""
        from app.database.database import _migrate_legacy_paper_imports

        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy_imports.db'}")
        async with engine.begin() as conn:
            # 旧版 schema: 没有 year 列
            await conn.execute(
                text(
                    "CREATE TABLE paper_imports ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "title VARCHAR(512) NOT NULL DEFAULT '')"
                )
            )
            rows = (await conn.execute(text("PRAGMA table_info(paper_imports)"))).fetchall()
            assert [r[1] for r in rows] == ["id", "title"]

            # 首次迁移: 补 year 列
            await _migrate_legacy_paper_imports(conn)
            rows = (await conn.execute(text("PRAGMA table_info(paper_imports)"))).fetchall()
            names = [r[1] for r in rows]
            assert "year" in names
            types = {r[1]: r[2] for r in rows}
            assert types["year"] == "INTEGER"

            # 新列可插入 NULL 与整数
            await conn.execute(text("INSERT INTO paper_imports (title, year) VALUES ('t', NULL)"))
            await conn.execute(text("INSERT INTO paper_imports (title, year) VALUES ('t2', 2023)"))

            # 再次迁移: 幂等(SQLite 重复 ADD COLUMN 会抛错, 若迁移重复 ALTER 此处将失败)
            await _migrate_legacy_paper_imports(conn)
            rows = (await conn.execute(text("PRAGMA table_info(paper_imports)"))).fetchall()
            assert [r[1] for r in rows].count("year") == 1
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_paper_imports_migration_noop_when_table_missing(self, tmp_path):
        """新库尚无 paper_imports 表(create_all 将建带 year 列的新表)时迁移直接返回, 不报错。"""
        from app.database.database import _migrate_legacy_paper_imports

        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}")
        async with engine.begin() as conn:
            await conn.execute(
                text("CREATE TABLE papers (id INTEGER PRIMARY KEY, title VARCHAR(512) NOT NULL DEFAULT '')")
            )
            await _migrate_legacy_paper_imports(conn)  # 表不存在 → 直接返回, 无异常
        await engine.dispose()


class TestDbFile:
    """数据库文件创建测试"""

    @pytest.mark.asyncio
    async def test_db_file_is_created(self):
        """验证 SQLite 数据库文件在首次连接后自动创建"""
        # 触发一次连接，确保文件被创建
        await check_database_connection()

        url = str(engine.url)
        # URL 格式: sqlite+aiosqlite:///data/graphrag.db
        db_file = url.replace("sqlite+aiosqlite:///", "")
        assert Path(db_file).exists(), f"Database file not found: {db_file}"
