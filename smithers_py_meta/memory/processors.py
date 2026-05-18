"""Memory maintenance processors.

Three processors for managing memory lifecycle:
- ``TtlGarbageCollector`` — sweeps expired facts
- ``TokenLimiter`` — trims thread history below a token budget
- ``Summarizer`` — compresses old messages into a single system message
"""

from __future__ import annotations

from typing import Awaitable, Callable, Optional

from .types import MemoryMessage


class TtlGarbageCollector:
    """Sweeps expired facts from working memory.

    Call ``process(store)`` to remove all facts whose ``expires_at_ms`` is
    in the past.
    """

    async def process(self, store) -> int:
        """Remove expired facts. Returns count removed."""
        return await store.expire_sweep()


class TokenLimiter:
    """Trims a thread's message history below a token budget.

    Uses a ~4-char-per-token heuristic. Removes oldest messages first
    until the total estimated token count is below ``max_tokens``.
    """

    def __init__(self, max_tokens: int):
        self.max_tokens = max_tokens

    async def process(self, store, thread_id: str) -> int:
        """Trim thread history. Returns count of messages removed."""
        messages = await store.list_messages(thread_id)
        if not messages:
            return 0

        # Estimate tokens (~4 chars per token)
        def estimate_tokens(msg: MemoryMessage) -> int:
            return len(msg.content) // 4

        total = sum(estimate_tokens(m) for m in messages)
        if total <= self.max_tokens:
            return 0

        # Remove oldest until under budget
        removed = 0
        while total > self.max_tokens and messages:
            oldest = messages.pop(0)
            total -= estimate_tokens(oldest)
            removed += 1

        # Rebuild thread (delete all, re-insert remaining)
        store.conn.execute("DELETE FROM ts_memory_messages WHERE thread_id = ?", (thread_id,))
        for seq, msg in enumerate(messages):
            store.conn.execute(
                """
                INSERT INTO ts_memory_messages (thread_id, seq, role, content, created_at_ms)
                VALUES (?, ?, ?, ?, ?)
                """,
                (thread_id, seq, msg.role, msg.content, msg.created_at_ms),
            )
        store.conn.commit()
        return removed


class Summarizer:
    """Compresses old messages into a single system-role summary.

    Replaces the oldest N messages (where N ≥ ``min_to_compress``) with a
    single ``system``-role message produced by ``summarize_fn``. Keeps the
    most recent ``keep_recent`` messages untouched.
    """

    def __init__(
        self,
        summarize_fn: Callable[[list[MemoryMessage]], Awaitable[str]],
        keep_recent: int = 10,
        min_to_compress: int = 5,
    ):
        self.summarize_fn = summarize_fn
        self.keep_recent = keep_recent
        self.min_to_compress = min_to_compress

    async def process(self, store, thread_id: str) -> Optional[str]:
        """Compress old messages. Returns the summary text or ``None`` if
        nothing was compressed."""
        messages = await store.list_messages(thread_id)
        if len(messages) <= self.keep_recent:
            return None

        # Split into old and recent
        old = messages[: -self.keep_recent]
        recent = messages[-self.keep_recent :]

        if len(old) < self.min_to_compress:
            return None

        # Summarize old
        import time
        summary_text = await self.summarize_fn(old)
        summary_msg = MemoryMessage(
            role="system",
            content=summary_text,
            created_at_ms=int(time.time() * 1000),
        )

        # Rebuild thread: summary + recent
        store.conn.execute("DELETE FROM ts_memory_messages WHERE thread_id = ?", (thread_id,))
        all_msgs = [summary_msg] + recent
        for seq, msg in enumerate(all_msgs):
            store.conn.execute(
                """
                INSERT INTO ts_memory_messages (thread_id, seq, role, content, created_at_ms)
                VALUES (?, ?, ?, ?, ?)
                """,
                (thread_id, seq, msg.role, msg.content, msg.created_at_ms),
            )
        store.conn.commit()
        return summary_text
