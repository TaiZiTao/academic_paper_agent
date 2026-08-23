"""
长期记忆 — 基于 SQLite 的对话消息持久化

使用 Phase 3 的 async_session 进行异步数据库操作。
支持按 session_id 存取消息。

不实现复杂用户画像、embedding memory、向量记忆。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.memory.models import Message
from app.models.conversation import ConversationMessage


class LongTermMemory:
    """
    长期记忆管理器 — 对话消息持久化。

    每次 save_message 立即 commit，保证消息不丢失。
    load_messages 按时间升序返回最近 N 条。
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self.session_factory = session_factory

    async def save_message(
        self, session_id: str, message: Message
    ) -> None:
        """
        持久化一条消息到数据库。
        """
        import json
        citations_json = json.dumps(message.citations) if message.citations else None
        db_msg = ConversationMessage(
            session_id=session_id,
            role=message.role,
            content=message.content,
            citations=citations_json,
        )
        async with self.session_factory() as session:
            session.add(db_msg)
            await session.commit()

    async def save_messages(
        self, session_id: str, messages: list[Message]
    ) -> None:
        """
        批量持久化多条消息。
        """
        db_msgs = [
            ConversationMessage(
                session_id=session_id,
                role=m.role,
                content=m.content,
            )
            for m in messages
        ]
        async with self.session_factory() as session:
            session.add_all(db_msgs)
            await session.commit()

    async def load_messages(
        self, session_id: str, limit: int = 50
    ) -> list[Message]:
        """
        从数据库加载指定 session 的最近 N 条消息。

        Returns
        -------
        list[Message]
            按 created_at 升序排列
        """
        async with self.session_factory() as session:
            stmt = (
                select(ConversationMessage)
                .where(ConversationMessage.session_id == session_id)
                .order_by(ConversationMessage.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()

        # 反转：数据库取的是 desc，返回给调用方需要 asc（时间从旧到新）
        rows = list(reversed(rows))

        import json
        result: list[Message] = []
        for row in rows:
            citations = []
            if row.citations:
                try:
                    citations = json.loads(row.citations)
                except (json.JSONDecodeError, TypeError):
                    pass
            result.append(Message(
                role=row.role,
                content=row.content,
                citations=citations,
                timestamp=row.created_at.isoformat() if row.created_at else "",
            ))
        return result

    async def clear_session(self, session_id: str) -> None:
        """
        删除指定 session 的所有消息。
        """
        from sqlalchemy import delete

        async with self.session_factory() as session:
            stmt = delete(ConversationMessage).where(
                ConversationMessage.session_id == session_id
            )
            await session.execute(stmt)
            await session.commit()
