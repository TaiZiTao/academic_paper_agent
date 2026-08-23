"""
知识库业务服务

提供知识库 CRUD + 分页列表 + 搜索。
"""

from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.knowledge_base import KnowledgeBase


class KBService:
    """知识库管理服务"""

    def __init__(self, session_factory: async_sessionmaker, embedding=None) -> None:
        self.session_factory = session_factory
        self.embedding = embedding

    async def _embed_desc(self, name: str, description: str) -> str | None:
        """将 KB 描述向量化，返回 JSON 字符串"""
        if not self.embedding:
            logger.debug("无 embedding 模型，跳过")
            return None
        try:
            text = f"{name}: {description}" if description else name
            vec = await self.embedding.embed_text(text)
            import json
            result = json.dumps(vec)
            logger.debug(f"'{name}' → {len(vec)}维向量")
            return result
        except Exception as e:
            logger.warning(f"'{name}' embedding 失败: {e}")
            return None

    async def create_kb(self, name: str, description: str = "") -> KnowledgeBase:
        """创建知识库"""
        embedded = await self._embed_desc(name, description)
        kb = KnowledgeBase(name=name, description=description, embedding_vector=embedded)
        async with self.session_factory() as session:
            session.add(kb)
            await session.commit()
            await session.refresh(kb)
        return kb

    async def update_kb(
        self, kb_id: int, name: str | None = None, description: str | None = None
    ) -> KnowledgeBase | None:
        """编辑知识库（部分更新）"""
        async with self.session_factory() as session:
            kb = await session.get(KnowledgeBase, kb_id)
            if kb is None:
                return None
            if name is not None:
                kb.name = name
            if description is not None:
                kb.description = description
            kb.updated_at = datetime.now(timezone.utc)
            # 重新 embed
            embedded = await self._embed_desc(kb.name, kb.description or "")
            if embedded:
                kb.embedding_vector = embedded
            await session.commit()
            await session.refresh(kb)
        return kb

    async def list_kb(
        self,
        search: str = "",
        page: int = 1,
        page_size: int = 10,
    ) -> dict:
        """
        获取知识库列表（支持搜索 + 分页）。

        Returns
        -------
        dict: {"items": list[KnowledgeBase], "total": int}
        """
        async with self.session_factory() as session:
            stmt = select(KnowledgeBase)
            if search:
                stmt = stmt.where(KnowledgeBase.name.contains(search))
            stmt = stmt.order_by(KnowledgeBase.updated_at.desc())

            # 总数
            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = (await session.execute(count_stmt)).scalar() or 0

            # 分页
            offset = (page - 1) * page_size
            stmt = stmt.offset(offset).limit(page_size)
            result = await session.execute(stmt)
            items = result.scalars().all()

            return {"items": list(items), "total": total}

    async def delete_kb(self, kb_id: int) -> bool:
        """删除知识库，返回是否成功"""
        async with self.session_factory() as session:
            kb = await session.get(KnowledgeBase, kb_id)
            if kb is None:
                return False
            await session.delete(kb)
            await session.commit()
        return True

    async def get_kb(self, kb_id: int) -> KnowledgeBase | None:
        """获取单个知识库详情"""
        async with self.session_factory() as session:
            return await session.get(KnowledgeBase, kb_id)
