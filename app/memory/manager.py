"""
Memory Manager — 统一记忆管理入口

协调 ShortTermMemory + LongTermMemory，对外提供简洁接口。

职责：
- add_message: 同时写入短期和长期记忆
- get_context: 返回组装好的 WorkingMemory
- load_history: 从长期记忆恢复历史消息到短期记忆

不包含业务逻辑、LLM 调用、Prompt 构造。
"""

from app.memory.long_term import LongTermMemory
from app.memory.models import Message, WorkingMemory
from app.memory.short_term import ShortTermMemory


class MemoryManager:
    """
    记忆管理器 — 唯一对外接口。

    使用方式：
        manager = MemoryManager(short_term, long_term)
        await manager.add_message("sess_1", "user", "什么是RAG？")
        context = manager.get_context("sess_1")
    """

    def __init__(
        self,
        short_term: ShortTermMemory,
        long_term: LongTermMemory,
    ) -> None:
        self.short_term = short_term
        self.long_term = long_term
        self._working_memory: WorkingMemory | None = None

    def init_working_memory(
        self,
        session_id: str,
        query: str = "",
    ) -> WorkingMemory:
        """
        初始化或重置当前请求的工作记忆。
        """
        wm = WorkingMemory(
            session_id=session_id,
            current_query=query,
            retrieved_documents=[],
            messages=self.short_term.get_messages(),
        )
        self._working_memory = wm
        return wm

    @property
    def working_memory(self) -> WorkingMemory | None:
        """当前工作记忆（可能为 None）"""
        return self._working_memory

    async def add_message(
        self, session_id: str, role: str, content: str, citations: list[str] | None = None
    ) -> None:
        """
        添加一条消息到短期记忆 + 持久化到长期记忆。

        这是每次对话轮次调用的核心方法。
        """
        message = Message(role=role, content=content, citations=citations or [])

        # 短期记忆（滑动窗口）
        self.short_term.add_message(message)

        # 长期记忆（持久化）
        await self.long_term.save_message(session_id, message)

        # 同步到工作记忆
        if self._working_memory:
            self._working_memory.messages = self.short_term.get_messages()

    async def add_user_message(self, session_id: str, content: str) -> None:
        """便捷方法：添加用户消息"""
        await self.add_message(session_id, "user", content)
        if self._working_memory:
            self._working_memory.current_query = content

    async def add_assistant_message(self, session_id: str, content: str, citations: list[str] | None = None) -> None:
        """便捷方法：添加助手消息"""
        msg = Message(role="assistant", content=content, citations=citations or [])
        self.short_term.add_message(msg)
        await self.long_term.save_message(session_id, msg)
        if self._working_memory:
            self._working_memory.messages = self.short_term.get_messages()

    def get_context(self) -> WorkingMemory | None:
        """
        返回当前工作记忆上下文。

        调用方（如 Phase 7 LangGraph Node）从此方法获取 LLM 输入。
        """
        return self._working_memory

    async def load_history(
        self, session_id: str, limit: int = 10
    ) -> None:
        """
        从长期记忆加载历史消息到短期记忆窗口。

        用于恢复已有会话的上下文。
        """
        messages = await self.long_term.load_messages(session_id, limit=limit)
        for msg in messages:
            self.short_term.add_message(msg)

    async def compress(self, llm=None) -> str | None:
        """
        压缩短期记忆中的旧消息为摘要。

        窗口接近上限时调用，用 LLM 将旧消息压缩为系统摘要。
        无 LLM 时静默丢弃旧消息。
        """
        return await self.short_term.compress(llm)

    def clear_short_term(self) -> None:
        """清空短期记忆（不影响长期记忆）"""
        self.short_term.clear()
        if self._working_memory:
            self._working_memory.messages = []
