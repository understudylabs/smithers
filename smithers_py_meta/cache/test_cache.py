"""Tests for cache subsystem."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest
from pydantic import BaseModel

from smithers_py_meta.cache import (
    Cache,
    CacheHit,
    CachePolicy,
    compute_cache_key,
    compute_schema_signature,
)


class SampleSchema(BaseModel):
    """Sample Pydantic schema for testing."""

    name: str
    version: int


class ModifiedSchema(BaseModel):
    """Modified schema to test signature changes."""

    name: str
    version: int
    extra_field: str = "default"


@pytest.fixture
def temp_cache():
    """Temporary cache for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        yield Cache(db_path=str(db_path))


def test_compute_schema_signature_stability():
    """Schema signature must be deterministic."""
    sig1 = compute_schema_signature(SampleSchema)
    sig2 = compute_schema_signature(SampleSchema)
    assert sig1 == sig2
    assert len(sig1) == 64  # SHA-256 hex


def test_compute_schema_signature_changes():
    """Different schemas produce different signatures."""
    sig1 = compute_schema_signature(SampleSchema)
    sig2 = compute_schema_signature(ModifiedSchema)
    assert sig1 != sig2


def test_compute_schema_signature_none():
    """None schema returns empty string."""
    sig = compute_schema_signature(None)
    assert sig == ""


def test_compute_schema_signature_raw_dict():
    """Raw dict schemas produce stable signatures."""
    schema1 = {"type": "object", "properties": {"x": {"type": "int"}}}
    schema2 = {"properties": {"x": {"type": "int"}}, "type": "object"}
    sig1 = compute_schema_signature(schema1)
    sig2 = compute_schema_signature(schema2)
    # Sorted keys ensure dict order doesn't matter
    assert sig1 == sig2


def test_cache_key_determinism():
    """Same inputs produce identical keys."""

    class MockCtx:
        pass

    ctx = MockCtx()
    ctx.repo = "smithers"
    ctx.version = "v1"

    policy = CachePolicy(
        by=lambda c: {"repo": c.repo, "version": c.version},
        version="v2",
        scope="workflow",
    )

    key1 = compute_cache_key(policy, ctx=ctx, schema_signature="abc123", scope_id="wf1")
    key2 = compute_cache_key(policy, ctx=ctx, schema_signature="abc123", scope_id="wf1")
    assert key1 == key2


def test_cache_key_different_by():
    """Different by(ctx) produces different keys."""

    class MockCtx:
        pass

    ctx1 = MockCtx()
    ctx1.repo = "smithers"
    ctx2 = MockCtx()
    ctx2.repo = "other"

    policy = CachePolicy(
        by=lambda c: {"repo": c.repo},
        version="v1",
        scope="workflow",
    )

    key1 = compute_cache_key(policy, ctx=ctx1)
    key2 = compute_cache_key(policy, ctx=ctx2)
    assert key1 != key2


def test_cache_key_different_version():
    """Different version produces different keys."""
    policy1 = CachePolicy(version="v1", scope="workflow")
    policy2 = CachePolicy(version="v2", scope="workflow")

    key1 = compute_cache_key(policy1)
    key2 = compute_cache_key(policy2)
    assert key1 != key2


def test_cache_key_different_schema():
    """Different schema signature produces different keys."""
    policy = CachePolicy(version="v1", scope="workflow")

    key1 = compute_cache_key(policy, schema_signature="sig1")
    key2 = compute_cache_key(policy, schema_signature="sig2")
    assert key1 != key2


def test_cache_key_different_scope():
    """Different scope produces different keys."""
    policy1 = CachePolicy(version="v1", scope="run")
    policy2 = CachePolicy(version="v1", scope="workflow")

    key1 = compute_cache_key(policy1)
    key2 = compute_cache_key(policy2)
    assert key1 != key2
    assert key1.startswith("run:")
    assert key2.startswith("workflow:")


def test_cache_key_scope_prefix():
    """Key includes scope and scope_id prefix."""
    policy = CachePolicy(version="v1", scope="workflow")
    key = compute_cache_key(policy, scope_id="my-wf")
    assert key.startswith("workflow:my-wf:")


def test_cache_key_default_scope_id():
    """Default scope_id is 'default'."""
    policy = CachePolicy(version="v1", scope="global")
    key = compute_cache_key(policy)
    assert key.startswith("global:default:")


def test_cache_key_sorted_dict_keys():
    """Dict keys are sorted for stability."""

    class MockCtx:
        pass

    ctx = MockCtx()

    # Same content, different insertion order
    policy1 = CachePolicy(
        by=lambda c: {"z": "last", "a": "first", "m": "middle"},
        version="v1",
    )
    policy2 = CachePolicy(
        by=lambda c: {"a": "first", "m": "middle", "z": "last"},
        version="v1",
    )

    key1 = compute_cache_key(policy1, ctx=ctx)
    key2 = compute_cache_key(policy2, ctx=ctx)
    assert key1 == key2


def test_cache_get_set(temp_cache):
    """Basic get/set operations."""
    key = "test:default:abc123"
    value = {"result": 42}

    # Initially missing
    assert temp_cache.get(key) is None

    # Set value
    temp_cache.set(key, value)

    # Retrieve
    hit = temp_cache.get(key)
    assert hit is not None
    assert hit.value == value
    assert hit.created_at_ms > 0
    assert hit.expires_at_ms is None


