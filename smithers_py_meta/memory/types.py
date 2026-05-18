"""Shared types for the smithers_py.memory subsystem.

Cross-run memory with three layers: working memory (key-value facts),
message history (append-only chat threads), and semantic recall (vector
search).
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class MemoryNamespace(BaseModel):
    """Composite namespace key scoping a fact to a specific lifetime.

    ``kind`` determines the lifetime — ``workflow`` scopes to a workflow
    definition, ``agent`` to a single agent instance, ``user`` to a user
    session, ``global`` is shared everywhere.
    """

    kind: Literal["workflow", "agent", "user", "global"]
    id: str


class MemoryFact(BaseModel):
    """A single key-value fact stored in working memory.

    ``value`` is JSON-serializable. ``metadata`` is optional additional
    structured data. ``expires_at_ms`` is optional TTL; when set, the fact
    is eligible for garbage collection after that timestamp.
    """

    key: str
    value: Any
    metadata: Optional[dict[str, Any]] = None
    created_at_ms: Optional[int] = None
    expires_at_ms: Optional[int] = None


class MemoryMessage(BaseModel):
    """A single message in a chat thread.

    ``role`` is one of ``user``, ``assistant``, ``system``. ``content`` is
    the message body. ``created_at_ms`` is auto-populated at save time.
    """

    role: Literal["user", "assistant", "system"]
    content: str
    created_at_ms: Optional[int] = None


class MemoryThread(BaseModel):
    """A chat thread with ordered messages.

    ``messages`` is ordered by ``seq`` ascending. ``id`` is the thread
    identifier passed to ``save_message`` and ``list_messages``.
    """

    id: str
    messages: list[MemoryMessage] = Field(default_factory=list)
