"""
数据库引擎与 Session 管理

提供 SQLAlchemy 2.0 async 风格的数据库基础设施：
- AsyncEngine 单例
- async_sessionmaker 工厂
- Session 生命周期管理（供 FastAPI Depends 使用）
- 数据库连接健康检查
- 表创建与引擎释放
"""

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config.settings import settings
from app.database.base import Base

import app.models  # noqa: F401  注册全部 ORM 模型(Base.metadata), 确保 init_db 的 create_all 创建全部表

# ============================================================
# Engine & Session Factory（模块级单例）
# ============================================================
# create_async_engine 在 import 时不会真正连接数据库，
# 只在首次使用时建立连接（惰性初始化），因此模块级创建是安全的。

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
)

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,  # 防止 commit 后属性过期
)


# ============================================================
# Session 生命周期（供 FastAPI Depends 使用）
# ============================================================

async def get_session():
    """
    获取一个数据库 Session，自动管理生命周期。

    用法（Phase 9 在 API 中使用）:
        @router.get("/items")
        async def get_items(session: AsyncSession = Depends(get_session)):
            ...
    """
    async with async_session() as session:
        yield session


# ============================================================
# 数据库健康检查
# ============================================================

async def check_database_connection() -> dict:
    """
    检查数据库连接是否正常。

    执行 SELECT 1 验证连通性，返回状态字典。
    用于 /health 接口扩展（后续 Phase）。
    """
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"database": "connected"}
    except Exception as e:
        return {"database": "disconnected", "error": str(e)}


# ============================================================
# 数据库初始化 & 清理
# ============================================================

async def _migrate_legacy_papers(conn):
    """
    幂等迁移: 为已存在的 papers 表补充缺失列与索引。

    Base.metadata.create_all 只会创建新表, 不会给已存在表加列,
    存量库需要先查 PRAGMA table_info 再 ALTER TABLE ADD COLUMN(幂等)。
    存量库 ALTER 也不会自动补索引, 需显式 CREATE INDEX IF NOT EXISTS。
    """
    cols = await conn.execute(
        text("SELECT name FROM pragma_table_info('papers')")
    )
    names = {row[0] for row in cols}
    if "research_field" not in names:
        await conn.execute(
            text("ALTER TABLE papers ADD COLUMN research_field VARCHAR(128) NOT NULL DEFAULT ''")
        )
    cols = {row[1] for row in await conn.execute(text("PRAGMA table_info(papers)"))}
    if "publication_year" not in cols:
        await conn.execute(
            text("ALTER TABLE papers ADD COLUMN publication_year INTEGER")
        )
    # 索引名与 SQLAlchemy 为 index=True 自动生成的一致(ix_papers_publication_year)
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_papers_publication_year ON papers (publication_year)")
    )


async def _migrate_legacy_paper_imports(conn):
    """
    幂等迁移: 为存量 paper_imports 表补充 year/venue 列(发表年份 + venue, L1.5 free_pdf 按年/按 venue 查找用)。

    paper_imports 由 create_all 创建(新库自带 year 列); 但存量库可能已有
    无 year 列的旧表, 需 PRAGMA table_info 检查后 ALTER TABLE ADD COLUMN(幂等)。
    表不存在时由 create_all 建新表, 此处直接返回(不报错)。
    """
    exists = (
        await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='paper_imports'")
        )
    ).scalar()
    if not exists:
        return
    cols = await conn.execute(text("PRAGMA table_info(paper_imports)"))
    names = {row[1] for row in cols}
    if "year" not in names:
        await conn.execute(
            text("ALTER TABLE paper_imports ADD COLUMN year INTEGER")
        )
    if "venue" not in names:
        await conn.execute(
            text("ALTER TABLE paper_imports ADD COLUMN venue VARCHAR(256) NOT NULL DEFAULT ''")
        )


async def init_db():
    """
    创建所有 ORM Model 对应的数据库表, 并对存量库执行幂等迁移。

    调用 Base.metadata.create_all，只会创建尚未存在的表（幂等操作），
    随后对已存在的 papers 表补充缺失列与索引。
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        try:
            await _migrate_legacy_papers(conn)
        except Exception:
            # 表不存在时 create_all 已处理, 迁移失败不阻断启动
            logger.exception("papers 表迁移失败(表不存在或结构异常), 忽略")
        try:
            await _migrate_legacy_paper_imports(conn)
        except Exception:
            # paper_imports 表不存在时 create_all 已处理, 迁移失败不阻断启动
            logger.exception("paper_imports 表迁移失败(表不存在或结构异常), 忽略")


async def close_db():
    """
    释放数据库引擎连接池。

    应在应用关闭时调用，确保所有连接被正确释放。
    """
    await engine.dispose()
