"""MemoryStore — SQLite-backed cross-run memory with semantic recall.

Three layers:
- Working memory: ``set``, ``get``, ``list``, ``delete``
- Message history: ``save_message``, ``list_messages``, ``get_thread``
- Semantic recall: ``recall`` (requires embedding adapter)
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Optional

from .embeddings import EmbeddingAdapter, cosine_similarity, pack_vector, unpack_vector
from .types import MemoryFact, MemoryMessage, MemoryNamespace, MemoryThread


class MemoryStore:
    """SQLite-backed memory store with optional semantic recall.

    ``db_path`` is the SQLite file (created if missing). ``embeddings`` is
    an optional adapter; if ``None``, ``recall()`` will raise
    ``RuntimeError``.
    """

    def __init__(
        self,
        db_path: str,
        embeddings: Optional[EmbeddingAdapter] = None,
    ):
        self.db_path = db_path
        self.embeddings = embeddings
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        """Create tables if they don't exist."""
        self.conn.execute("""
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
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS ts_memory_messages (
                thread_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                PRIMARY KEY (thread_id, seq)
            )
        """)
        self.conn.commit()

    async def set(
        self,
        ns: MemoryNamespace,
        key: str,
        value: Any,
        ttl_ms: Optional[int] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Store a fact in working memory.

        ``ttl_ms`` sets an optional expiry; the fact is eligible for
        garbage collection after ``now() + ttl_ms``.
        """
        now_ms = int(time.time() * 1000)
        expires_at_ms = (now_ms + ttl_ms) if ttl_ms else None
        value_json = json.dumps(value)
        metadata_json = json.dumps(metadata) if metadata else None

        # Embed the value if an adapter is configured
        embedding_blob: Optional[bytes] = None
        embedding_model: Optional[str] = None
        if self.embeddings:
            text = json.dumps(value) if not isinstance(value, str) else value
            vectors = await self.embeddings.embed([text])
            embedding_blob = pack_vector(vectors[0])
            embedding_model = self.embeddings.model

        self.conn.execute(
            """
            INSERT OR REPLACE INTO ts_memory_facts
            (namespace_kind, namespace_id, key, value_json, metadata_json,
             created_at_ms, expires_at_ms, embedding, embedding_model)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ns.kind,
                ns.id,
                key,
                value_json,
                metadata_json,
                now_ms,
                expires_at_ms,
                embedding_blob,
                embedding_model,
            ),
        )
        self.conn.commit()

    async def get(self, ns: MemoryNamespace, key: str) -> Optional[Any]:
        """Retrieve a fact by key, or ``None`` if missing or expired."""
        now_ms = int(time.time() * 1000)
        row = self.conn.execute(
            """
            SELECT value_json FROM ts_memory_facts
            WHERE namespace_kind = ? AND namespace_id = ? AND key = ?
              AND (expires_at_ms IS NULL OR expires_at_ms > ?)
            """,
            (ns.kind, ns.id, key, now_ms),
        ).fetchone()
        if not row:
            return None
        return json.loads(row[0])

    async def list(self, ns: MemoryNamespace) -> list[MemoryFact]:
        """List all non-expired facts in a namespace."""
        now_ms = int(time.time() * 1000)
        rows = self.conn.execute(
            """
            SELECT key, value_json, metadata_json, created_at_ms, expires_at_ms
            FROM ts_memory_facts
            WHERE namespace_kind = ? AND namespace_id = ?
              AND (expires_at_ms IS NULL OR expires_at_ms > ?)
            """,
            (ns.kind, ns.id, now_ms),
        ).fetchall()
        return [
            MemoryFact(
                key=row[0],
                value=json.loads(row[1]),
                metadata=json.loads(row[2]) if row[2] else None,
                created_at_ms=row[3],
                expires_at_ms=row[4],
            )
            for row in rows
        ]

    async def delete(self, ns: MemoryNamespace, key: str) -> None:
        """Delete a fact by key."""
        self.conn.execute(
            """
            DELETE FROM ts_memory_facts
            WHERE namespace_kind = ? AND namespace_id = ? AND key = ?
            """,
            (ns.kind, ns.id, key),
        )
        self.conn.commit()

    async def recall(
        self,
        ns: MemoryNamespace,
        query: str,
        top_k: int = 5,
    ) -> list[MemoryFact]:
        """Semantic recall via vector similarity.

        Embeds ``query`` and returns the top-K facts by descending cosine
        similarity. Skips facts whose stored ``embedding_model`` doesn't
        match the current adapter's model.
        """
        if not self.embeddings:
            raise RuntimeError(
                "recall() requires an embedding adapter; pass "
                "embeddings=<adapter> to MemoryStore constructor"
            )
        if top_k <= 0:
            return []

        # Embed the query
        query_vec = (await self.embeddings.embed([query]))[0]
        model_tag = self.embeddings.model
        now_ms = int(time.time() * 1000)

        # Fetch all matching facts with embeddings
        rows = self.conn.execute(
            """
            SELECT key, value_json, metadata_json, created_at_ms,
                   expires_at_ms, embedding
            FROM ts_memory_facts
            WHERE namespace_kind = ? AND namespace_id = ?
              AND embedding IS NOT NULL
              AND embedding_model = ?
              AND (expires_at_ms IS NULL OR expires_at_ms > ?)
            """,
            (ns.kind, ns.id, model_tag, now_ms),
        ).fetchall()

        # Compute similarities
        scored: list[tuple[float, MemoryFact]] = []
        for row in rows:
            stored_vec = unpack_vector(row[5])
            sim = cosine_similarity(query_vec, stored_vec)
            fact = MemoryFact(
                key=row[0],
                value=json.loads(row[1]),
                metadata=json.loads(row[2]) if row[2] else None,
                created_at_ms=row[3],
                expires_at_ms=row[4],
            )
            scored.append((sim, fact))

        # Sort descending and take top-K
        scored.sort(key=lambda x: x[0], reverse=True)
        return [fact for _, fact in scored[:top_k]]

    async def save_message(
        self,
        thread_id: str,
        message: MemoryMessage,
    ) -> None:
        """Append a message to a thread.

        ``message.created_at_ms`` is auto-populated if not set. ``seq`` is
        auto-incremented.
        """
        now_ms = int(time.time() * 1000)
        created_at_ms = message.created_at_ms or now_ms

        # Get next seq
        row = self.conn.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 FROM ts_memory_messages WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        next_seq = row[0]

        self.conn.execute(
            """
            INSERT INTO ts_memory_messages (thread_id, seq, role, content, created_at_ms)
            VALUES (?, ?, ?, ?, ?)
            """,
            (thread_id, next_seq, message.role, message.content, created_at_ms),
        )
        self.conn.commit()

    async def list_messages(
        self,
        thread_id: str,
        limit: Optional[int] = None,
    ) -> list[MemoryMessage]:
        """List messages in a thread, ordered by seq ascending.

        ``limit`` caps the result count, taking the most recent messages.
        """
        if limit is None:
            rows = self.conn.execute(
                """
                SELECT role, content, created_at_ms
                FROM ts_memory_messages
                WHERE thread_id = ?
                ORDER BY seq ASC
                """,
                (thread_id,),
            ).fetchall()
        else:
            # Take the last N
            rows = self.conn.execute(
                """
                SELECT role, content, created_at_ms
                FROM ts_memory_messages
                WHERE thread_id = ?
                ORDER BY seq DESC
                LIMIT ?
                """,
                (thread_id, limit),
            ).fetchall()
            rows = list(reversed(rows))

        return [
            MemoryMessage(role=row[0], content=row[1], created_at_ms=row[2])
            for row in rows
        ]

    async def get_thread(self, thread_id: str) -> MemoryThread:
        """Retrieve a thread with all its messages."""
        messages = await self.list_messages(thread_id)
        return MemoryThread(id=thread_id, messages=messages)

    async def expire_sweep(self) -> int:
        """Remove all expired facts. Returns count removed."""
        now_ms = int(time.time() * 1000)
        cursor = self.conn.execute(
            "DELETE FROM ts_memory_facts WHERE expires_at_ms IS NOT NULL AND expires_at_ms <= ?",
            (now_ms,),
        )
        self.conn.commit()
        return cursor.rowcount
