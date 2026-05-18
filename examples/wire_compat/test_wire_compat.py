"""Wire-compat regression test.

Runs the canonical wire-compat workflow against an ephemeral SQLite DB,
normalizes the resulting ``ts_output_rows`` table, and asserts that
the row set matches the committed snapshot at
``examples/wire_compat/snapshot.json``.

This is the v0.1 parity acceptance check on the Python side. A future
TS-side dump of the equivalent workflow (using upstream
``smithers-orchestrator``) goes through the same normalize → JSON
pipeline; differences in the resulting snapshots are exactly the
cross-runtime parity bugs we care about.

To regenerate the snapshot after an intentional change to the wire-
compat workflow, run:

    uv run python /Users/luis/smithers/examples/wire_compat/generate_snapshot.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

from smithers_py import RunStatus, run_workflow

# Make the examples package importable regardless of how pytest was invoked.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from examples.wire_compat.snapshot_helpers import diff_rows, normalize_rows
from examples.wire_compat.workflow import wire_compat_workflow


SNAPSHOT_PATH = Path(__file__).parent / "snapshot.json"


@pytest.fixture
def db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    for sfx in ("", "-wal", "-shm", "-journal"):
        try:
            os.unlink(path + sfx)
        except FileNotFoundError:
            pass


def test_wire_compat_snapshot_matches(db_path: str) -> None:
    assert SNAPSHOT_PATH.exists(), (
        f"snapshot file missing: {SNAPSHOT_PATH}. "
        "Regenerate with examples/wire_compat/generate_snapshot.py"
    )
    expected = json.loads(SNAPSHOT_PATH.read_text())

    result = run_workflow(
        wire_compat_workflow,
        input={"workload": "snapshot", "branch": True, "iterations": 3},
        db_path=db_path,
    )
    assert result.status == RunStatus.COMPLETED, (
        f"wire-compat run did not complete: {result.status} "
        f"error={result.error}"
    )
    actual = normalize_rows(result.output_rows)

    diffs = diff_rows(expected, actual)
    if diffs:
        msg = "\n".join(diffs)
        raise AssertionError(
            "wire-compat snapshot drift:\n"
            + msg
            + "\n\nIf this drift is intentional, regenerate via:\n"
            + "  uv run python "
            + str(SNAPSHOT_PATH.parent / "generate_snapshot.py")
        )


def test_branch_false_path_alters_snapshot(db_path: str) -> None:
    """Quick reverse-check: flipping the branch input changes the row set.

    Confirms our diff_rows helper actually catches divergences. The
    flipped workflow should differ from the snapshot on the branch
    step's node_id and the final payload's `branch_step` field.
    """
    expected = json.loads(SNAPSHOT_PATH.read_text())
    result = run_workflow(
        wire_compat_workflow,
        input={"workload": "snapshot", "branch": False, "iterations": 3},
        db_path=db_path,
    )
    assert result.status == RunStatus.COMPLETED
    actual = normalize_rows(result.output_rows)
    diffs = diff_rows(expected, actual)
    assert diffs, "diff_rows should detect the branch flip"
    # Expect drift on the branch and final rows.
    drift_text = "\n".join(diffs)
    assert "branch" in drift_text or "final" in drift_text
