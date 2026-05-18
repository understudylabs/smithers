"""Per-Task output caching with explicit invalidation.

Mirrors the upstream Smithers cache surface: cache keys are derived
from a user-supplied function ``cache.by(ctx)`` plus a string
``version`` plus the output schema's signature. The schema signature
piece means a schema change auto-invalidates stale rows — the validator
rejects them on read, so the cache misses safely rather than returning
the wrong shape.

```python
from smithers_py.cache import Cache, CachePolicy

policy = CachePolicy(
    by=lambda ctx: {"repo": ctx.input.repo},
    version="v3",
    scope="workflow",  # "run" | "workflow" | "global"
    ttl_ms=3_600_000,
)
cache = Cache(db_path="smithers.db")

key = cache.compute_key(
    policy, ctx, schema_signature=hash(output_schema_string)
)
hit = cache.get(key)
if hit is not None:
    return hit
# ... run task ...
cache.set(key, output_value, ttl_ms=policy.ttl_ms)
```

Side-effect tasks should not be cached. The runtime layer enforces
this by refusing to honor a ``cache`` prop on tasks declared as
side-effecting; this module doesn't enforce it directly — callers
gate.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Literal, Optional


CacheScope = Literal["run", "workflow", "global"]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS ts_cache (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    expires_at_ms INTEGER,
    schema_signature TEXT
);

CREATE INDEX IF NOT EXISTS idx_ts_cache_expiry
    ON ts_cache(expires_at_ms);
"""


@dataclass
class CachePolicy:
    """Cache configuration attached to a Task.

    The cache key is built deterministically from ``by(ctx)`` (any
    JSON-serializable value), ``version`` (a string the user bumps to
    invalidate), and ``schema_signature`` (auto-computed from the
    task's output schema). All three contribute equally to the key
    hash, so changing any one of them invalidates the entry without
    requiring a manual purge.

    ``scope`` controls cache visibility:

    - ``"run"`` — keyed under the current run; useful for memoizing
      repeated work within a single execution.
    - ``"workflow"`` — shared across runs of the same workflow
      definition; the typical choice for "don't recompute this if the
      inputs are the same".
    - ``"global"`` — shared across all workflows. Use sparingly; mostly
      for cross-workflow shared data.

    ``ttl_ms`` is optional. Cache entries past their TTL are skipped on
    read; cleanup happens lazily (next ``sweep()`` call) since the
    cost of leaving them is low.
    """

    by: Optional[Callable[[Any], Any]] = None
    version: str = ""
    scope: CacheScope = "workflow"
    ttl_ms: Optional[int] = None


@dataclass
class CacheHit:
    """A successful cache lookup with provenance for diagnostics."""

    value: Any
    created_at_ms: int
    expires_at_ms: Optional[int]


def _compute_signature(value: Any) -> str:
    """Stable SHA-256 hex digest for any JSON-serializable value.

    Uses sorted-keys + default-string fallback so equivalent objects
    produce the same hash regardless of insertion order.
    """
    serialized = json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def compute_cache_key(
    policy: CachePolicy,
    *,
    ctx: Any = None,
    schema_signature: str = "",
    scope_id: str = "",
) -> str:
    """Build the canonical cache key for a (policy, ctx, schema) triple.

    The key is a single string of the form
    ``<scope>:<scope_id>:<digest>`` so cache rows can be filtered or
    purged by scope.

    - ``policy.by(ctx)`` is hashed if ``by`` is set; otherwise the
      empty-string signature is used.
    - ``policy.version`` is folded into the digest verbatim.
    - ``schema_signature`` is the hash of the task's output schema
      (the caller computes it once per task and passes it in).
    - ``scope_id`` is the run_id / workflow_name / "global" the cache
      is scoped to; lets the key reflect the scope.
    """
    by_payload = policy.by(ctx) if policy.by is not None else None
    digest_input = json.dumps(
        {
            "by": by_payload,
            "version": policy.version,
            "schema": schema_signature,
        },
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:32]
    return f"{policy.scope}:{scope_id or 'default'}:{digest}"


