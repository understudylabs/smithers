"""Tests for the cache subsystem."""

from __future__ import annotations

import os
import tempfile
import time
from dataclasses import dataclass

import pytest
from pydantic import BaseModel

from smithers_py.cache import (
    Cache,
    CachePolicy,
    compute_cache_key,
    compute_schema_signature,
)


# ----- fixtures -------------------------------------------------------------


@pytest.fixture
def cache_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        yield path
    finally:
        for suffix in ("", "-wal", "-shm"):
            cand = path + suffix
            if os.path.exists(cand):
                try:
                    os.unlink(cand)
                except OSError:
                    pass


@dataclass
class FakeCtx:
    """Minimal stand-in for the workflow ctx that ``cache.by`` receives."""

    repo: str
    sha: str


# ----- key derivation -------------------------------------------------------


def test_same_inputs_same_key():
    policy = CachePolicy(by=lambda c: {"repo": c.repo}, version="v1")
    ctx = FakeCtx(repo="acme/app", sha="abc")
    k1 = compute_cache_key(policy, ctx=ctx, schema_signature="schema")
    k2 = compute_cache_key(policy, ctx=ctx, schema_signature="schema")
    assert k1 == k2


def test_different_by_payload_different_key():
    policy = CachePolicy(by=lambda c: {"repo": c.repo}, version="v1")
    k1 = compute_cache_key(policy, ctx=FakeCtx(repo="a", sha=""))
    k2 = compute_cache_key(policy, ctx=FakeCtx(repo="b", sha=""))
    assert k1 != k2


def test_different_version_different_key():
    policy_v1 = CachePolicy(by=lambda c: {"r": c.repo}, version="v1")
    policy_v2 = CachePolicy(by=lambda c: {"r": c.repo}, version="v2")
    ctx = FakeCtx(repo="a", sha="")
    assert (
        compute_cache_key(policy_v1, ctx=ctx)
        != compute_cache_key(policy_v2, ctx=ctx)
    )


def test_different_schema_signature_different_key():
    policy = CachePolicy(by=lambda c: {"r": c.repo}, version="v1")
    ctx = FakeCtx(repo="a", sha="")
    assert (
        compute_cache_key(policy, ctx=ctx, schema_signature="s1")
        != compute_cache_key(policy, ctx=ctx, schema_signature="s2")
    )


def test_key_includes_scope_prefix():
    policy_run = CachePolicy(version="v1", scope="run")
    policy_wf = CachePolicy(version="v1", scope="workflow")
    policy_gl = CachePolicy(version="v1", scope="global")
    k_run = compute_cache_key(policy_run, scope_id="r1")
    k_wf = compute_cache_key(policy_wf, scope_id="wf1")
    k_gl = compute_cache_key(policy_gl, scope_id="global")
    assert k_run.startswith("run:r1:")
    assert k_wf.startswith("workflow:wf1:")
    assert k_gl.startswith("global:global:")


def test_key_sorts_dict_keys():
    """Equivalent dicts with different insertion order must hash the same."""
    p1 = CachePolicy(by=lambda c: {"a": 1, "b": 2}, version="v")
    p2 = CachePolicy(by=lambda c: {"b": 2, "a": 1}, version="v")
    assert compute_cache_key(p1) == compute_cache_key(p2)


# ----- get / set ------------------------------------------------------------


def test_set_then_get(cache_path):
    cache = Cache(db_path=cache_path)
    cache.set("k1", {"value": 42})
    hit = cache.get("k1")
    assert hit is not None
    assert hit.value == {"value": 42}


def test_get_missing_returns_none(cache_path):
    cache = Cache(db_path=cache_path)
    assert cache.get("nope") is None


def test_last_write_wins(cache_path):
    cache = Cache(db_path=cache_path)
    cache.set("k", "first")
    cache.set("k", "second")
    hit = cache.get("k")
    assert hit is not None
    assert hit.value == "second"


def test_ttl_expiry(cache_path):
    cache = Cache(db_path=cache_path)
    cache.set("k", "v", ttl_ms=0)  # already expired
    time.sleep(0.005)
    assert cache.get("k") is None


def test_delete(cache_path):
    cache = Cache(db_path=cache_path)
    cache.set("k", "v")
    assert cache.delete("k") is True
    assert cache.get("k") is None
    assert cache.delete("k") is False


# ----- purge / sweep --------------------------------------------------------


def test_purge_scope_removes_only_matching(cache_path):
    cache = Cache(db_path=cache_path)
    cache.set("run:r1:abc", "in run 1")
    cache.set("run:r2:abc", "in run 2")
    cache.set("workflow:wf:abc", "in workflow")
    removed = cache.purge_scope("run", "r1")
    assert removed == 1
    assert cache.get("run:r1:abc") is None
    assert cache.get("run:r2:abc") is not None
    assert cache.get("workflow:wf:abc") is not None


def test_sweep_expired(cache_path):
    cache = Cache(db_path=cache_path)
    cache.set("expired", "v", ttl_ms=0)
    cache.set("live", "v", ttl_ms=60_000)
    time.sleep(0.005)
    removed = cache.sweep_expired()
    assert removed == 1
    assert cache.get("live") is not None


# ----- end-to-end task-cache flow ------------------------------------------


def test_end_to_end_memoization(cache_path):
    """Walk through the canonical "memoize an expensive task" flow."""
    cache = Cache(db_path=cache_path)
    policy = CachePolicy(
        by=lambda ctx: {"repo": ctx.repo, "sha": ctx.sha},
        version="v1",
        scope="workflow",
    )
    ctx = FakeCtx(repo="acme/app", sha="abc")
    key = cache.compute_key(
        policy, ctx, schema_signature="schema-sig", scope_id="my-wf"
    )

    # Miss.
    assert cache.get(key) is None

    # Compute + store.
    cache.set(key, {"summary": "expensive result"}, ttl_ms=policy.ttl_ms)

    # Hit.
    hit = cache.get(key)
    assert hit is not None
    assert hit.value["summary"] == "expensive result"


# ----- schema signature -----------------------------------------------------


class Demo(BaseModel):
    name: str
    count: int


def test_schema_signature_stable_across_calls():
    a = compute_schema_signature(Demo)
    b = compute_schema_signature(Demo)
    assert a == b
    assert len(a) > 0


def test_schema_signature_different_for_different_schemas():
    class Other(BaseModel):
        name: str
        amount: float

    assert compute_schema_signature(Demo) != compute_schema_signature(Other)


def test_schema_signature_handles_none():
    assert compute_schema_signature(None) == ""


def test_schema_signature_falls_back_to_value_hash():
    sig = compute_schema_signature({"shape": "raw-dict"})
    assert len(sig) > 0
