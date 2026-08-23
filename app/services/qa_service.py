"""
问答业务服务

编排一次完整的问答生命周期：加载历史 → 构建 State → 调用 Graph → 保存结果。
"""

import json
from typing import Any, AsyncGenerator

from pydantic import BaseModel, Field
from langgraph.graph import StateGraph

from app.memory.manager import MemoryManager
from app.memory.models import Message as MemoryMessage
from app.graph.state import GraphState
from app.utils.exceptions import AppException

try:
    from loguru import logger
except ImportError:
    logger = None  # type: ignore


# ============================================================
# QAResponse — 问答结果数据对象
# ============================================================

class QAResponse(BaseModel):
    """一次问答的返回结果"""

    answer: str
    citations: list[str] = Field(default_factory=list)
    session_id: str
    intent: str = ""


# ============================================================
# QAService — 问答业务入口
# ============================================================

class QAService:
    """
    问答业务服务。

    依赖注入：MemoryManager + CompiledStateGraph。
    通过 config 向 Graph Node 传递 LLM / Retriever。

    使用方式：
        service = QAService(memory_manager, compiled_graph)
        response = await service.ask(
            "sess_1", "什么是论文知识问答？",
            config={"configurable": {"llm": ..., "retriever": ...}}
        )
    """

    def __init__(
        self,
        memory: MemoryManager,
        graph: StateGraph,
    ) -> None:
        self.memory = memory
        self.graph = graph

    async def ask(
        self,
        session_id: str,
        query: str,
        config: dict[str, Any] | None = None,
        kb_id: int = 0,
    ) -> QAResponse:
        # 注入 thread_id 供 checkpointer 使用
        config = _ensure_thread_id(config, session_id)
        """
        执行一次完整问答。

        Phase 10：config 可选，包含 {"configurable": {"llm": ..., "retriever": ...}}。
        无 config 时 Graph Node 降级为骨架行为。

        流程：
        1. 加载会话历史
        2. 初始化工作记忆
        3. 保存用户消息
        4. 构建 GraphState → 异步调用 Graph（ainvoke）
        5. 保存助手回复
        6. 返回 QAResponse
        """
        self._log(f"Session [{session_id}] 收到问题: {query[:50]}...")

        try:
            # 1. 加载历史消息到短期记忆
            await self.memory.load_history(session_id)

            # 2. 初始化工作记忆（关联当前 query）
            self.memory.init_working_memory(session_id, query)

            # 3. 持久化用户消息（短期 + 长期）
            await self.memory.add_user_message(session_id, query)

            # 4. 构建 GraphState 并异步调用 Graph
            state = self._build_state(session_id, query, kb_id)
            result = await self.graph.ainvoke(state, config=config)

            # 5. 获取回答并持久化
            answer = result.get("answer", "")
            citations = result.get("citations", [])
            await self.memory.add_assistant_message(session_id, answer, citations=citations)

            self._log(f"Session [{session_id}] 回答完成, 长度={len(answer)}")

            return QAResponse(
                answer=answer,
                citations=citations,
                session_id=session_id,
                intent=result.get("intent", ""),
            )

        except Exception as e:
            self._log(f"Session [{session_id}] 处理失败: {e}", level="ERROR")
            raise AppException(
                message="问答处理失败，请稍后重试",
                detail=str(e),
            ) from e

    async def ask_stream(
        self,
        session_id: str,
        query: str,
        config: dict[str, Any] | None = None,
        kb_id: int = 0,
    ) -> AsyncGenerator[str, None]:
        config = _ensure_thread_id(config, session_id)
        """
        SSE 流式问答 — 返回 text/event-stream 事件。

        每个 Node 执行完成后发送 node 事件，
        最终发送 answer 和 done 事件。
        """
        self._log(f"Stream [{session_id}]: {query[:50]}...")

        try:
            await self.memory.load_history(session_id)
            self.memory.init_working_memory(session_id, query)
            await self.memory.add_user_message(session_id, query)

            state = self._build_state(session_id, query, kb_id)

            # Node 状态追踪（重试时节点可被多次访问）
            nodes_order = [
                "query_rewrite", "intent_router", "knowledge_selection",
                "retrieval", "relevance_evaluation", "generation",
                "citation_formatting", "error_handler",
            ]

            # 首次发送所有节点 pending
            for name in nodes_order:
                yield _sse("node", {"node": name, "status": "pending"})

            answer = ""
            citations = []
            intent = ""
            async for mode, event in self.graph.astream(
                state, config=config, stream_mode=["updates", "messages"],
            ):
                if mode == "messages":
                    token, meta = event
                    node_name = meta.get("langgraph_node", "") if isinstance(meta, dict) else ""
                    if node_name == "generation":
                        content = token.content if hasattr(token, "content") else str(token)
                        if content:
                            answer += content
                            yield _sse("token", {"content": content})
                elif mode == "updates":
                    for node_name, node_output in event.items():
                        yield _sse("node", {"node": node_name, "status": "running"})
                        extra = _safe_dict(node_output) if isinstance(node_output, dict) else {}
                        if node_name in ("retrieval", "relevance_evaluation"):
                            raw_docs = node_output.get("retrieved_documents", []) if isinstance(node_output, dict) else []
                            extra["docs_count"] = len(raw_docs)
                        if node_name == "generation":
                            generated_answer = extra.get("answer", "")
                            if generated_answer:
                                answer = generated_answer
                            citations = extra.get("citations", [])
                        if node_name == "intent_router":
                            intent = extra.get("intent", "")
                        yield _sse("node", {"node": node_name, "status": "completed", **extra})

            await self.memory.add_assistant_message(session_id, answer, citations=citations)

            yield _sse("done", {
                "answer": answer,
                "citations": citations,
                "intent": intent,
            })

        except Exception as e:
            self._log(f"Stream [{session_id}] 失败: {e}", level="ERROR")
            yield _sse("error", {"detail": str(e)})

    def _build_state(self, session_id: str, query: str, kb_id: int = 0) -> GraphState:
        """基于当前 Memory 构建 GraphState"""
        messages = self.memory.short_term.get_messages()
        return GraphState(
            session_id=session_id,
            query=query,
            kb_id=kb_id,
            messages=[{"role": m.role, "content": m.content} for m in messages],
        )

    @staticmethod
    def _log(message: str, level: str = "INFO") -> None:
        """内部日志"""
        if logger:
            getattr(logger, level.lower())(message)


# ============================================================
# SSE Helpers
# ============================================================

def _sse(event: str, data: dict) -> str:
    """格式化 SSE 事件"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _ensure_thread_id(config: dict | None, session_id: str) -> dict:
    """确保 config 中包含 thread_id（checkpointer 需要）"""
    if config is None:
        config = {}
    if "configurable" not in config:
        config["configurable"] = {}
    if "thread_id" not in config["configurable"]:
        config["configurable"]["thread_id"] = session_id
    return config


def _safe_dict(obj: dict) -> dict:
    """将 dict 中的不可序列化对象转为字符串"""
    result = {}
    for k, v in obj.items():
        try:
            json.dumps({k: v})
            result[k] = v
        except (TypeError, ValueError):
            if isinstance(v, list):
                result[k] = [_safe_item(x) for x in v]
            else:
                result[k] = str(v)
    return result


def _safe_item(item: Any) -> Any:
    if isinstance(item, dict):
        return _safe_dict(item)
    try:
        json.dumps(item)
        return item
    except (TypeError, ValueError):
        return str(item)
