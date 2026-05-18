"""SQLite-backed memory store with three layers + four namespaces.

Layers match the upstream Smithers documentation:

- **Working memory** — ``set(ns, key, value)`` / ``get(ns, key)``. Facts
  with optional TTL. Last-write-wins.
- **Message history** — ``save_message(thread_id, msg)`` /
  ``list_messages(thread_id)``. Ordered chat threads.
- **Semantic recall** — ``recall(ns, query, top_k)``. Vector search over
  stored facts. Requires an ``EmbeddingAdapter`` at construction time.

Tables:

- ``ts_memory_facts (namespace_kind, namespace_id, key, value_json,
  metadata_json, created_at_ms, expires_at_ms, embedding BLOB,
  embedding_model)`` — primary key ``(namespace_kind, namespace_id,
  key)``. Last-write-wins via ``INSERT OR REPLACE``.
- ``ts_memory_messages (thread_id, seq, role, content, created_at_ms)``
  — primary key ``(thread_id, seq)`` where seq is a monotonically
  increasing integer per thread.

All writes are eventually consistent with the rest of the runtime —
memory state is separate from frame state. Don't use it for run-scoped
data that needs to be atomic with the workflow's frame commits; use
``ctx`` and a Task output instead.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from .embeddings import (
    EmbeddingAdapter,
    cosine_similarity,
    pack_vector,
    unpack_vector,
)
from .types import (
    MemoryFact,
    MemoryMessage,
    MemoryNamespace,
    MemoryThread,
)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS ts_memory_facts (
    namespace_kind TEXT NOT NULL,
    namespace_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    metadata_json TEXT,
    created_at_ms INTEGER NOT NULL,
    expires_at_ms INTEGER,
    embedding BLOB,
    embedding_model TEXT,
    PRIMARY KEY (namespace_kind, namespace_id, key)
);

CREATE INDEX IF NOT EXISTS idx_ts_memory_facts_expiry
    ON ts_memory_facts(expires_at_ms);

CREATE TABLE IF NOT EXISTS ts_memory_messages (
    thread_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    PRIMARY KEY (thread_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_ts_memory_messages_thread
    ON ts_memory_messages(thread_id, seq);
"""


def _now_ms() -> int:
    return int(time.time() * 1000)


