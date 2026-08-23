"""
Memory 模块测试

覆盖：WorkingMemory / ShortTermMemory / LongTermMemory / MemoryManager
"""

import pytest

from app.database import async_session, init_db
from app.memory import (
    LongTermMemory,
    MemoryManager,
    Message,
    ShortTermMemory,
    WorkingMemory,
)

# 导入 ORM Model 以确保注册到 Base.metadata
import app.models.conversation  # noqa: F401


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def short_term():
    return ShortTermMemory(max_messages=10)


@pytest.fixture
def long_term():
    return LongTermMemory(async_session)


@pytest.fixture
def manager(short_term, long_term):
    return MemoryManager(short_term, long_term)


# ============================================================
# WorkingMemory Tests
# ============================================================

class TestWorkingMemory:
    """WorkingMemory 数据结构测试"""

    def test_create_working_memory(self):
        wm = WorkingMemory(session_id="sess_1")
        assert wm.session_id == "sess_1"
        assert wm.current_query == ""
        assert wm.retrieved_documents == []
        assert wm.messages == []

    def test_working_memory_with_query(self):
        wm = WorkingMemory(session_id="sess_1", current_query="什么是GraphRAG？")
        assert wm.current_query == "什么是GraphRAG？"

    def test_working_memory_defaults(self):
        wm = WorkingMemory(session_id="sess_test")
        assert isinstance(wm.current_query, str)
        assert isinstance(wm.messages, list)


# ============================================================
# ShortTermMemory Tests
# ============================================================

class TestShortTermMemory:
    """短期记忆滑动窗口测试"""

    def test_add_message(self, short_term):
        msg = Message(role="user", content="hello")
        short_term.add_message(msg)
        assert short_term.message_count == 1

    def test_get_messages_returns_copy(self, short_term):
        short_term.add_message(Message(role="user", content="hello"))
        messages = short_term.get_messages()
        messages.append(Message(role="user", content="should not affect"))
        assert short_term.message_count == 1

    def test_window_truncation(self):
        stm = ShortTermMemory(max_messages=3)
        for i in range(5):
            stm.add_message(Message(role="user", content=f"msg_{i}"))

        assert stm.message_count == 3
        messages = stm.get_messages()
        assert messages[0].content == "msg_2"
        assert messages[-1].content == "msg_4"

    def test_clear(self, short_term):
        short_term.add_message(Message(role="user", content="hello"))
        short_term.clear()
        assert short_term.message_count == 0

    def test_get_messages_order(self, short_term):
        short_term.add_message(Message(role="user", content="first"))
        short_term.add_message(Message(role="assistant", content="second"))
        messages = short_term.get_messages()
        assert messages[0].content == "first"
        assert messages[1].content == "second"


# ============================================================
# LongTermMemory Tests
# ============================================================

class TestLongTermMemory:
    """长期记忆持久化测试"""

    @pytest.mark.asyncio
    async def test_save_and_load(self, long_term):
        await init_db()
        session_id = "test_sess_LT_save_load"
        msg = Message(role="user", content="什么是GraphRAG？")

        await long_term.save_message(session_id, msg)
        messages = await long_term.load_messages(session_id)

        assert len(messages) >= 1
        loaded = messages[-1]
        assert loaded.role == "user"
        assert loaded.content == "什么是GraphRAG？"

    @pytest.mark.asyncio
    async def test_save_messages_batch(self, long_term):
        await init_db()
        session_id = "test_sess_LT_batch"
        await long_term.clear_session(session_id)  # 清理旧数据
        msgs = [
            Message(role="user", content="hello"),
            Message(role="assistant", content="hi there"),
            Message(role="user", content="how are you"),
        ]

        await long_term.save_messages(session_id, msgs)
        loaded = await long_term.load_messages(session_id)

        assert len(loaded) == 3

    @pytest.mark.asyncio
    async def test_load_messages_respects_limit(self, long_term):
        await init_db()
        session_id = "test_sess_LT_limit"
        for i in range(20):
            await long_term.save_message(
                session_id, Message(role="user", content=f"msg_{i}")
            )

        loaded = await long_term.load_messages(session_id, limit=5)
        assert len(loaded) <= 5

    @pytest.mark.asyncio
    async def test_load_messages_empty_session(self, long_term):
        await init_db()
        messages = await long_term.load_messages("nonexistent_LT_empty")
        assert messages == []

    @pytest.mark.asyncio
    async def test_clear_session(self, long_term):
        await init_db()
        session_id = "test_sess_LT_clear"
        await long_term.save_message(
            session_id, Message(role="user", content="to be deleted")
        )

        await long_term.clear_session(session_id)
        loaded = await long_term.load_messages(session_id)
        assert loaded == []

    @pytest.mark.asyncio
    async def test_messages_order_ascending(self, long_term):
        await init_db()
        session_id = "test_sess_LT_order"
        await long_term.clear_session(session_id)  # 清理旧数据
        await long_term.save_message(session_id, Message(role="user", content="msg_1"))
        await long_term.save_message(session_id, Message(role="user", content="msg_2"))
        await long_term.save_message(session_id, Message(role="user", content="msg_3"))

        loaded = await long_term.load_messages(session_id)
        contents = [m.content for m in loaded]
        assert contents == ["msg_1", "msg_2", "msg_3"]


