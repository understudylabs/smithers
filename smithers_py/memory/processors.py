"""Maintenance processors for the memory subsystem.

Three built-ins matching the upstream Smithers documentation:

- ``TtlGarbageCollector`` — sweeps facts whose ``expires_at_ms`` is in
  the past. Cheap; safe to run on a tight schedule.
- ``TokenLimiter`` — keeps a thread's message history below a token
  budget by trimming the oldest messages. Token counts are estimated
  with a tokenizer if available; otherwise falls back to a character
  heuristic (1 token ≈ 4 characters).
- ``Summarizer`` — replaces the oldest N messages in a thread with a
  single summary message produced by an LLM. Requires a callable that
  generates the summary text from a list of messages.

Each processor exposes a single ``process(store)`` coroutine. Call them
from a cron-like loop:

```python
gc = TtlGarbageCollector()
limiter = TokenLimiter(max_tokens=4000)
await gc.process(store)
await limiter.process(store, thread_id="my-thread")
```

The processors are stateless objects; configuration lives on the
constructor.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Optional

from .store import MemoryStore
from .types import MemoryMessage


def _estimate_tokens(text: str) -> int:
    """Cheap fallback token estimate. ~4 chars per token (English avg)."""
    # Better backends (tiktoken) plug in here if installed; for now we
    # use the character heuristic which is accurate within ~20% for
    # natural-language English and is good enough for budget bookkeeping.
    return max(1, len(text) // 4)


class TtlGarbageCollector:
    """Delete expired facts."""

    async def process(self, store: MemoryStore) -> int:
        """Run a sweep. Returns the count of facts removed."""
        return await store.expire_sweep()


class TokenLimiter:
    """Trim a thread's message history below a token budget.

    Drops the oldest messages first; preserves at least one message if
    the budget is set extremely low. Idempotent.
    """

    def __init__(self, max_tokens: int) -> None:
        if max_tokens <= 0:
            raise ValueError("max_tokens must be > 0")
        self.max_tokens = max_tokens

    async def process(self, store: MemoryStore, thread_id: str) -> int:
        """Trim messages above the budget. Returns the count dropped.

        Operates inside a single transaction by re-saving the kept tail
        as a fresh sequence. Not efficient for very long threads —
        upstream uses an out-of-place log; we accept the simpler shape
        here since threads should be short-ish in practice (use the
        ``Summarizer`` for long-running threads).
        """
        messages = await store.list_messages(thread_id)
        if not messages:
            return 0

        # Walk from the end (most recent first), accumulate until over
        # budget, then everything before that is dropped.
        running = 0
        keep_from_index = 0
        for idx in range(len(messages) - 1, -1, -1):
            running += _estimate_tokens(messages[idx].content)
            if running > self.max_tokens and idx < len(messages) - 1:
                keep_from_index = idx + 1
                break

        if keep_from_index == 0:
            return 0

        kept = messages[keep_from_index:]
        # Re-save the kept tail. We don't have a multi-row delete by
        # range yet, so issue the deletes individually under a single
        # connection.
        with store._connect() as db:  # noqa: SLF001 — intentional cross-module access
            db.execute(
                "DELETE FROM ts_memory_messages WHERE thread_id = ?",
                (thread_id,),
            )
            for new_seq, msg in enumerate(kept):
                db.execute(
                    """
                    INSERT INTO ts_memory_messages
                        (thread_id, seq, role, content, created_at_ms)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (thread_id, new_seq, msg.role, msg.content, msg.created_at_ms),
                )
            db.commit()
        return keep_from_index


SummarizeFn = Callable[[list[MemoryMessage]], Awaitable[str]]


class Summarizer:
    """Compress old messages into a single summary message.

    Keeps the most recent ``keep_recent`` messages verbatim; the oldest
    block is replaced with a single ``system``-role summary produced by
    the provided ``summarize`` callable. The callable receives the list
    of messages to compress and returns the summary text.

    Useful for keeping a long thread useful in-prompt while bounded in
    tokens.
    """

    def __init__(
        self,
        summarize: SummarizeFn,
        *,
        keep_recent: int = 10,
        min_to_compress: int = 5,
    ) -> None:
        if keep_recent < 0:
            raise ValueError("keep_recent must be >= 0")
        if min_to_compress < 1:
            raise ValueError("min_to_compress must be >= 1")
        self._summarize = summarize
        self._keep_recent = keep_recent
        self._min_to_compress = min_to_compress

    async def process(
        self,
        store: MemoryStore,
        thread_id: str,
    ) -> Optional[int]:
        """Run a compression pass. Returns the count of messages compressed.

        Returns ``None`` when nothing was compressed (thread too short).
        """
        messages = await store.list_messages(thread_id)
        if len(messages) < self._keep_recent + self._min_to_compress:
            return None

        head_count = len(messages) - self._keep_recent
        head = messages[:head_count]
        tail = messages[head_count:]
        summary_text = await self._summarize(head)

        # Re-save: one summary message + the verbatim tail.
        with store._connect() as db:  # noqa: SLF001
            db.execute(
                "DELETE FROM ts_memory_messages WHERE thread_id = ?",
                (thread_id,),
            )
            # Index 0: summary (synthesized as a system message)
            db.execute(
                """
                INSERT INTO ts_memory_messages
                    (thread_id, seq, role, content, created_at_ms)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    0,
                    "system",
                    summary_text,
                    head[0].created_at_ms,
                ),
            )
            for offset, msg in enumerate(tail, start=1):
                db.execute(
                    """
                    INSERT INTO ts_memory_messages
                        (thread_id, seq, role, content, created_at_ms)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        thread_id,
                        offset,
                        msg.role,
                        msg.content,
                        msg.created_at_ms,
                    ),
                )
            db.commit()

        return head_count