class MemoryStore:
    """Three-layer memory store.

    Construct with a SQLite path and an optional embedding adapter. Pass
    no embedding adapter to disable semantic recall (``recall()`` will
    raise ``RuntimeError`` in that case).

    The store is sync (stdlib ``sqlite3``); the public API is ``async``
    for forward-compatibility with an async backend. All current
    methods are coroutines that wrap synchronous DB calls — they're
    safe to ``await`` from any event loop.
    """

    def __init__(
        self,
        db_path: str,
        *,
        embeddings: Optional[EmbeddingAdapter] = None,
    ) -> None:
        self._db_path = db_path
        self._embeddings = embeddings
        self._init_schema()

    # ----- public API ----------------------------------------------------

    @property
    def embeddings(self) -> Optional[EmbeddingAdapter]:
        """The configured embedding adapter, or None if semantic recall is off."""
        return self._embeddings

    async def set(
        self,
        namespace: MemoryNamespace,
        key: str,
        value: Any,
        *,
        metadata: Optional[dict[str, Any]] = None,
        ttl_ms: Optional[int] = None,
        embed: bool = True,
    ) -> None:
        """Write a fact. Last-write-wins.

        Pass ``ttl_ms`` to set ``expires_at_ms = now + ttl_ms``. Pass
        ``embed=False`` to skip embedding even when an adapter is
        configured (e.g., for large non-text values).
        """
        now = _now_ms()
        expires_at = now + ttl_ms if ttl_ms is not None else None

        embedding_blob: Optional[bytes] = None
        embedding_model: Optional[str] = None
        if embed and self._embeddings is not None and isinstance(value, (str, dict, list)):
            text = value if isinstance(value, str) else json.dumps(value)
            vectors = await self._embeddings.embed([text])
            if vectors:
                embedding_blob = pack_vector(vectors[0])
                embedding_model = self._embeddings.model

        with self._connect() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO ts_memory_facts (
                    namespace_kind, namespace_id, key,
                    value_json, metadata_json,
                    created_at_ms, expires_at_ms,
                    embedding, embedding_model
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    namespace.kind,
                    namespace.id,
                    key,
                    json.dumps(value),
                    json.dumps(metadata) if metadata is not None else None,
                    now,
                    expires_at,
                    embedding_blob,
                    embedding_model,
                ),
            )
            db.commit()

    async def get(
        self,
        namespace: MemoryNamespace,
        key: str,
        *,
        include_expired: bool = False,
    ) -> Any:
        """Read a fact by key. Returns ``None`` if missing or expired.

        Pass ``include_expired=True`` to surface facts past their TTL
        (useful for diagnostics or for the ``TtlGarbageCollector`` itself).
        """
        with self._connect() as db:
            row = db.execute(
                """
                SELECT value_json, expires_at_ms
                FROM ts_memory_facts
                WHERE namespace_kind = ?
                  AND namespace_id = ?
                  AND key = ?
                """,
                (namespace.kind, namespace.id, key),
            ).fetchone()
        if row is None:
            return None
        value_json, expires_at = row
        if not include_expired and expires_at is not None and expires_at <= _now_ms():
            return None
        return json.loads(value_json)

    async def list(
        self,
        namespace: MemoryNamespace,
        *,
        include_expired: bool = False,
    ) -> list[MemoryFact]:
        """List every fact in a namespace. Skips expired by default."""
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT key, value_json, metadata_json,
                       created_at_ms, expires_at_ms
                FROM ts_memory_facts
                WHERE namespace_kind = ? AND namespace_id = ?
                ORDER BY key ASC
                """,
                (namespace.kind, namespace.id),
            ).fetchall()
        now = _now_ms()
        facts: list[MemoryFact] = []
        for row in rows:
            key, value_json, metadata_json, created_at, expires_at = row
            if not include_expired and expires_at is not None and expires_at <= now:
                continue
            facts.append(
                MemoryFact(
                    key=key,
                    value=json.loads(value_json),
                    metadata=json.loads(metadata_json) if metadata_json else None,
                    created_at_ms=created_at,
                    expires_at_ms=expires_at,
                )
            )
        return facts

    async def delete(self, namespace: MemoryNamespace, key: str) -> bool:
        """Delete a fact. Returns True if a row was removed."""
        with self._connect() as db:
            cur = db.execute(
                """
                DELETE FROM ts_memory_facts
                WHERE namespace_kind = ?
                  AND namespace_id = ?
                  AND key = ?
                """,
                (namespace.kind, namespace.id, key),
            )
            db.commit()
            return cur.rowcount > 0

    async def recall(
        self,
        namespace: MemoryNamespace,
        query: str,
        *,
        top_k: int = 5,
    ) -> list[MemoryFact]:
        """Vector search over stored facts.

        Requires an embedding adapter at construction. Embeddings are
        computed for the query at call time; stored facts use their
        cached embeddings (set during ``set()`` with ``embed=True``).
        Facts without an embedding are skipped.

        Returns up to ``top_k`` facts in descending cosine similarity.
        Skips expired facts.
        """
        if self._embeddings is None:
            raise RuntimeError(
                "MemoryStore.recall() requires an embedding adapter; "
                "construct MemoryStore(..., embeddings=OpenAIEmbeddingAdapter())"
            )
        if top_k <= 0:
            return []

        query_vec_batch = await self._embeddings.embed([query])
        if not query_vec_batch:
            return []
        query_vec = query_vec_batch[0]

        now = _now_ms()
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT key, value_json, metadata_json,
                       created_at_ms, expires_at_ms,
                       embedding, embedding_model
                FROM ts_memory_facts
                WHERE namespace_kind = ?
                  AND namespace_id = ?
                  AND embedding IS NOT NULL
                """,
                (namespace.kind, namespace.id),
            ).fetchall()

        scored: list[tuple[float, MemoryFact]] = []
        for row in rows:
            (
                key,
                value_json,
                metadata_json,
                created_at,
                expires_at,
                blob,
                model,
            ) = row
            if expires_at is not None and expires_at <= now:
                continue
            if model != self._embeddings.model:
                # Mismatched embedding model — skip rather than mix
                # dimensions or compare across incompatible spaces.
                continue
            vec = unpack_vector(blob, self._embeddings.dimensions)
            score = cosine_similarity(query_vec, vec)
            scored.append(
                (
                    score,
                    MemoryFact(
                        key=key,
                        value=json.loads(value_json),
                        metadata=json.loads(metadata_json) if metadata_json else None,
                        created_at_ms=created_at,
                        expires_at_ms=expires_at,
                    ),
                )
            )

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [fact for _, fact in scored[:top_k]]

    async def save_message(
        self,
        thread_id: str,
        message: MemoryMessage,
    ) -> int:
        """Append a message to a thread. Returns the assigned ``seq``."""
        ts = message.created_at_ms or _now_ms()
        with self._connect() as db:
            cur = db.execute(
                "SELECT COALESCE(MAX(seq), -1) + 1 FROM ts_memory_messages WHERE thread_id = ?",
                (thread_id,),
            )
            (next_seq,) = cur.fetchone()
            db.execute(
                """
                INSERT INTO ts_memory_messages (
                    thread_id, seq, role, content, created_at_ms
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (thread_id, next_seq, message.role, message.content, ts),
            )
            db.commit()
            return next_seq

    async def list_messages(
        self,
        thread_id: str,
        *,
        limit: Optional[int] = None,
    ) -> list[MemoryMessage]:
        """List messages in a thread in insertion order.

        Pass ``limit`` to read only the most recent N messages (still
        returned in chronological order).
        """
        with self._connect() as db:
            if limit is None:
                rows = db.execute(
                    """
                    SELECT role, content, created_at_ms
                    FROM ts_memory_messages
                    WHERE thread_id = ?
                    ORDER BY seq ASC
                    """,
                    (thread_id,),
                ).fetchall()
            else:
                # SQLite has no clean "last N in chronological order" so
                # we fetch the tail descending and reverse in Python.
                rows = db.execute(
                    """
                    SELECT role, content, created_at_ms
                    FROM (
                        SELECT role, content, created_at_ms, seq
                        FROM ts_memory_messages
                        WHERE thread_id = ?
                        ORDER BY seq DESC
                        LIMIT ?
                    ) ORDER BY seq ASC
                    """,
                    (thread_id, limit),
                ).fetchall()
        return [
            MemoryMessage(role=role, content=content, created_at_ms=created_at)
            for role, content, created_at in rows
        ]

    async def get_thread(self, thread_id: str) -> MemoryThread:
        """Convenience wrapper around ``list_messages``."""
        return MemoryThread(
            id=thread_id,
            messages=await self.list_messages(thread_id),
        )

    async def expire_sweep(self, *, now_ms: Optional[int] = None) -> int:
        """Delete facts past their TTL. Returns the number of rows removed.

        Called by ``TtlGarbageCollector.process``. Safe to call manually.
        """
        cutoff = now_ms if now_ms is not None else _now_ms()
        with self._connect() as db:
            cur = db.execute(
                "DELETE FROM ts_memory_facts WHERE expires_at_ms IS NOT NULL AND expires_at_ms <= ?",
                (cutoff,),
            )
            db.commit()
            return cur.rowcount

    # ----- internals -----------------------------------------------------

    def _init_schema(self) -> None:
        with self._connect() as db:
            db.executescript(_SCHEMA)
            db.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # WAL mode keeps reads non-blocking while a writer is active.
        db = sqlite3.connect(self._db_path, isolation_level=None, timeout=30.0)
        try:
            db.execute("PRAGMA journal_mode = WAL")
            db.execute("PRAGMA synchronous = NORMAL")
            yield db
        finally:
            db.close()
