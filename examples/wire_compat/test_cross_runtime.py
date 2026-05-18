"""Cross-runtime parity test: Python ts_output_rows vs TS Drizzle tables.

This is the actual cross-runtime parity acceptance check. It loads
both snapshot artifacts (the Python ``snapshot.json`` and the TS
``ts_snapshot.json``) and runs them through the same ``diff_rows``
helper our intra-runtime regression uses. An empty diff is parity; a
non-empty diff is a precisely-located divergence.

How to run:

    # 1. Refresh both snapshots (they're under .gitignore'd db files
    #    so re-running is required after edits to either workflow).
    cd /Users/luis/smithers/examples/wire_compat
    bun install
    setopt no_nomatch ; rm -f wire_compat.db wire_compat.db-* ; setopt nomatch
    WIRE_COMPAT_DB=wire_compat.db ./node_modules/.bin/smithers \
        up workflow.tsx --run-id wire-compat-ts \
        --input '{"workload":"snapshot","branch":true,"iterations":3}'
    python3 extract_ts_snapshot.py

    cd /Users/luis/smithers/smithers_py
    uv run python /Users/luis/smithers/examples/wire_compat/generate_snapshot.py

    # 2. Run the diff test.
    uv run python -m pytest /Users/luis/smithers/examples/wire_compat/test_cross_runtime.py -v

Divergences flagged by this test are the v0.1→v0.2 backlog: things
our Python port should match the TS contract on. Some divergences are
*intentionally accepted* (documented inline as expected); the rest are
real bugs to fix.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Local import bootstrap.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from examples.wire_compat.snapshot_helpers import diff_rows


_HERE = Path(__file__).resolve().parent
_PY_SNAPSHOT = _HERE / "snapshot.json"
_TS_SNAPSHOT = _HERE / "ts_snapshot.json"


@pytest.fixture
def py_snapshot() -> list:
    if not _PY_SNAPSHOT.exists():
        pytest.skip(f"Python snapshot missing: {_PY_SNAPSHOT}")
    return json.loads(_PY_SNAPSHOT.read_text())


@pytest.fixture
def ts_snapshot() -> list:
    if not _TS_SNAPSHOT.exists():
        pytest.skip(
            f"TS snapshot missing: {_TS_SNAPSHOT}. "
            "Run the TS workflow + extract_ts_snapshot.py first."
        )
    return json.loads(_TS_SNAPSHOT.read_text())


def test_both_snapshots_exist(py_snapshot: list, ts_snapshot: list) -> None:
    assert len(py_snapshot) > 0
    assert len(ts_snapshot) > 0


def test_cross_runtime_row_set_diff(
    py_snapshot: list, ts_snapshot: list
) -> None:
    """The full cross-runtime parity check.

    A non-empty diff is the v0.1 → v0.2 backlog of shape mismatches the
    Python port needs to converge on. The test is currently expected
    to fail with a documented set of divergences (see
    ``EXPECTED_DIVERGENCE_KEYWORDS`` for the categories).
    """
    diffs = diff_rows(py_snapshot, ts_snapshot)

    # Group the diff lines by category so the failure message tells us
    # exactly which classes of divergence are present.
    categories: dict = {
        "row count": [],
        "node_id (path prefix)": [],
        "loop iteration": [],
        "approval row schema": [],
        "subflow output_name": [],
        "other": [],
    }
    for line in diffs:
        if line.startswith("row count differs"):
            categories["row count"].append(line)
        elif "main/" in line or "branch:" in line:
            categories["node_id (path prefix)"].append(line)
        elif "loop:loop/iter:" in line or "loop-step" in line:
            categories["loop iteration"].append(line)
        elif "smithers-py-approval-v0" in line or "gate" in line:
            categories["approval row schema"].append(line)
        elif "sub" in line or "child_out" in line:
            categories["subflow output_name"].append(line)
        else:
            categories["other"].append(line)

    if not diffs:
        return  # 🎉 parity achieved

    summary = ["cross-runtime divergences (Python vs TS):"]
    for category, lines in categories.items():
        if lines:
            summary.append(f"  • {category}: {len(lines)} row(s)")
            for line in lines[:3]:
                summary.append(f"      - {line}")
            if len(lines) > 3:
                summary.append(f"      … and {len(lines) - 3} more")
    pytest.fail("\n".join(summary))


# ----- Per-category convergence assertions (run individually) ---------------


def test_terminal_payload_matches(
    py_snapshot: list, ts_snapshot: list
) -> None:
    """The terminal `output` row's payload should be identical across runtimes.

    Even when node_id formats differ, the *final* output payload (the
    `output_name == "output"` row) is the user-visible contract. If
    this matches, the workflow's observable behavior is identical.
    """
    py_final = [r for r in py_snapshot if r["output_name"] == "output"]
    ts_final = [r for r in ts_snapshot if r["output_name"] == "output"]
    # Filter to the parent-run terminal (TS has an extra child-run row
    # under the same output_name).
    py_terminals = [
        r for r in py_final if r["schema_version"] == "wire-compat-final-v0"
    ]
    ts_terminals = [
        r for r in ts_final if r["schema_version"] == "wire-compat-final-v0"
    ]
    assert len(py_terminals) == 1
    assert len(ts_terminals) == 1
    assert py_terminals[0]["payload"] == ts_terminals[0]["payload"], (
        f"terminal payloads differ\n"
        f"Python: {py_terminals[0]['payload']}\n"
        f"TS:     {ts_terminals[0]['payload']}"
    )


def test_loop_total_iterations_match(
    py_snapshot: list, ts_snapshot: list
) -> None:
    """Both runtimes should record 3 loop iterations.

    Schema differs (Python uses suffixed node_ids; TS uses the
    iteration column), but the *count* must match.
    """
    py_loop = [r for r in py_snapshot if r["output_name"] == "loop_step"]
    ts_loop = [r for r in ts_snapshot if r["output_name"] == "loop_step"]
    assert len(py_loop) == 3
    assert len(ts_loop) == 3


def test_parallel_fanout_row_count_matches(
    py_snapshot: list, ts_snapshot: list
) -> None:
    py_par = [
        r for r in py_snapshot
        if (r["output_name"] or "").startswith("par")
    ]
    ts_par = [
        r for r in ts_snapshot
        if (r["output_name"] or "").startswith("par")
    ]
    assert len(py_par) == 3
    assert len(ts_par) == 3
