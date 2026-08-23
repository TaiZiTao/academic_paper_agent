"""
Service 层测试

使用 Fake Memory + Fake Graph，不调用真实：
- OpenAI / LLM
- FAISS / BM25
- SQLite
"""

import asyncio
import json

import pytest

from app.memory.models import Message as MemoryMessage
from app.memory.short_term import ShortTermMemory
from app.services.qa_service import QAResponse, QAService
from app.utils.exceptions import AppException


# ============================================================
# Fake Implementations
# ============================================================

class FakeMemoryManager:
    """
    模拟 MemoryManager，纯内存，不访问数据库。

    消息按 session_id 隔离存储。
    """

    def __init__(self, max_messages: int = 10):
        self.short_term = ShortTermMemory(max_messages=max_messages)
        self._storage: dict[str, list[MemoryMessage]] = {}  # session_id → messages
        self._working_memory = None

    async def load_history(self, session_id: str, limit: int = 10):
        """从内存存储加载历史"""
        messages = self._storage.get(session_id, [])[-limit:]
        for msg in messages:
            self.short_term.add_message(msg)

    def init_working_memory(self, session_id: str, query: str = ""):
        from app.memory.models import WorkingMemory
        wm = WorkingMemory(
            session_id=session_id,
            current_query=query,
            messages=self.short_term.get_messages(),
        )
        self._working_memory = wm
        return wm

    async def add_user_message(self, session_id: str, content: str):
        msg = MemoryMessage(role="user", content=content)
        self.short_term.add_message(msg)
        self._persist(session_id, msg)
        if self._working_memory:
            self._working_memory.current_query = content

    async def add_assistant_message(self, session_id: str, content: str, citations=None):
        msg = MemoryMessage(role="assistant", content=content, citations=citations or [])
        self.short_term.add_message(msg)
        self._persist(session_id, msg)

    def _persist(self, session_id: str, msg: MemoryMessage):
        if session_id not in self._storage:
            self._storage[session_id] = []
        self._storage[session_id].append(msg)

    @property
    def stored_messages(self):
        return dict(self._storage)


class FakeGraph:
    """
    模拟编译后的 LangGraph。

    支持 ainvoke (Phase 10 async)，用于测试不同场景。
    """

    def __init__(self, response: dict | None = None):
        self._response = response or {}
        self.ainvoke_calls: list[dict] = []

    async def ainvoke(self, state: dict, config=None) -> dict:
        self.ainvoke_calls.append(dict(state))
        base = {
            "answer": f"[Fake] Answer for: {state.get('query', '')}",
            "citations": [],
            "intent": "knowledge_qa",
        }
        base.update(self._response)
        return base


class FailingGraph:
    """模拟失败的 Graph（用于异常处理测试）。"""

    async def ainvoke(self, state: dict, config=None) -> dict:
        raise RuntimeError("Simulated graph failure")


class UpdateOnlyStreamingGraph:
    """模拟只通过 updates 返回生成结果、没有 messages token 的 Graph。"""

    async def astream(self, state: dict, config=None, stream_mode=None):
        yield "updates", {
            "generation": {
                "answer": "生成节点的完整回答",
                "citations": ["source.pdf"],
            }
        }


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def fake_memory():
    return FakeMemoryManager(max_messages=10)


@pytest.fixture
def fake_graph():
    return FakeGraph()


@pytest.fixture
def qa_service(fake_memory, fake_graph):
    return QAService(memory=fake_memory, graph=fake_graph)


# ============================================================
# QAResponse Tests
# ============================================================

class TestQAResponse:
    """QAResponse 数据模型测试"""

    def test_create_minimal_response(self):
        resp = QAResponse(answer="hello", session_id="s1")
        assert resp.answer == "hello"
        assert resp.session_id == "s1"
        assert resp.citations == []
        assert resp.intent == ""

    def test_create_full_response(self):
        resp = QAResponse(
            answer="GraphRAG 是...",
            citations=["doc1.md", "doc2.md"],
            session_id="s1",
            intent="knowledge_qa",
        )
        assert len(resp.citations) == 2
        assert resp.intent == "knowledge_qa"


# ============================================================
# QAService Tests
# ============================================================

