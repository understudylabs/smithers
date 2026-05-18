"""Tests for smithers_py_meta.memory subsystem.

Covers:
- Working memory: set/get/list/delete + TTL expiry
- Message history: save/list with limit
- Semantic recall: ordering by similarity, mismatched-model skip
- Processors: TtlGarbageCollector, TokenLimiter, Summarizer
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest

from smithers_py_meta.memory import (
    MemoryFact,
    MemoryMessage,
    MemoryNamespace,
    MemoryStore,
    NullEmbeddingAdapter,
    Summarizer,
    TokenLimiter,
    TtlGarbageCollector,
)
from smithers_py_meta.memory.embeddings import EmbeddingAdapter


class DeterministicEmbeddingAdapter:
    """Deterministic embedding adapter for testing.

    Returns vectors based on text content to ensure reproducible recall
    ordering. Uses character codes to create non-parallel vectors.
    """

    def __init__(self, dimensions: int = 8):
        self._dimensions = dimensions

    @property
    def model(self) -> str:
        return "deterministic-v1"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return vectors based on text content."""
        vectors = []
        for text in texts:
            # Create a vector where each dimension gets a different char code
            vec = []
            for i in range(self._dimensions):
                if i < len(text):
                    vec.append(float(ord(text[i])))
                else:
                    vec.append(0.0)
            vectors.append(vec)
        return vectors


@pytest.fixture
def db_path():
    """Create a temporary SQLite database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def store(db_path):
    """Create a MemoryStore with deterministic embeddings."""
    return MemoryStore(db_path, embeddings=DeterministicEmbeddingAdapter())


@pytest.mark.asyncio
async def test_set_get(store):
    """Test basic set/get."""
    ns = MemoryNamespace(kind="workflow", id="test")
    await store.set(ns, "key1", {"value": 42})
    result = await store.get(ns, "key1")
    assert result == {"value": 42}


@pytest.mark.asyncio
async def test_get_missing(store):
    """Test get on missing key returns None."""
    ns = MemoryNamespace(kind="workflow", id="test")
    result = await store.get(ns, "missing")
    assert result is None


@pytest.mark.asyncio
async def test_list(store):
    """Test listing facts in a namespace."""
    ns = MemoryNamespace(kind="workflow", id="test")
    await store.set(ns, "a", 1)
    await store.set(ns, "b", 2)
    facts = await store.list(ns)
    assert len(facts) == 2
    keys = {f.key for f in facts}
    assert keys == {"a", "b"}


@pytest.mark.asyncio
async def test_delete(store):
    """Test delete removes a fact."""
    ns = MemoryNamespace(kind="workflow", id="test")
    await store.set(ns, "key1", "value1")
    await store.delete(ns, "key1")
    result = await store.get(ns, "key1")
    assert result is None


@pytest.mark.asyncio
async def test_ttl_expiry(store):
    """Test that expired facts are not returned."""
    ns = MemoryNamespace(kind="workflow", id="test")
    # Set with 50ms TTL
    await store.set(ns, "key1", "value1", ttl_ms=50)
    # Should be available immediately
    assert await store.get(ns, "key1") == "value1"
    # Wait for expiry
    time.sleep(0.1)
    # Should be None
    assert await store.get(ns, "key1") is None


@pytest.mark.asyncio
async def test_namespace_isolation(store):
    """Test that namespaces are isolated."""
    ns1 = MemoryNamespace(kind="workflow", id="w1")
    ns2 = MemoryNamespace(kind="workflow", id="w2")
    await store.set(ns1, "key", "value1")
    await store.set(ns2, "key", "value2")
    assert await store.get(ns1, "key") == "value1"
    assert await store.get(ns2, "key") == "value2"


@pytest.mark.asyncio
async def test_save_message(store):
    """Test saving messages to a thread."""
    msg1 = MemoryMessage(role="user", content="hello")
    msg2 = MemoryMessage(role="assistant", content="hi there")
    await store.save_message("t1", msg1)
    await store.save_message("t1", msg2)
    messages = await store.list_messages("t1")
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[1].role == "assistant"


@pytest.mark.asyncio
async def test_list_messages_limit(store):
    """Test listing messages with a limit."""
    for i in range(10):
        await store.save_message("t1", MemoryMessage(role="user", content=f"msg{i}"))
    messages = await store.list_messages("t1", limit=3)
    assert len(messages) == 3
    # Should be the most recent 3
    assert messages[0].content == "msg7"
    assert messages[1].content == "msg8"
    assert messages[2].content == "msg9"


@pytest.mark.asyncio
async def test_get_thread(store):
    """Test get_thread returns a MemoryThread."""
    await store.save_message("t1", MemoryMessage(role="user", content="hello"))
    thread = await store.get_thread("t1")
    assert thread.id == "t1"
    assert len(thread.messages) == 1
    assert thread.messages[0].content == "hello"


@pytest.mark.asyncio
async def test_recall_ordering(store):
    """Test semantic recall orders by similarity."""
    ns = MemoryNamespace(kind="workflow", id="test")
    # Store facts with different text lengths
    await store.set(ns, "short", "ab")  # length 2
    await store.set(ns, "medium", "abcdef")  # length 6
    await store.set(ns, "long", "abcdefghij")  # length 10

    # Query with a medium-length text
    results = await store.recall(ns, "abcde", top_k=3)  # length 5
    # Deterministic adapter: similarity based on closeness of first component
    # Expect: medium (6) closest, then short (2), then long (10)
    assert len(results) == 3
    # Since our deterministic adapter returns [len(text), 0, 0, ...],
    # cosine similarity will favor vectors with similar first component
    # Query "abcde" (len=5) should be closest to "abcdef" (len=6)
    assert results[0].key == "medium"


@pytest.mark.asyncio
async def test_recall_mismatched_model_skip(store):
    """Test that recall skips facts with mismatched embedding models."""
    ns = MemoryNamespace(kind="workflow", id="test")
    # Store a fact with the current model
    await store.set(ns, "key1", "value1")

    # Manually insert a fact with a different model
    store.conn.execute(
        """
        INSERT INTO ts_memory_facts
        (namespace_kind, namespace_id, key, value_json, metadata_json,
         created_at_ms, expires_at_ms, embedding, embedding_model)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ns.kind,
            ns.id,
            "key2",
            '"value2"',
            None,
            int(time.time() * 1000),
            None,
            b"\x00" * 32,  # dummy embedding
            "different-model",
        ),
    )
    store.conn.commit()

    # Recall should only return key1
    results = await store.recall(ns, "query", top_k=10)
    keys = {r.key for r in results}
    assert "key1" in keys
    assert "key2" not in keys


