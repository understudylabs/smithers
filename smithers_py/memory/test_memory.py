"""Tests for the memory subsystem.

Covers:
- Working memory: set / get / list / delete + TTL expiry
- Message history: save / list (with limit) / thread retrieval
- Semantic recall: cosine similarity ordering with a deterministic
  embedding adapter (avoids network calls in CI)
- Processors: TtlGarbageCollector, TokenLimiter, Summarizer

The tests use a temporary SQLite file per test so they don't interfere
with each other or pollute the working directory.
"""

from __future__ import annotations

import os
import tempfile
import time

import pytest

from smithers_py.memory import (
    EmbeddingAdapter,
    MemoryMessage,
    MemoryNamespace,
    MemoryStore,
    NullEmbeddingAdapter,
    Summarizer,
    TokenLimiter,
    TtlGarbageCollector,
)


# ----- fixtures -------------------------------------------------------------


@pytest.fixture
def db_path():
    """One-off temp SQLite file per test."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        yield path
    finally:
        # Best-effort cleanup; ignore errors if the OS hasn't released the
        # file yet (Windows). The tempfile is deleted on process exit if
        # we miss it here.
        for suffix in ("", "-wal", "-shm"):
            candidate = path + suffix
            if os.path.exists(candidate):
                try:
                    os.unlink(candidate)
                except OSError:
                    pass


class _DeterministicEmbeddings:
    """Embedding adapter that maps each character to a position in a
    fixed-size vector. Lets tests assert deterministic recall ordering
    without making network calls.
    """

    def __init__(self, dimensions: int = 64) -> None:
        self._dim = dimensions

    @property
    def model(self) -> str:
        return f"deterministic-{self._dim}"

    @property
    def dimensions(self) -> int:
        return self._dim

    async def embed(self, texts):
        vectors = []
        for text in texts:
            v = [0.0] * self._dim
            for ch in text.lower():
                v[ord(ch) % self._dim] += 1.0
            # L2-normalize so cosine similarity is well-defined and
            # symmetric. Otherwise long texts swamp short ones.
            norm = sum(x * x for x in v) ** 0.5
            if norm > 0:
                v = [x / norm for x in v]
            vectors.append(v)
        return vectors


# ----- working memory -------------------------------------------------------


@pytest.mark.asyncio
async def test_set_and_get_simple_value(db_path):
    store = MemoryStore(db_path=db_path)
    ns = MemoryNamespace(kind="workflow", id="t")
    await store.set(ns, "k", {"foo": 1})
    assert await store.get(ns, "k") == {"foo": 1}


@pytest.mark.asyncio
async def test_get_missing_returns_none(db_path):
    store = MemoryStore(db_path=db_path)
    ns = MemoryNamespace(kind="workflow", id="t")
    assert await store.get(ns, "absent") is None


@pytest.mark.asyncio
async def test_last_write_wins(db_path):
    store = MemoryStore(db_path=db_path)
    ns = MemoryNamespace(kind="workflow", id="t")
    await store.set(ns, "k", "first")
    await store.set(ns, "k", "second")
    assert await store.get(ns, "k") == "second"


@pytest.mark.asyncio
async def test_namespaces_are_isolated(db_path):
    store = MemoryStore(db_path=db_path)
    workflow = MemoryNamespace(kind="workflow", id="t")
    agent = MemoryNamespace(kind="agent", id="t")
    await store.set(workflow, "k", "workflow-value")
    await store.set(agent, "k", "agent-value")
    assert await store.get(workflow, "k") == "workflow-value"
    assert await store.get(agent, "k") == "agent-value"


@pytest.mark.asyncio
async def test_list_namespace(db_path):
    store = MemoryStore(db_path=db_path)
    ns = MemoryNamespace(kind="global", id="default")
    await store.set(ns, "a", 1)
    await store.set(ns, "b", 2)
    await store.set(ns, "c", 3)
    facts = await store.list(ns)
    assert [f.key for f in facts] == ["a", "b", "c"]
    assert [f.value for f in facts] == [1, 2, 3]


@pytest.mark.asyncio
async def test_delete(db_path):
    store = MemoryStore(db_path=db_path)
    ns = MemoryNamespace(kind="user", id="u")
    await store.set(ns, "k", "value")
    assert await store.delete(ns, "k") is True
    assert await store.get(ns, "k") is None
    assert await store.delete(ns, "k") is False  # already gone


@pytest.mark.asyncio
async def test_ttl_expiry_hides_value(db_path):
    store = MemoryStore(db_path=db_path)
    ns = MemoryNamespace(kind="workflow", id="t")
    # TTL of 0ms means already expired before next millisecond tick.
    await store.set(ns, "k", "value", ttl_ms=0)
    # Sleep a moment to ensure the expiry timestamp is in the past.
    time.sleep(0.005)
    assert await store.get(ns, "k") is None
    # include_expired=True still surfaces the value.
    assert await store.get(ns, "k", include_expired=True) == "value"


# ----- message history ------------------------------------------------------


@pytest.mark.asyncio
async def test_save_and_list_messages(db_path):
    store = MemoryStore(db_path=db_path)
    seq0 = await store.save_message(
        "thread-1", MemoryMessage(role="user", content="hi")
    )
    seq1 = await store.save_message(
        "thread-1", MemoryMessage(role="assistant", content="hello")
    )
    assert seq0 == 0
    assert seq1 == 1
    msgs = await store.list_messages("thread-1")
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert [m.content for m in msgs] == ["hi", "hello"]


@pytest.mark.asyncio
async def test_list_messages_with_limit_returns_tail(db_path):
    store = MemoryStore(db_path=db_path)
    for i in range(5):
        await store.save_message(
            "thread-1", MemoryMessage(role="user", content=f"msg-{i}")
        )
    tail = await store.list_messages("thread-1", limit=2)
    assert [m.content for m in tail] == ["msg-3", "msg-4"]


@pytest.mark.asyncio
async def test_threads_are_isolated(db_path):
    store = MemoryStore(db_path=db_path)
    await store.save_message("a", MemoryMessage(role="user", content="in a"))
    await store.save_message("b", MemoryMessage(role="user", content="in b"))
    a = await store.list_messages("a")
    b = await store.list_messages("b")
    assert len(a) == 1 and a[0].content == "in a"
    assert len(b) == 1 and b[0].content == "in b"


# ----- semantic recall ------------------------------------------------------


@pytest.mark.asyncio
async def test_recall_orders_by_similarity(db_path):
    embeddings: EmbeddingAdapter = _DeterministicEmbeddings()
    store = MemoryStore(db_path=db_path, embeddings=embeddings)
    ns = MemoryNamespace(kind="workflow", id="t")
    await store.set(ns, "auth", "authentication bugs in login flow")
    await store.set(ns, "perf", "performance optimizations for queries")
    await store.set(ns, "ui", "color picker UI tweaks")
    results = await store.recall(ns, "auth bugs", top_k=2)
    keys = [f.key for f in results]
    # "auth" should score higher than "perf" or "ui" given the
    # deterministic character-based embedding.
    assert "auth" in keys
    assert keys[0] == "auth"


@pytest.mark.asyncio
async def test_recall_without_adapter_raises(db_path):
    store = MemoryStore(db_path=db_path)  # no embeddings
    ns = MemoryNamespace(kind="workflow", id="t")
    with pytest.raises(RuntimeError, match="recall.* requires an embedding adapter"):
        await store.recall(ns, "anything")


@pytest.mark.asyncio
async def test_recall_skips_facts_with_mismatched_model(db_path):
    store_a = MemoryStore(db_path=db_path, embeddings=_DeterministicEmbeddings(8))
    ns = MemoryNamespace(kind="workflow", id="t")
    await store_a.set(ns, "a", "alpha")

    # Reopen with a different embedding model — the stored fact's model
    # tag won't match, so recall skips it.
    store_b = MemoryStore(db_path=db_path, embeddings=_DeterministicEmbeddings(16))
    results = await store_b.recall(ns, "alpha")
    assert results == []


@pytest.mark.asyncio
async def test_null_adapter_returns_zero_vectors(db_path):
    adapter = NullEmbeddingAdapter(dimensions=4)
    store = MemoryStore(db_path=db_path, embeddings=adapter)
    ns = MemoryNamespace(kind="global", id="default")
    await store.set(ns, "k", "anything")
    # All embeddings are zero so similarity is 0; recall still returns
    # the row (cosine_similarity returns 0.0 not NaN).
    results = await store.recall(ns, "anything", top_k=1)
    assert len(results) == 1


# ----- processors -----------------------------------------------------------


@pytest.mark.asyncio
async def test_ttl_garbage_collector(db_path):
    store = MemoryStore(db_path=db_path)
    ns = MemoryNamespace(kind="workflow", id="t")
    await store.set(ns, "expired", "x", ttl_ms=0)
    await store.set(ns, "live", "y", ttl_ms=60_000)
    time.sleep(0.005)
    removed = await TtlGarbageCollector().process(store)
    assert removed == 1
    assert await store.get(ns, "expired", include_expired=True) is None
    assert await store.get(ns, "live") == "y"


@pytest.mark.asyncio
async def test_token_limiter_trims_oldest_messages(db_path):
    store = MemoryStore(db_path=db_path)
    # Five messages of ~10 tokens each (40 chars / 4 chars per token).
    long = "x" * 40
    for i in range(5):
        await store.save_message(
            "thread", MemoryMessage(role="user", content=f"{long}-{i}")
        )
    limiter = TokenLimiter(max_tokens=20)
    dropped = await limiter.process(store, "thread")
    remaining = await store.list_messages("thread")
    assert dropped >= 1
    assert len(remaining) <= 4
    # The most recent message is always preserved.
    assert remaining[-1].content.endswith("-4")


@pytest.mark.asyncio
async def test_summarizer_compresses_oldest_block(db_path):
    store = MemoryStore(db_path=db_path)
    for i in range(8):
        await store.save_message(
            "thread", MemoryMessage(role="user", content=f"old-{i}")
        )

    async def fake_summarize(messages):
        return f"(compressed {len(messages)} messages)"

    summarizer = Summarizer(
        fake_summarize,
        keep_recent=3,
        min_to_compress=2,
    )
    compressed = await summarizer.process(store, "thread")
    assert compressed == 5  # 8 - 3 kept
    msgs = await store.list_messages("thread")
    # 1 summary + 3 tail = 4
    assert len(msgs) == 4
    assert msgs[0].role == "system"
    assert msgs[0].content == "(compressed 5 messages)"
    assert msgs[-1].content == "old-7"


@pytest.mark.asyncio
async def test_summarizer_skips_short_threads(db_path):
    store = MemoryStore(db_path=db_path)
    await store.save_message(
        "thread", MemoryMessage(role="user", content="only one")
    )

    async def boom(_):  # pragma: no cover — should not be called
        raise AssertionError("summarize should not be invoked")

    summarizer = Summarizer(boom, keep_recent=5, min_to_compress=3)
    result = await summarizer.process(store, "thread")
    assert result is None
