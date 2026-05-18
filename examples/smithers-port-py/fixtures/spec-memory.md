# `smithers_py.memory` — cross-run memory (working/messages/recall)

Mirrors upstream Smithers' memory surface
(`/llms-memory.txt`). Three layers, four namespaces, three
maintenance processors, pluggable embedding adapter.

## Public surface

```python
from smithers_py.memory import (
    MemoryStore,
    MemoryNamespace,
    MemoryMessage,
    OpenAIEmbeddingAdapter,
    NullEmbeddingAdapter,
    TtlGarbageCollector,
    TokenLimiter,
    Summarizer,
)

store = MemoryStore(
    db_path="smithers.db",
    embeddings=OpenAIEmbeddingAdapter(),  # optional; None disables recall
)

ns = MemoryNamespace(kind="workflow", id="code-review")
await store.set(ns, "last-review", {"approved": True, "issues": 3})
await store.get(ns, "last-review")           # -> {...}
await store.recall(ns, "auth bugs", top_k=3) # -> list[MemoryFact]

await store.save_message("thread-1", MemoryMessage(role="user", content="hi"))
await store.list_messages("thread-1", limit=10)
```

## Three layers

| Layer | API | Purpose |
| --- | --- | --- |
| Working memory | `set(ns, key, value, ttl_ms?)` / `get(ns, key)` / `list(ns)` / `delete(ns, key)` | Key-value facts. Optional TTL. Last-write-wins. |
| Message history | `save_message(thread_id, message)` / `list_messages(thread_id, limit?)` / `get_thread(thread_id)` | Append-only chat threads. Sequence-ordered. |
| Semantic recall | `recall(ns, query, top_k=5)` | Vector search via cosine similarity. Requires embedding adapter. |

## Four namespaces

`MemoryNamespace.kind` is one of `"workflow"`, `"agent"`, `"user"`,
`"global"`. Pick by lifetime — `workflow` scopes to a workflow
definition, `global` is shared everywhere. `kind + id` is the
composite namespace key.

## Pluggable embedding adapter

```python
class EmbeddingAdapter(Protocol):
    @property
    def model(self) -> str: ...
    @property
    def dimensions(self) -> int: ...
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
```

Built-ins:

- `OpenAIEmbeddingAdapter(model="text-embedding-3-small", api_key=None, base_url=None)`
  — requires the `openai` package; reads `OPENAI_API_KEY` from env if
  `api_key` not provided. 1536 dims for the small model.
- `NullEmbeddingAdapter(dimensions=8)` — zero vectors; for tests.

## Processors

Three maintenance routines, each with a single `process(store, ...)`
async method:

- `TtlGarbageCollector` — sweeps expired facts.
  `await TtlGarbageCollector().process(store)` returns the count removed.
- `TokenLimiter(max_tokens)` — trims a thread's history below a token
  budget. ~4-char-per-token heuristic. `await limiter.process(store,
  thread_id)`.
- `Summarizer(summarize_fn, keep_recent=10, min_to_compress=5)` —
  replaces the oldest N messages with a single `system`-role summary
  produced by an LLM. `summarize_fn(messages) -> str`.

## SQLite schema

Two tables, both with `ts_*` prefix to match the project convention.
WAL mode.

```sql
CREATE TABLE ts_memory_facts (
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

CREATE TABLE ts_memory_messages (
    thread_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    PRIMARY KEY (thread_id, seq)
);
```

Embeddings pack as little-endian float32 BLOB via stdlib `struct`. No
numpy dependency.

## Types

```python
class MemoryFact(BaseModel):
    key: str
    value: Any
    metadata: Optional[dict[str, Any]] = None
    created_at_ms: Optional[int] = None
    expires_at_ms: Optional[int] = None

class MemoryMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str
    created_at_ms: Optional[int] = None

class MemoryThread(BaseModel):
    id: str
    messages: list[MemoryMessage] = Field(default_factory=list)
```

## Recall behavior

- Embeds the query via the configured adapter at call time.
- Computes cosine similarity against every stored embedding in the
  namespace whose `embedding_model` matches the current adapter's
  `model` tag.
- Returns top-K facts by descending similarity, skipping expired.
- Returns empty list if `top_k <= 0` or no embedding adapter.
- Raises `RuntimeError` if `recall()` is called when no adapter is
  configured.

## Mismatched-model skip

Facts whose stored `embedding_model` doesn't match the current
adapter's model are skipped during recall. Prevents accidentally
mixing dimensions or comparing across incompatible embedding spaces.

## Files to produce

- `__init__.py` — public exports
- `types.py` — `MemoryNamespace`, `MemoryFact`, `MemoryMessage`,
  `MemoryThread` Pydantic types
- `embeddings.py` — `EmbeddingAdapter` Protocol, `OpenAIEmbeddingAdapter`,
  `NullEmbeddingAdapter`, `pack_vector`, `unpack_vector`,
  `cosine_similarity`
- `store.py` — `MemoryStore` class
- `processors.py` — `TtlGarbageCollector`, `TokenLimiter`, `Summarizer`
- `test_memory.py` — pytest-asyncio tests covering: set/get/list/
  delete, TTL expiry, namespace isolation, message history, semantic
  recall ordering, mismatched-model skip, all three processors
