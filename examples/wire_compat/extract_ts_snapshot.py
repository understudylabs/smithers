"""Extract a normalized snapshot from a TS Smithers run's SQLite DB.

Upstream Smithers writes one table per registered ``createSmithers``
output key, with columns ``run_id``, ``node_id``, ``iteration`` and
then the schema fields flat. This script:

1. Lists every non-``_smithers_*`` table (those are runtime/infra
   tables, not output tables).
2. Reads each row, reconstructs the ``payload`` from the schema-shaped
   columns, and emits a ``(node_id, schema_version, output_name,
   iteration, payload)`` record.
3. Filters out the ``input`` table (input row, not an output row).
4. Applies the same ``normalize_rows`` we use on the Python side so
   the two snapshots are directly diffable.

Result lands at ``examples/wire_compat/ts_snapshot.json``. The
companion ``test_cross_runtime.py`` diffs it against
``examples/wire_compat/snapshot.json`` (the Python side).

Run:
    cd /Users/luis/smithers/smithers_py
    uv run python /Users/luis/smithers/examples/wire_compat/extract_ts_snapshot.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List


_HERE = Path(__file__).resolve().parent
_DB = _HERE / "wire_compat.db"
_OUT = _HERE / "ts_snapshot.json"

# Tables that aren't output rows. ``input`` is the workflow's stored input.
_NON_OUTPUT_TABLES = {"input"}
_INTERNAL_TABLE_PREFIX = "_smithers_"
# Drizzle-injected columns that aren't part of the row payload.
_NON_PAYLOAD_COLS = {"run_id", "node_id", "iteration"}


def _output_tables(cur: sqlite3.Cursor) -> List[str]:
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE ? "
        "AND name NOT IN ('sqlite_sequence') "
        "ORDER BY name",
        (_INTERNAL_TABLE_PREFIX + "%",),
    )
    names = [row[0] for row in cur.fetchall()]
    return [n for n in names if n not in _NON_OUTPUT_TABLES]


def _columns(cur: sqlite3.Cursor, table: str) -> List[str]:
    cur.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in cur.fetchall()]


def _row_to_payload(
    columns: List[str], row: sqlite3.Row
) -> Dict[str, Any]:
    """Reconstruct the payload dict from a TS output row.

    JSON-encoded columns (objects, arrays) are stored as TEXT in
    SQLite; ``json.loads`` them when the value looks like a JSON
    container so the snapshot matches the Python side's nested shape.
    """
    payload: Dict[str, Any] = {}
    for col in columns:
        if col in _NON_PAYLOAD_COLS:
            continue
        value = row[col]
        if value is None:
            continue
        if isinstance(value, str) and value and value[0] in "[{":
            try:
                payload[col] = json.loads(value)
                continue
            except (ValueError, TypeError):
                pass
        payload[col] = value
    return payload


def extract(
    db_path: Path,
    run_id: str = "wire-compat-ts",
) -> List[Dict[str, Any]]:
    """Extract output rows for a single run, matching what the Python
    snapshot generator does on its side (``store.list_output_rows(run_id)``).

    Subflow child rows live under their own run_id (``<parent>:child:<id>:0``)
    and are intentionally excluded here — the parent run's view is the
    contract we're diffing against. Cross-runtime parity at the
    parent-row level is the v0.1 acceptance bar; cross-runtime parity
    at the child-row level lands once we add a child-run snapshot pass
    in v0.2.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    out: List[Dict[str, Any]] = []
    try:
        cur = conn.cursor()
        for table in _output_tables(cur):
            cols = _columns(cur, table)
            if "run_id" in cols:
                cur.execute(f"SELECT * FROM {table} WHERE run_id = ?", (run_id,))
            else:
                cur.execute(f"SELECT * FROM {table}")
            for row in cur.fetchall():
                payload = _row_to_payload(cols, row)
                if not payload:
                    continue
                out.append(
                    {
                        "node_id": row["node_id"],
                        "schema_version": payload.get("schema_version"),
                        "output_name": table,
                        "iteration": row["iteration"],
                        "payload": payload,
                    }
                )
    finally:
        conn.close()
    out.sort(key=lambda r: (r["node_id"], r["iteration"]))
    return out


def main() -> int:
    if not _DB.exists():
        print(
            f"TS DB not found at {_DB}. Run the TS workflow first:\n"
            "  cd /Users/luis/smithers/examples/wire_compat\n"
            "  bun install\n"
            "  WIRE_COMPAT_DB=wire_compat.db ./node_modules/.bin/smithers "
            "up workflow.tsx "
            "--run-id wire-compat-ts "
            "--input '{\"workload\":\"snapshot\",\"branch\":true,\"iterations\":3}'",
            file=sys.stderr,
        )
        return 1

    rows = extract(_DB)
    _OUT.write_text(json.dumps(rows, indent=2) + "\n")
    print(f"wrote {len(rows)} TS rows → {_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
