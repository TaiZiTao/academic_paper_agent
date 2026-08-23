"""PaperImport 模型持久化测试。"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.base import Base
from app.models.research import PaperImport


def _make_engine(tmp_path, name: str):
    return create_async_engine(f"sqlite+aiosqlite:///{tmp_path / name}")


@pytest.mark.asyncio
async def test_paper_import_roundtrip(tmp_path):
    """写入后可跨会话读回, 字段值与写入一致。"""
    engine = _make_engine(tmp_path, "research.db")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    item = PaperImport(
        title="Test Paper",
        source="arxiv",
        external_id="2401.12345",
        doi="10.1000/xyz",
        pdf_url="https://arxiv.org/pdf/2401.12345",
        page_url="https://arxiv.org/abs/2401.12345",
        status="pending",
        progress=0,
    )
    async with session_factory() as session:
        session.add(item)
        await session.commit()
        assert item.id > 0

    # 关闭第一个 session 后, 用新 session 跨会话读回已提交数据
    async with session_factory() as session:
        fetched = (await session.execute(select(PaperImport).where(PaperImport.id == item.id))).scalar_one()
        assert fetched.title == "Test Paper"
        assert fetched.status == "pending"
        assert fetched.paper_id is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_paper_import_orm_defaults(tmp_path):
    """不传可默认字段时, ORM 默认值生效并持久化。"""
    engine = _make_engine(tmp_path, "defaults.db")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 省略 status/progress/error_message/created_at/updated_at, 触发 ORM 默认值
    item = PaperImport(title="Defaults Paper", source="arxiv", external_id="2401.99999")
    async with session_factory() as session:
        session.add(item)
        await session.commit()
        saved_id = item.id

    # 跨会话读回, 验证默认值已落库
    async with session_factory() as session:
        fetched = (await session.execute(select(PaperImport).where(PaperImport.id == saved_id))).scalar_one()
        assert fetched.status == "pending"
        assert fetched.progress == 0
        assert fetched.error_message == ""
        assert fetched.created_at is not None
        assert fetched.updated_at is not None
    await engine.dispose()


@pytest.mark.asyncio
async def test_paper_import_year_persists(tmp_path):
    """year 列可持久化: 写入 2023 跨会话读回一致; 缺省为 NULL。"""
    engine = _make_engine(tmp_path, "year.db")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    item = PaperImport(title="Year Paper", source="arxiv", year=2023)
    async with session_factory() as session:
        session.add(item)
        await session.commit()
        saved_id = item.id

    async with session_factory() as session:
        fetched = (await session.execute(select(PaperImport).where(PaperImport.id == saved_id))).scalar_one()
        assert fetched.year == 2023

    # 缺省 year 为 NULL(不传时插入 NULL)
    item2 = PaperImport(title="No Year", source="arxiv")
    async with session_factory() as session:
        session.add(item2)
        await session.commit()
        saved_id2 = item2.id
    async with session_factory() as session:
        fetched2 = (await session.execute(select(PaperImport).where(PaperImport.id == saved_id2))).scalar_one()
        assert fetched2.year is None
    await engine.dispose()


def test_paper_import_has_year_column():
    """paper_imports 必须有 year 列(可空, CVF L1.5 按年定位会议页的落库字段)。"""
    assert "year" in PaperImport.__table__.c
    assert PaperImport.__table__.c.year.nullable is True


def test_paper_import_paper_id_has_foreign_key_and_index():
    """paper_id 必须通过外键引用 papers.id(ondelete=SET NULL)并建索引, 与全项目一致。"""
    col = PaperImport.__table__.c.paper_id
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    fk = fks[0]
    assert fk.column.table.name == "papers"
    assert fk.column.name == "id"
    assert fk.ondelete == "SET NULL"
    index_names = {ix.name for ix in PaperImport.__table__.indexes}
    assert "ix_paper_imports_paper_id" in index_names