class TestQAService:
    """QAService 核心流程测试"""

    @pytest.mark.asyncio
    async def test_ask_returns_qaresponse(self, qa_service):
        """ask 返回 QAResponse 对象"""
        resp = await qa_service.ask("sess_1", "什么是GraphRAG？")
        assert isinstance(resp, QAResponse)
        assert len(resp.answer) > 0
        assert resp.session_id == "sess_1"

    @pytest.mark.asyncio
    async def test_ask_saves_user_message(self, qa_service, fake_memory):
        """用户消息被持久化"""
        await qa_service.ask("sess_2", "测试问题")
        stored = fake_memory.stored_messages.get("sess_2", [])
        user_msgs = [m for m in stored if m.role == "user"]
        assert len(user_msgs) >= 1
        assert user_msgs[0].content == "测试问题"

    @pytest.mark.asyncio
    async def test_ask_saves_assistant_message(self, qa_service, fake_memory):
        """助手回复被持久化"""
        await qa_service.ask("sess_3", "问题")
        stored = fake_memory.stored_messages.get("sess_3", [])
        asst_msgs = [m for m in stored if m.role == "assistant"]
        assert len(asst_msgs) >= 1

    @pytest.mark.asyncio
    async def test_ask_loads_history(self, qa_service, fake_memory):
        """历史消息在调用时被加载到短期记忆"""
        # 先存一条历史
        fake_memory._persist("sess_4", MemoryMessage(role="user", content="历史问题"))
        fake_memory._persist("sess_4", MemoryMessage(role="assistant", content="历史回答"))

        # 新问题
        await qa_service.ask("sess_4", "新问题")

        # 短期记忆应包含历史 + 本轮消息（共 4 条）
        messages = fake_memory.short_term.get_messages()
        assert len(messages) >= 4

    @pytest.mark.asyncio
    async def test_ask_invokes_graph(self, qa_service, fake_graph):
        """Graph.invoke 被调用且 State 正确"""
        await qa_service.ask("sess_5", "GraphRAG？")

        assert len(fake_graph.ainvoke_calls) >= 1
        state = fake_graph.ainvoke_calls[0]
        assert state["session_id"] == "sess_5"
        assert state["query"] == "GraphRAG？"

    @pytest.mark.asyncio
    async def test_graph_state_contains_messages(self, qa_service, fake_graph):
        """GraphState 中 messages 字段包含历史消息"""
        await qa_service.ask("sess_6", "query")
        state = fake_graph.ainvoke_calls[0]
        assert "messages" in state
        assert isinstance(state["messages"], list)

    @pytest.mark.asyncio
    async def test_ask_exception_wraps_in_app_exception(self, fake_memory):
        """Graph 失败时，异常被包装为 AppException"""
        failing_graph = FailingGraph()
        service = QAService(memory=fake_memory, graph=failing_graph)

        with pytest.raises(AppException) as exc_info:
            await service.ask("sess_err", "crash me")
        assert "问答处理失败" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_session_isolation(self, qa_service, fake_memory):
        """不同 session 的消息互不干扰"""
        await qa_service.ask("sess_A", "A的查询")
        await qa_service.ask("sess_B", "B的查询")

        stored_a = fake_memory.stored_messages.get("sess_A", [])
        stored_b = fake_memory.stored_messages.get("sess_B", [])

        a_contents = [m.content for m in stored_a]
        b_contents = [m.content for m in stored_b]
        assert "A的查询" in a_contents
        assert "A的查询" not in b_contents
        assert "B的查询" in b_contents

    @pytest.mark.asyncio
    async def test_multiple_turns_in_session(self, qa_service, fake_memory):
        """同一 session 的多轮对话"""
        await qa_service.ask("sess_multi", "第一轮")
        await qa_service.ask("sess_multi", "第二轮")

        stored = fake_memory.stored_messages.get("sess_multi", [])
        user_msgs = [m for m in stored if m.role == "user"]
        assert len(user_msgs) == 2

    @pytest.mark.asyncio
    async def test_response_includes_intent(self, qa_service):
        """QAResponse 包含 intent 字段"""
        resp = await qa_service.ask("sess_intent", "问题")
        assert hasattr(resp, "intent")

    def test_stream_uses_generation_update_when_no_token_events(self, fake_memory):
        """模型没有产生 messages token 时，仍使用 generation update 的完整回答。"""
        service = QAService(memory=fake_memory, graph=UpdateOnlyStreamingGraph())

        async def collect_events():
            return [
                event async for event in service.ask_stream(
                    "sess_update_only", "测试问题"
                )
            ]

        events = asyncio.run(collect_events())
        done_event = next(event for event in events if event.startswith("event: done"))
        payload = json.loads(done_event.split("data: ", 1)[1])

        assert payload["answer"] == "生成节点的完整回答"
        stored = fake_memory.stored_messages["sess_update_only"]
        assert stored[-1].content == "生成节点的完整回答"
