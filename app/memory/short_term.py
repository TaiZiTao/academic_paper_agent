"""
短期记忆 — 滑动窗口 + 摘要压缩

基于 collections.deque 的固定大小消息缓冲区。
超出窗口的消息自动丢弃，手工调用 compress() 可在丢弃前生成摘要。
"""

from collections import deque

from app.memory.models import Message


class ShortTermMemory:
    """
    滑动窗口短期记忆，支持 LLM 摘要压缩。

    deque(maxlen=N) 自动淘汰旧消息。
    调用 compress(llm) 可在淘汰前压缩旧消息为系统摘要。
    """

    def __init__(self, max_messages: int = 10) -> None:
        self.max_messages = max_messages
        self._messages: deque[Message] = deque(maxlen=max_messages)
        self._summary: str = ""

    def add_message(self, message: Message) -> None:
        """添加消息。超出窗口时自动丢弃最早消息。"""
        self._messages.append(message)

    async def compress(self, llm=None) -> str | None:
        """
        将当前窗口中最旧的消息压缩为摘要，保留最近 50% 消息。

        传入 LLM 时用 LLM 生成摘要；无 LLM 时返回 None。

        Returns
        -------
        str | None
        """
        if llm is None or len(self._messages) < 4:
            return None

        all_msgs = list(self._messages)
        split = max(2, len(all_msgs) // 2)
        old_msgs = all_msgs[:split]
        recent_msgs = all_msgs[split:]

        conversation = "\n".join(
            f"{m.role}: {m.content[:200]}" for m in old_msgs
        )
        prompt = (
            f"将以下对话历史压缩为一段简短摘要（50字以内），保留关键信息：\n\n"
            f"{conversation}\n\n摘要："
        )

        try:
            response = await llm.ainvoke(prompt)
            summary = response.content if hasattr(response, "content") else str(response)
            self._summary = summary.strip()
        except Exception:
            self._summary = "（对话历史摘要）"

        # 替换：摘要 + 最近消息
        summary_msg = Message(role="system", content=f"[历史摘要] {self._summary}")
        self._messages = deque(
            [summary_msg] + recent_msgs,
            maxlen=self.max_messages,
        )
        return self._summary

    def get_messages(self) -> list[Message]:
        """返回窗口内所有消息。"""
        return list(self._messages)

    @property
    def summary(self) -> str:
        """当前摘要文本"""
        return self._summary

    def clear(self) -> None:
        """清空"""
        self._messages.clear()
        self._summary = ""

    @property
    def message_count(self) -> int:
        """当前窗口内消息数量"""
        return len(self._messages)
