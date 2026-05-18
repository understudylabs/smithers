"""Cross-run memory for smithers_py.

Mirrors the upstream Smithers memory surface documented at
/llms-memory.txt. Three layers (working memory, message history,
semantic recall), four namespaces (workflow / agent / user / global),
three processors (TtlGarbageCollector, TokenLimiter, Summarizer),
pluggable embedding adapters.

```python
from smithers_py.memory import MemoryStore, MemoryNamespace, OpenAIEmbeddingAdapter

store = MemoryStore(
    db_path="smithers.db",
    embeddings=OpenAIEmbeddingAdapter(),  # optional — disables recall when omitted
)

ns = MemoryNamespace(kind="workflow", id="code-review")
await store.set(ns, "last-review", {"approved": True, "issues": 3})
await store.get(ns, "last-review")           # -> {"approved": True, "issues": 3}
await store.recall(ns, "auth bugs", top_k=3) # -> [MemoryFact, ...]

await store.save_message("thread-1", MemoryMessage(role="user", content="hi"))
await store.list_messages("thread-1", limit=10)
```

The store writes to ``ts_memory_facts`` and ``ts_memory_messages``
tables in the same SQLite file as the rest of the runtime. No
coordination with frame commits — memory is per-namespace, not per-run.
"""

from __future__ import annotations

from .embeddings import (
    EmbeddingAdapter,
    NullEmbeddingAdapter,
    OpenAIEmbeddingAdapter,
)
from .processors import (
    Summarizer,
    SummarizeFn,
    TokenLimiter,
    TtlGarbageCollector,
)
from .store import MemoryStore
from .types import (
    MemoryFact,
    MemoryMessage,
    MemoryNamespace,
    MemoryNamespaceKind,
    MemoryThread,
    MessageRole,
)

__all__ = [
    # store
    "MemoryStore",
    # types
    "MemoryFact",
    "MemoryMessage",
    "MemoryNamespace",
    "MemoryNamespaceKind",
    "MemoryThread",
    "MessageRole",
    # embeddings
    "EmbeddingAdapter",
    "NullEmbeddingAdapter",
    "OpenAIEmbeddingAdapter",
    # processors
    "SummarizeFn",
    "Summarizer",
    "TokenLimiter",
    "TtlGarbageCollector",
]