class Cache:
    """SQLite-backed cache. Initializes the ``ts_cache`` table on
    first connect (idempotent CREATE TABLE IF NOT EXISTS).
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._init_schema()

    def compute_key(
        self,
        policy: CachePolicy,
        ctx: Any = None,
        *,
        schema_signature: str = "",
        scope_id: str = "",
    ) -> str:
        """Compute the cache key for a (policy, ctx, schema) triple."""
        return compute_cache_key(
            policy,
            ctx=ctx,
            schema_signature=schema_signature,
            scope_id=scope_id,
        )

    def get(self, key: str) -> Optional[CacheHit]:
        """Look up a key. Returns ``None`` if missing or expired."""
        with self._connect() as db:
            row = db.execute(
                """
                SELECT value_json, created_at_ms, expires_at_ms
                FROM ts_cache
                WHERE key = ?
                """,
                (key,),
            ).fetchone()
        if row is None:
            return None
        value_json, created_at, expires_at = row
        if expires_at is not None and expires_at <= _now_ms():
            return None
        return CacheHit(
            value=json.loads(value_json),
            created_at_ms=created_at,
            expires_at_ms=expires_at,
        )

    def set(
        self,
        key: str,
        value: Any,
        *,
        ttl_ms: Optional[int] = None,
        schema_signature: str = "",
    ) -> None:
        """Write a value. Last-write-wins."""
        now = _now_ms()
        expires_at = now + ttl_ms if ttl_ms is not None else None
        with self._connect() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO ts_cache (
                    key, value_json, created_at_ms,
                    expires_at_ms, schema_signature
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    key,
                    json.dumps(value, default=str),
                    now,
                    expires_at,
                    schema_signature or None,
                ),
            )

    def delete(self, key: str) -> bool:
        """Drop a key. Returns True if a row was removed."""
        with self._connect() as db:
            cur = db.execute("DELETE FROM ts_cache WHERE key = ?", (key,))
            return cur.rowcount > 0

    def purge_scope(self, scope: CacheScope, scope_id: str = "default") -> int:
        """Drop every entry under ``<scope>:<scope_id>:``.

        Returns the number of rows removed. Useful when a run ends or a
        workflow is re-deployed.
        """
        prefix = f"{scope}:{scope_id}:"
        with self._connect() as db:
            cur = db.execute(
                "DELETE FROM ts_cache WHERE key LIKE ?",
                (prefix + "%",),
            )
            return cur.rowcount

    def sweep_expired(self, *, now_ms: Optional[int] = None) -> int:
        """Delete entries past their TTL. Returns the count removed."""
        cutoff = now_ms if now_ms is not None else _now_ms()
        with self._connect() as db:
            cur = db.execute(
                "DELETE FROM ts_cache WHERE expires_at_ms IS NOT NULL AND expires_at_ms <= ?",
                (cutoff,),
            )
            return cur.rowcount

    def _init_schema(self) -> None:
        with self._connect() as db:
            db.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self._db_path, isolation_level=None, timeout=30.0)
        try:
            db.execute("PRAGMA journal_mode = WAL")
            db.execute("PRAGMA synchronous = NORMAL")
            yield db
        finally:
            db.close()


def _now_ms() -> int:
    return int(time.time() * 1000)


def compute_schema_signature(schema: Any) -> str:
    """Compute a stable signature for a Pydantic-class schema.

    Used by the runtime to fold the schema shape into the cache key.
    Schema changes (added / removed / renamed fields, type changes)
    produce different signatures and thus invalidate stale entries.

    Accepts either a Pydantic model class (uses its ``model_json_schema()``)
    or any JSON-serializable value (uses its sorted-JSON hash).
    """
    if schema is None:
        return ""
    if hasattr(schema, "model_json_schema"):
        try:
            return _compute_signature(schema.model_json_schema())
        except Exception:
            pass
    return _compute_signature(schema)


__all__ = [
    "Cache",
    "CacheHit",
    "CachePolicy",
    "CacheScope",
    "compute_cache_key",
    "compute_schema_signature",
]