@pytest.mark.asyncio
async def test_recall_without_adapter():
    """Test that recall raises RuntimeError when no adapter is configured."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    store = MemoryStore(db_path, embeddings=None)
    ns = MemoryNamespace(kind="workflow", id="test")
    with pytest.raises(RuntimeError, match="requires an embedding adapter"):
        await store.recall(ns, "query")
    Path(db_path).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_ttl_garbage_collector(store):
    """Test TtlGarbageCollector removes expired facts."""
    ns = MemoryNamespace(kind="workflow", id="test")
    await store.set(ns, "key1", "value1", ttl_ms=50)
    await store.set(ns, "key2", "value2")  # no TTL
    time.sleep(0.1)
    gc = TtlGarbageCollector()
    removed = await gc.process(store)
    assert removed == 1
    # key2 should still exist
    assert await store.get(ns, "key2") == "value2"
    # key1 should be gone
    assert await store.get(ns, "key1") is None


@pytest.mark.asyncio
async def test_token_limiter(store):
    """Test TokenLimiter trims thread history."""
    for i in range(10):
        # Each message is ~20 chars -> ~5 tokens
        await store.save_message("t1", MemoryMessage(role="user", content=f"message number {i:02d}"))
    # Limit to ~15 tokens -> should keep ~3 messages
    limiter = TokenLimiter(max_tokens=15)
    removed = await limiter.process(store, "t1")
    assert removed > 0
    messages = await store.list_messages("t1")
    assert len(messages) <= 4  # approximate


@pytest.mark.asyncio
async def test_summarizer(store):
    """Test Summarizer compresses old messages."""

    async def mock_summarize(msgs):
        return f"Summary of {len(msgs)} messages"

    # Add 15 messages
    for i in range(15):
        await store.save_message("t1", MemoryMessage(role="user", content=f"msg{i}"))

    # Summarize: keep_recent=5, min_to_compress=5
    summarizer = Summarizer(mock_summarize, keep_recent=5, min_to_compress=5)
    summary = await summarizer.process(store, "t1")

    assert summary is not None
    assert "Summary of 10 messages" in summary

    # Thread should now have: 1 system message + 5 recent
    messages = await store.list_messages("t1")
    assert len(messages) == 6
    assert messages[0].role == "system"
    assert messages[0].content == "Summary of 10 messages"
    # Last 5 should be msg10-msg14
    assert messages[-1].content == "msg14"


@pytest.mark.asyncio
async def test_summarizer_no_compression_if_too_few(store):
    """Test Summarizer doesn't compress if too few messages."""

    async def mock_summarize(msgs):
        return "summary"

    # Add only 8 messages
    for i in range(8):
        await store.save_message("t1", MemoryMessage(role="user", content=f"msg{i}"))

    # keep_recent=5, min_to_compress=5 -> only 3 old messages, below threshold
    summarizer = Summarizer(mock_summarize, keep_recent=5, min_to_compress=5)
    summary = await summarizer.process(store, "t1")

    # Should return None (no compression)
    assert summary is None
    messages = await store.list_messages("t1")
    assert len(messages) == 8
