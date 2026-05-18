"""Regenerate the wire-compat snapshot.

Run this when the wire_compat workflow definition changes and the row
shape genuinely should be different. The result lands at
``examples/wire_compat/snapshot.json`` and is the new contract.

Usage:
    cd /Users/luis/smithers/smithers_py
    uv run python -m examples.wire_compat.generate_snapshot

The test ``test_wire_compat_matches_snapshot`` will then assert that
fresh runs produce exactly this row set.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


def main() -> int:
    # Make examples/ resolvable as a package whether invoked as a
    # script or via uv run -m.
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from smithers_py import RunStatus, run_workflow
    from examples.wire_compat.snapshot_helpers import normalize_rows
    from examples.wire_compat.workflow import wire_compat_workflow

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        result = run_workflow(
            wire_compat_workflow,
            input={"workload": "snapshot", "branch": True, "iterations": 3},
            db_path=db_path,
        )
        if result.status != RunStatus.COMPLETED:
            print(f"snapshot run did not complete: {result.status}", file=sys.stderr)
            if result.error:
                print(json.dumps(result.error, indent=2), file=sys.stderr)
            return 1
        normalized = normalize_rows(result.output_rows)
    finally:
        for sfx in ("", "-wal", "-shm", "-journal"):
            try:
                os.unlink(db_path + sfx)
            except FileNotFoundError:
                pass

    snapshot_path = Path(__file__).parent / "snapshot.json"
    snapshot_path.write_text(json.dumps(normalized, indent=2, sort_keys=False) + "\n")
    print(f"wrote {len(normalized)} rows → {snapshot_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
