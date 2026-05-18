"""Smithers cross-run memory (working/messages/recall).

Three layers:
- Working memory: key-value facts with optional TTL
- Message history: append-only chat threads
- Semantic recall: vector search via pluggable embedding adapters

```python
from smithers_py_meta.memory import (
    MemoryStore,
    MemoryNamespace,
    OpenAIEmbeddingAdapter,
)

store = MemoryStore(
    db_path="smithers.db",
    embeddings=OpenAIEmbeddingAdapter(),
)

ns = MemoryNamespace(kind="workflow", id="code-review")
await store.set(ns, "last-review", {"approved": True})
await store.get(ns, "last-review")
```
"""

from __future__ import annotations

from .embeddings import (
    NullEmbeddingAdapter,
    OpenAIEmbeddingAdapter,
)
from .processors import (
    Summarizer,
    TokenLimiter,
    TtlGarbageCollector,
)
from .store import MemoryStore
from .types import (
    MemoryFact,
    MemoryMessage,
    MemoryNamespace,
    MemoryThread,
)

__all__ = [
    # Store
    "MemoryStore",
    # Types
    "MemoryFact",
    "MemoryMessage",
    "MemoryNamespace",
    "MemoryThread",
    # Embeddings
    "NullEmbeddingAdapter",
    "OpenAIEmbeddingAdapter",
    # Processors
    "Summarizer",
    "TokenLimiter",
    "TtlGarbageCollector",
]