# ============================================================
# MemoryManager Tests
# ============================================================

class TestMemoryManager:
    """MemoryManager 集成测试"""

    def test_init_working_memory(self, manager):
        wm = manager.init_working_memory("sess_wm", "测试查询")
        assert wm.session_id == "sess_wm"
        assert wm.current_query == "测试查询"
        assert wm.messages == []

    @pytest.mark.asyncio
    async def test_add_message_updates_both_memories(self, manager):
        await init_db()
        session_id = "sess_MM_both"
        manager.init_working_memory(session_id)

        await manager.add_message(session_id, "user", "你好")

        assert manager.short_term.message_count == 1

        loaded = await manager.long_term.load_messages(session_id)
        assert len(loaded) >= 1

    @pytest.mark.asyncio
    async def test_add_user_message_sets_query(self, manager):
        await init_db()
        manager.init_working_memory("sess_MM_query")
        await manager.add_user_message("sess_MM_query", "什么是Memory？")

        wm = manager.get_context()
        assert wm is not None
        assert wm.current_query == "什么是Memory？"
        assert wm.messages[-1].role == "user"

    @pytest.mark.asyncio
    async def test_add_assistant_message(self, manager):
        await init_db()
        manager.init_working_memory("sess_MM_asst")
        await manager.add_user_message("sess_MM_asst", "hello")
        await manager.add_assistant_message("sess_MM_asst", "你好！")

        messages = manager.short_term.get_messages()
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[1].role == "assistant"

    @pytest.mark.asyncio
    async def test_load_history_into_short_term(self, manager, long_term):
        await init_db()
        session_id = "sess_MM_history"
        await long_term.clear_session(session_id)  # 清理旧数据
        for i in range(5):
            await long_term.save_message(
                session_id, Message(role="user", content=f"msg_{i}")
            )

        await manager.load_history(session_id, limit=10)
        assert manager.short_term.message_count == 5

    def test_clear_short_term(self, manager):
        manager.short_term.add_message(Message(role="user", content="hello"))
        assert manager.short_term.message_count == 1

        manager.clear_short_term()
        assert manager.short_term.message_count == 0

    @pytest.mark.asyncio
    async def test_manager_isolated_per_session(self):
        await init_db()
        stm = ShortTermMemory(max_messages=20)
        ltm = LongTermMemory(async_session)
        mgr = MemoryManager(stm, ltm)

        mgr.init_working_memory("sess_MM_iso_A")
        await mgr.add_message("sess_MM_iso_A", "user", "A的消息")

        mgr.clear_short_term()
        mgr.init_working_memory("sess_MM_iso_B")
        await mgr.add_message("sess_MM_iso_B", "user", "B的消息")

        a_messages = await ltm.load_messages("sess_MM_iso_A")
        a_contents = [m.content for m in a_messages]
        assert "A的消息" in a_contents
        assert "B的消息" not in a_contents