def test_cache_set_with_ttl(temp_cache):
    """TTL sets expiry timestamp."""
    key = "test:default:abc123"
    value = {"result": 42}
    ttl_ms = 5000

    temp_cache.set(key, value, ttl_ms=ttl_ms)
    hit = temp_cache.get(key)
    assert hit is not None
    assert hit.expires_at_ms is not None
    assert hit.expires_at_ms > hit.created_at_ms


def test_cache_ttl_expiry(temp_cache):
    """Expired entries return None on get."""
    key = "test:default:abc123"
    value = {"result": 42}
    ttl_ms = 50  # 50ms

    temp_cache.set(key, value, ttl_ms=ttl_ms)

    # Should be available immediately
    hit = temp_cache.get(key)
    assert hit is not None

    # Wait for expiry
    time.sleep(0.1)  # 100ms

    # Should return None now
    hit = temp_cache.get(key)
    assert hit is None


def test_cache_delete(temp_cache):
    """Delete removes entry."""
    key = "test:default:abc123"
    value = {"result": 42}

    temp_cache.set(key, value)
    assert temp_cache.get(key) is not None

    # Delete
    deleted = temp_cache.delete(key)
    assert deleted is True
    assert temp_cache.get(key) is None

    # Delete non-existent
    deleted = temp_cache.delete(key)
    assert deleted is False


def test_cache_purge_scope(temp_cache):
    """Purge removes all entries in scope."""
    # Set entries in different scopes
    temp_cache.set("workflow:wf1:key1", {"v": 1})
    temp_cache.set("workflow:wf1:key2", {"v": 2})
    temp_cache.set("workflow:wf2:key3", {"v": 3})
    temp_cache.set("run:r1:key4", {"v": 4})

    # Purge workflow:wf1
    count = temp_cache.purge_scope("workflow", "wf1")
    assert count == 2

    # Verify removals
    assert temp_cache.get("workflow:wf1:key1") is None
    assert temp_cache.get("workflow:wf1:key2") is None
    assert temp_cache.get("workflow:wf2:key3") is not None
    assert temp_cache.get("run:r1:key4") is not None


def test_cache_sweep_expired(temp_cache):
    """Sweep removes expired entries."""
    now_ms = int(time.time() * 1000)

    # Set entries with different expiry
    temp_cache.set("key1", {"v": 1}, ttl_ms=1000)  # Expires in 1s
    temp_cache.set("key2", {"v": 2}, ttl_ms=10000)  # Expires in 10s
    temp_cache.set("key3", {"v": 3})  # No expiry

    # Sweep with future timestamp
    future_ms = now_ms + 2000  # 2s in future
    count = temp_cache.sweep_expired(now_ms=future_ms)
    assert count == 1  # Only key1 expired

    # Verify
    assert temp_cache.get("key1") is None
    assert temp_cache.get("key2") is not None
    assert temp_cache.get("key3") is not None


def test_cache_compute_key_method(temp_cache):
    """Cache.compute_key convenience method."""

    class MockCtx:
        pass

    ctx = MockCtx()
    ctx.repo = "smithers"

    policy = CachePolicy(
        by=lambda c: {"repo": c.repo},
        version="v1",
        scope="workflow",
    )

    key = temp_cache.compute_key(policy, ctx, schema_signature="sig1", scope_id="wf1")
    assert key.startswith("workflow:wf1:")


def test_end_to_end_memoization(temp_cache):
    """Complete memoization scenario."""

    class TaskInput(BaseModel):
        repo: str
        branch: str

    class TaskOutput(BaseModel):
        analysis: str
        score: int

    class MockCtx:
        def __init__(self, repo: str, branch: str):
            self.input = TaskInput(repo=repo, branch=branch)

    # Policy
    policy = CachePolicy(
        by=lambda ctx: {"repo": ctx.input.repo, "branch": ctx.input.branch},
        version="v1",
        scope="workflow",
        ttl_ms=60_000,
    )

    ctx = MockCtx("smithers", "main")
    schema_sig = compute_schema_signature(TaskOutput)

    # First run - cache miss
    key = temp_cache.compute_key(policy, ctx, schema_signature=schema_sig, scope_id="wf1")
    hit = temp_cache.get(key)
    assert hit is None

    # Compute and cache
    result = TaskOutput(analysis="Looks good", score=85)
    temp_cache.set(key, result.model_dump(), ttl_ms=policy.ttl_ms, schema_signature=schema_sig)

    # Second run - cache hit
    hit = temp_cache.get(key)
    assert hit is not None
    assert hit.value["analysis"] == "Looks good"
    assert hit.value["score"] == 85

    # Different input - cache miss
    ctx2 = MockCtx("smithers", "feature")
    key2 = temp_cache.compute_key(policy, ctx2, schema_signature=schema_sig, scope_id="wf1")
    hit2 = temp_cache.get(key2)
    assert hit2 is None
    assert key2 != key  # Different cache key


def test_schema_signature_invalidation(temp_cache):
    """Schema change produces different key, auto-invalidating cache."""

    class MockCtx:
        pass

    ctx = MockCtx()
    policy = CachePolicy(version="v1", scope="workflow")

    # Cache with SampleSchema
    sig1 = compute_schema_signature(SampleSchema)
    key1 = temp_cache.compute_key(policy, ctx, schema_signature=sig1)
    temp_cache.set(key1, {"result": "old"})

    # Change schema to ModifiedSchema
    sig2 = compute_schema_signature(ModifiedSchema)
    key2 = temp_cache.compute_key(policy, ctx, schema_signature=sig2)

    # Different key means cache miss
    assert key1 != key2
    assert temp_cache.get(key2) is None
