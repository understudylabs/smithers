# `smithers_py.cache` — task output caching with explicit invalidation

Per-Task cache key = user-supplied `by(ctx)` + `version` + schema
signature. Schema changes auto-invalidate stale entries. Mirrors
upstream Smithers' cache surface (/llms-core.txt#caching).

## Public surface

```python
from smithers_py.cache import (
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
    scope="workflow",   # "run" | "workflow" | "global"
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

## Types

```python
CacheScope = Literal["run", "workflow", "global"]

@dataclass
class CachePolicy:
    by: Optional[Callable[[Any], Any]] = None
    version: str = ""
    scope: CacheScope = "workflow"
    ttl_ms: Optional[int] = None

@dataclass
class CacheHit:
    value: Any
    created_at_ms: int
    expires_at_ms: Optional[int]
```

## Cache-key derivation

```python
def compute_cache_key(
    policy: CachePolicy,
    *,
    ctx: Any = None,
    schema_signature: str = "",
    scope_id: str = "",
) -> str: ...
```

Returns a string of the form
`"<scope>:<scope_id>:<sha256(by + version + schema)>"`. The digest is
a 32-char hex prefix of:

```python
json.dumps({"by": policy.by(ctx) if policy.by else None,
            "version": policy.version,
            "schema": schema_signature},
           sort_keys=True, default=str)
```

The scope prefix lets `purge_scope` drop entries by scope without
touching unrelated rows. `scope_id` defaults to `"default"` when not
provided.

Key determinism rules:
- Same inputs → identical key (sorted dict keys)
- Different `by(ctx)`, `version`, `schema_signature`, or `scope` →
  different keys
- Same `policy.by(ctx)` value with keys in different insertion order
  → identical keys (sort_keys=True)

## `Cache` class

```python
class Cache:
    def __init__(self, db_path: str) -> None: ...

    def compute_key(self, policy, ctx=None, *, schema_signature="", scope_id="") -> str: ...
    def get(self, key: str) -> Optional[CacheHit]: ...   # None if missing or expired
    def set(self, key: str, value: Any, *, ttl_ms=None, schema_signature="") -> None: ...
    def delete(self, key: str) -> bool: ...   # True if removed
    def purge_scope(self, scope: CacheScope, scope_id: str = "default") -> int: ...  # count removed
    def sweep_expired(self, *, now_ms=None) -> int: ...  # count removed
```

Notes:

- TTL filtering happens on `get` — expired entries return `None`
  without being deleted. Lazy GC via `sweep_expired`.
- `set` writes via `INSERT OR REPLACE`; last-write-wins.
- `value` must be JSON-serializable (uses `json.dumps(default=str)`
  for fallback).

## SQLite schema — `ts_cache`

```sql
CREATE TABLE ts_cache (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    expires_at_ms INTEGER,
    schema_signature TEXT
);

CREATE INDEX idx_ts_cache_expiry ON ts_cache(expires_at_ms);
```

WAL mode. The schema_signature column is informational only — the
actual key includes it.

## Schema signatures

```python
def compute_schema_signature(schema: Any) -> str: ...
```

Stable SHA-256 hex digest. For Pydantic models, uses
`schema.model_json_schema()`. For raw values, uses
`json.dumps(value, sort_keys=True, default=str)`. Returns empty
string for `None`.

Schema changes (added / removed / renamed fields, type changes)
produce different signatures and thus auto-invalidate cached entries.

## Files to produce

- `__init__.py` — single file holds everything; public exports at
  module top-level

That's it — cache is small enough to fit in one file plus a test
file:

- `test_cache.py` — pytest fixtures with tempfile sqlite. Cover:
  same-inputs → same-key (deterministic), different-by → different-
  key, different-version → different-key, different-schema → different-
  key, scope prefix in key, sorted dict keys for stability;
  get/set/delete/purge_scope/sweep_expired; TTL expiry; end-to-end
  memoization scenario; schema_signature stability and changes;
  None / raw dict handling.
