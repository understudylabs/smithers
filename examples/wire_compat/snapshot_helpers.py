"""Helpers shared between the snapshot generator and the regression test.

The wire-compat snapshot is the durable artifact that defines what
"matching the TS row contract" means. To stay stable across runs we
normalize a few things:

- ``run_id`` is stripped — it's per-execution random.
- Timestamps (``created_at``, ``resolved_at``) are stripped.
- Rows are sorted by ``node_id`` for deterministic ordering across
  Python dict iteration whims.
- ``payload`` is left intact; that's the contract we want to enforce.
"""

from __future__ import annotations

from typing import Any, Dict, List


def normalize_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return rows sorted + stripped of per-run identifiers."""
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "node_id": r["node_id"],
                "schema_version": r["schema_version"],
                "output_name": r["output_name"],
                "iteration": r.get("iteration", 0),
                "payload": r["payload"],
            }
        )
    out.sort(key=lambda r: (r["node_id"], r["iteration"]))
    return out


def diff_rows(
    expected: List[Dict[str, Any]],
    actual: List[Dict[str, Any]],
) -> List[str]:
    """Return a list of human-readable differences between two row sets.

    Empty list means the row sets match. Differences are emitted in
    diagnostic form — which node id, which field, expected vs actual.
    """
    diffs: List[str] = []
    if len(expected) != len(actual):
        diffs.append(
            f"row count differs: expected {len(expected)}, got {len(actual)}"
        )
    exp_by_id = {(r["node_id"], r["iteration"]): r for r in expected}
    act_by_id = {(r["node_id"], r["iteration"]): r for r in actual}

    missing = sorted(set(exp_by_id) - set(act_by_id))
    extra = sorted(set(act_by_id) - set(exp_by_id))
    for k in missing:
        diffs.append(f"missing row in actual: node_id={k[0]} iter={k[1]}")
    for k in extra:
        diffs.append(f"extra row in actual: node_id={k[0]} iter={k[1]}")

    for k in sorted(set(exp_by_id) & set(act_by_id)):
        e = exp_by_id[k]
        a = act_by_id[k]
        for field in ("schema_version", "output_name", "payload"):
            if e[field] != a[field]:
                diffs.append(
                    f"{k[0]}[{k[1]}] {field}: expected {e[field]!r}, got {a[field]!r}"
                )
    return diffs
