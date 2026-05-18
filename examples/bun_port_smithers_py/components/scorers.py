"""Scorer bindings for bun-port-py.

Mirrors examples/bun-port-smithers/components/scorers.ts in spirit. The
v0.1 Python runtime doesn't yet route scorers through Task execution
the way TS Smithers does, so this module exposes a no-op
``standard_scorers`` helper for source-level compatibility with the
upstream pattern. The real scorer wiring lands in v0.2 alongside the
``AgentLike`` async dispatch lift.
"""

from __future__ import annotations

from typing import Any, List


def standard_scorers(repo: str = ".", *, sla_ms: int = 20 * 60_000) -> List[Any]:
    """Return the standard scorer bundle.

    v0.1: no-op (returns empty list). Reserved for v0.2 when the
    runtime supports per-task scorer hooks.
    """
    _ = (repo, sla_ms)
    return []


__all__ = ["standard_scorers"]
