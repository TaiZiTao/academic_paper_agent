"""
Memory 数据对象（Pydantic Schema）

定义 Memory 模块使用的内存数据结构。
这些对象是瞬态的，不涉及数据库持久化。

与 app/models/ 的区别：
- app/models/          → SQLAlchemy ORM Model（数据库表映射）
- app/memory/models.py → Pydantic Schema（Memory 模块内部数据对象）
"""

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def _now() -> str:
    """返回 UTC 时间 ISO 字符串"""
    return datetime.now(timezone.utc).isoformat()


class Message(BaseModel):
    """
    单条对话消息。

    role 取值：user / assistant / system
    """

    role: str
    content: str
    citations: list[str] = Field(default_factory=list)
    timestamp: str = Field(default_factory=_now)


class WorkingMemory(BaseModel):
    """
    当前请求的工作记忆上下文。

    每次用户请求时创建/更新，承载 LLM 调用所需的全部上下文信息。
    Phase 7 时 LangGraph State 可引用此对象。
    """

    session_id: str
    current_query: str = ""
    retrieved_documents: list[Any] = Field(default_factory=list)
    messages: list[Message] = Field(default_factory=list)
