"""Smithers task output caching with explicit invalidation.

Per-task cache key = user-supplied `by(ctx)` + `version` + schema signature.
Schema changes auto-invalidate stale entries.

```python
from smithers_py_meta.cache import (
    Cache,
    CacheHit,
    CachePolicy,
    CacheScope,
    compute_cache_key,
    compute_schema_signature,
)

policy = CachePolicy(
    by=lambda ctx: {"repo": ctx.input.repo, "version": "v3"},
    version="v3",
    scope="workflow",
    ttl_ms=3_600_000,
)
cache = Cache(db_path="smithers.db")

key = cache.compute_key(
    policy, ctx,
    schema_signature=compute_schema_signature(MyOutputSchema),
    scope_id="my-wf",
)
hit = cache.get(key)
if hit is not None:
    return hit.value

# ... compute ...
cache.set(key, computed_value, ttl_ms=policy.ttl_ms)
```
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Optional

CacheScope = Literal["run", "workflow", "global"]


@dataclass
class CachePolicy:
    """Cache policy with invalidation rules."""

    by: Optional[Callable[[Any], Any]] = None
    version: str = ""
    scope: CacheScope = "workflow"
    ttl_ms: Optional[int] = None


@dataclass
class CacheHit:
    """Cached value with metadata."""

    value: Any
    created_at_ms: int
    expires_at_ms: Optional[int]


def compute_schema_signature(schema: Any) -> str:
    """Stable SHA-256 hex digest of schema structure.

    For Pydantic models, uses model_json_schema().
    For raw values, uses json.dumps with sorted keys.
    Returns empty string for None.
    """
    if schema is None:
        return ""

    # Try Pydantic BaseModel
    if hasattr(schema, "model_json_schema"):
        schema_dict = schema.model_json_schema()
        payload = json.dumps(schema_dict, sort_keys=True, default=str)
    else:
        # Fallback to direct serialization
        payload = json.dumps(schema, sort_keys=True, default=str)

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_cache_key(
    policy: CachePolicy,
    *,
    ctx: Any = None,
    schema_signature: str = "",
    scope_id: str = "",
) -> str:
    """Compute deterministic cache key.

    Returns format: "<scope>:<scope_id>:<sha256_hex_prefix>"

    The digest includes:
    - by(ctx) result (if policy.by is set)
    - policy.version
    - schema_signature

    Sorted dict keys ensure stability.
    """
    if not scope_id:
        scope_id = "default"

    by_value = policy.by(ctx) if policy.by and ctx else None

    payload_dict = {
        "by": by_value,
        "version": policy.version,
        "schema": schema_signature,
    }

    payload = json.dumps(payload_dict, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    return f"{policy.scope}:{scope_id}:{digest}"


class Cache:
    """SQLite-backed cache with TTL and scope support."""

    def __init__(self, db_path: str) -> None:
        """Initialize cache with SQLite database.

        Creates ts_cache table if needed. Enables WAL mode.
        """
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")

        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ts_cache (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                expires_at_ms INTEGER,
                schema_signature TEXT
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ts_cache_expiry
            ON ts_cache(expires_at_ms)
            """
        )
        self.conn.commit()

    def compute_key(
        self,
        policy: CachePolicy,
        ctx: Any = None,
        *,
        schema_signature: str = "",
        scope_id: str = "",
    ) -> str:
        """Convenience wrapper for compute_cache_key."""
        return compute_cache_key(
            policy,
            ctx=ctx,
            schema_signature=schema_signature,
            scope_id=scope_id,
        )

    def get(self, key: str) -> Optional[CacheHit]:
        """Retrieve cached value if present and not expired.

        Returns None if:
        - Key doesn't exist
        - Entry has expired (checked lazily)
        """
        now_ms = int(time.time() * 1000)

        row = self.conn.execute(
            """
            SELECT value_json, created_at_ms, expires_at_ms
            FROM ts_cache
            WHERE key = ?
            """,
            (key,),
        ).fetchone()

        if not row:
            return None

        value_json, created_at_ms, expires_at_ms = row

        # Lazy expiry check
        if expires_at_ms is not None and now_ms >= expires_at_ms:
            return None

        value = json.loads(value_json)
        return CacheHit(
            value=value,
            created_at_ms=created_at_ms,
            expires_at_ms=expires_at_ms,
        )

    def set(
        self,
        key: str,
        value: Any,
        *,
        ttl_ms: Optional[int] = None,
        schema_signature: str = "",
    ) -> None:
        """Store value with optional TTL.

        Uses INSERT OR REPLACE (last-write-wins).
        Value must be JSON-serializable.
        """
        now_ms = int(time.time() * 1000)
        expires_at_ms = (now_ms + ttl_ms) if ttl_ms else None

        value_json = json.dumps(value, default=str)

        self.conn.execute(
            """
            INSERT OR REPLACE INTO ts_cache
            (key, value_json, created_at_ms, expires_at_ms, schema_signature)
            VALUES (?, ?, ?, ?, ?)
            """,
            (key, value_json, now_ms, expires_at_ms, schema_signature),
        )
        self.conn.commit()

    def delete(self, key: str) -> bool:
        """Remove entry by key.

        Returns True if a row was deleted, False otherwise.
        """
        cursor = self.conn.execute("DELETE FROM ts_cache WHERE key = ?", (key,))
        self.conn.commit()
        return cursor.rowcount > 0

    def purge_scope(self, scope: CacheScope, scope_id: str = "default") -> int:
        """Delete all entries matching scope prefix.

        Returns count of removed rows.
        """
        prefix = f"{scope}:{scope_id}:"
        cursor = self.conn.execute(
            "DELETE FROM ts_cache WHERE key LIKE ?",
            (f"{prefix}%",),
        )
        self.conn.commit()
        return cursor.rowcount

    def sweep_expired(self, *, now_ms: Optional[int] = None) -> int:
        """Delete all expired entries.

        Returns count of removed rows.
        """
        if now_ms is None:
            now_ms = int(time.time() * 1000)

        cursor = self.conn.execute(
            """
            DELETE FROM ts_cache
            WHERE expires_at_ms IS NOT NULL
              AND expires_at_ms <= ?
            """,
            (now_ms,),
        )
        self.conn.commit()
        return cursor.rowcount


__all__ = [
    "Cache",
    "CacheHit",
    "CachePolicy",
    "CacheScope",
    "compute_cache_key",
    "compute_schema_signature",
]
