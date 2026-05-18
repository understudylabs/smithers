"""Shared types for the smithers_py memory subsystem.

Matches the upstream TS shapes documented at /llms-memory.txt:

- ``MemoryNamespace`` — kind/id tuple scoping facts and threads
- ``MemoryFact`` — a key/value pair with optional metadata + TTL
- ``MemoryMessage`` — one message in a thread
- ``MemoryThread`` — convenience wrapper around an ordered list of messages

All shapes are Pydantic so they serialize cleanly to JSON in SQLite columns
and can be validated at the API boundary.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


MemoryNamespaceKind = Literal["workflow", "agent", "user", "global"]
"""Four namespace lifetimes from upstream Smithers."""


class MemoryNamespace(BaseModel):
    """Scope for a set of facts or a thread.

    Kind picks the lifetime; id distinguishes instances within a kind.
    e.g. ``{kind: "workflow", id: "code-review"}`` keeps facts scoped to
    a specific workflow definition.
    """

    kind: MemoryNamespaceKind
    id: str

    def as_tuple(self) -> tuple[str, str]:
        """Used as the SQL primary key fragment for facts."""
        return (self.kind, self.id)


class MemoryFact(BaseModel):
    """A single (key, value) entry with optional metadata + TTL.

    ``expires_at_ms`` is an absolute Unix epoch in milliseconds. The
    ``TtlGarbageCollector`` processor sweeps expired facts; reads do not
    filter on expiry unless explicitly told to.
    """

    key: str
    value: Any
    metadata: Optional[dict[str, Any]] = None
    created_at_ms: Optional[int] = None
    expires_at_ms: Optional[int] = None


MessageRole = Literal["user", "assistant", "system"]


class MemoryMessage(BaseModel):
    """One message in a memory thread.

    Threads are append-only and identified by an arbitrary ``thread_id``
    string (typically derived from agent + user + workflow).
    """

    role: MessageRole
    content: str
    created_at_ms: Optional[int] = None


class MemoryThread(BaseModel):
    """Convenience wrapper exposed by ``store.get_thread(thread_id)``."""

    id: str
    messages: list[MemoryMessage] = Field(default_factory=list)
