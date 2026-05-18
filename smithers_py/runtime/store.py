"""SQLite store for the TS-shape runtime.

Owns three tables that the v1.0.0 schema doesn't already cover:

- ``ts_runs`` — one row per top-level workflow invocation, with status,
  workflow name, input payload, and terminal output.
- ``ts_output_rows`` — one row per node that emits output. The canonical
  shape mirrors what TS Smithers writes: ``(run_id, node_id, iteration,
  schema_version, payload)``. ``payload`` is JSON-encoded so any nested
  shape is allowed regardless of column type, sidestepping the float→
  INTEGER trap discovered during the understudy spike.
- ``ts_approvals`` — one row per ApprovalGate or HumanTask request. The
  shape is independent from the v1.0.0 ``approvals`` table so the two
  runtimes don't fight over schema migrations, but the column names are
  compatible enough that a future merge is straightforward.

All writes go through this class so the table definitions live in one
place and migrations are idempotent.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional


_SCHEMA = """
CREATE TABLE IF NOT EXISTS ts_runs (
    run_id TEXT PRIMARY KEY,
    workflow_name TEXT NOT NULL,
    status TEXT NOT NULL,
    input_json TEXT NOT NULL,
    output_json TEXT,
    error_json TEXT,
    started_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    finished_at REAL,
    parent_run_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_ts_runs_status ON ts_runs(status);
CREATE INDEX IF NOT EXISTS idx_ts_runs_parent ON ts_runs(parent_run_id);

CREATE TABLE IF NOT EXISTS ts_output_rows (
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    iteration INTEGER NOT NULL DEFAULT 0,
    schema_version TEXT,
    output_name TEXT,
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (run_id, node_id, iteration)
);

CREATE INDEX IF NOT EXISTS idx_ts_output_rows_schema ON ts_output_rows(schema_version);

CREATE TABLE IF NOT EXISTS ts_approvals (
    approval_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    metadata_json TEXT,
    output_name TEXT,
    on_deny TEXT NOT NULL DEFAULT 'fail',
    status TEXT NOT NULL DEFAULT 'pending',
    note TEXT,
    decided_by TEXT,
    created_at REAL NOT NULL,
    resolved_at REAL,
    UNIQUE (run_id, node_id)
);

CREATE INDEX IF NOT EXISTS idx_ts_approvals_run ON ts_approvals(run_id);
CREATE INDEX IF NOT EXISTS idx_ts_approvals_status ON ts_approvals(status);

CREATE TABLE IF NOT EXISTS ts_signals (
    signal_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    event TEXT NOT NULL,
    correlation_id TEXT,
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    source TEXT NOT NULL DEFAULT 'inline'
);

CREATE INDEX IF NOT EXISTS idx_ts_signals_run ON ts_signals(run_id);
CREATE INDEX IF NOT EXISTS idx_ts_signals_event ON ts_signals(run_id, event, correlation_id);
"""


@dataclass
class RunRow:
    run_id: str
    workflow_name: str
    status: str
    input: Dict[str, Any]
    output: Optional[Dict[str, Any]]
    error: Optional[Dict[str, Any]]
    started_at: float
    updated_at: float
    finished_at: Optional[float]
    parent_run_id: Optional[str]


@dataclass
class OutputRow:
    run_id: str
    node_id: str
    iteration: int
    schema_version: Optional[str]
    output_name: Optional[str]
    payload: Dict[str, Any]
    created_at: float


@dataclass
class SignalRow:
    signal_id: str
    run_id: str
    event: str
    correlation_id: Optional[str]
    payload: Dict[str, Any]
    created_at: float
    source: str


@dataclass
class ApprovalRow:
    approval_id: str
    run_id: str
    node_id: str
    kind: str  # 'approval_gate' | 'human_task'
    title: str
    summary: str
    metadata: Dict[str, Any]
    output_name: Optional[str]
    on_deny: str
    status: str  # 'pending' | 'approved' | 'denied'
    note: Optional[str]
    decided_by: Optional[str]
    created_at: float
    resolved_at: Optional[float]


class Store:
    """SQLite store. Thin wrapper around a connection.

    Construction is cheap; pass the same Store object around within a run.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    # ----- Connection management ---------------------------------------------

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, isolation_level=None)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self.ensure_schema()
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def ensure_schema(self) -> None:
        assert self._conn is not None
        self._conn.executescript(_SCHEMA)

    @contextmanager
    def cursor(self) -> Iterator[sqlite3.Cursor]:
        conn = self.connect()
        cur = conn.cursor()
        try:
            yield cur
        finally:
            cur.close()

    # ----- Runs --------------------------------------------------------------

    def create_run(
        self,
        run_id: str,
        workflow_name: str,
        input_payload: Dict[str, Any],
        parent_run_id: Optional[str] = None,
    ) -> None:
        now = time.time()
        with self.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ts_runs
                    (run_id, workflow_name, status, input_json,
                     started_at, updated_at, parent_run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    workflow_name,
                    "running",
                    json.dumps(input_payload, default=_json_default),
                    now,
                    now,
                    parent_run_id,
                ),
            )

    def get_run(self, run_id: str) -> Optional[RunRow]:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM ts_runs WHERE run_id = ?", (run_id,))
            row = cur.fetchone()
            if row is None:
                return None
            return RunRow(
                run_id=row["run_id"],
                workflow_name=row["workflow_name"],
                status=row["status"],
                input=json.loads(row["input_json"]),
                output=json.loads(row["output_json"]) if row["output_json"] else None,
                error=json.loads(row["error_json"]) if row["error_json"] else None,
                started_at=row["started_at"],
                updated_at=row["updated_at"],
                finished_at=row["finished_at"],
                parent_run_id=row["parent_run_id"],
            )

    def update_run_status(
        self,
        run_id: str,
        status: str,
        *,
        output: Optional[Dict[str, Any]] = None,
        error: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = time.time()
        finished_at = now if status in ("completed", "failed", "cancelled") else None
        with self.cursor() as cur:
            cur.execute(
                """
                UPDATE ts_runs
                   SET status = ?,
                       output_json = COALESCE(?, output_json),
                       error_json = COALESCE(?, error_json),
                       updated_at = ?,
                       finished_at = COALESCE(?, finished_at)
                 WHERE run_id = ?
                """,
                (
                    status,
                    json.dumps(output, default=_json_default) if output is not None else None,
                    json.dumps(error, default=_json_default) if error is not None else None,
                    now,
                    finished_at,
                    run_id,
                ),
            )

    def list_runs(self, *, status: Optional[str] = None, limit: int = 50) -> List[RunRow]:
        with self.cursor() as cur:
            if status:
                cur.execute(
                    "SELECT * FROM ts_runs WHERE status = ? "
                    "ORDER BY started_at DESC LIMIT ?",
                    (status, limit),
                )
            else:
                cur.execute(
                    "SELECT * FROM ts_runs ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                )
            return [
                RunRow(
                    run_id=r["run_id"],
                    workflow_name=r["workflow_name"],
                    status=r["status"],
                    input=json.loads(r["input_json"]),
                    output=json.loads(r["output_json"]) if r["output_json"] else None,
                    error=json.loads(r["error_json"]) if r["error_json"] else None,
                    started_at=r["started_at"],
                    updated_at=r["updated_at"],
                    finished_at=r["finished_at"],
                    parent_run_id=r["parent_run_id"],
                )
                for r in cur.fetchall()
            ]

    # ----- Outputs -----------------------------------------------------------

    def insert_output_row(
        self,
        run_id: str,
        node_id: str,
        payload: Dict[str, Any],
        *,
        schema_version: Optional[str] = None,
        output_name: Optional[str] = None,
        iteration: int = 0,
    ) -> None:
        with self.cursor() as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO ts_output_rows
                    (run_id, node_id, iteration, schema_version,
                     output_name, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    node_id,
                    iteration,
                    schema_version,
                    output_name,
                    json.dumps(payload, default=_json_default),
                    time.time(),
                ),
            )

    def get_output_row(
        self,
        run_id: str,
        node_id: str,
        iteration: int = 0,
    ) -> Optional[OutputRow]:
        with self.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM ts_output_rows
                 WHERE run_id = ? AND node_id = ? AND iteration = ?
                """,
                (run_id, node_id, iteration),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return _row_to_output(row)

    def list_output_rows(self, run_id: str) -> List[OutputRow]:
        with self.cursor() as cur:
            cur.execute(
                "SELECT * FROM ts_output_rows WHERE run_id = ? "
                "ORDER BY created_at ASC",
                (run_id,),
            )
            return [_row_to_output(r) for r in cur.fetchall()]

    # ----- Approvals ---------------------------------------------------------

    def insert_approval(
        self,
        run_id: str,
        node_id: str,
        *,
        kind: str,
        title: str,
        summary: str,
        metadata: Dict[str, Any],
        output_name: Optional[str],
        on_deny: str,
    ) -> ApprovalRow:
        approval_id = f"appr-{uuid.uuid4().hex[:12]}"
        now = time.time()
        with self.cursor() as cur:
            try:
                cur.execute(
                    """
                    INSERT INTO ts_approvals
                        (approval_id, run_id, node_id, kind, title, summary,
                         metadata_json, output_name, on_deny, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (
                        approval_id,
                        run_id,
                        node_id,
                        kind,
                        title,
                        summary,
                        json.dumps(metadata, default=_json_default),
                        output_name,
                        on_deny,
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                # Existing pending approval for this (run, node) — return it.
                cur.execute(
                    "SELECT * FROM ts_approvals WHERE run_id = ? AND node_id = ?",
                    (run_id, node_id),
                )
                row = cur.fetchone()
                if row is None:
                    raise
                return _row_to_approval(row)
        with self.cursor() as cur:
            cur.execute(
                "SELECT * FROM ts_approvals WHERE approval_id = ?",
                (approval_id,),
            )
            row = cur.fetchone()
            assert row is not None
            return _row_to_approval(row)

    def get_approval(
        self,
        run_id: str,
        node_id: str,
    ) -> Optional[ApprovalRow]:
        with self.cursor() as cur:
            cur.execute(
                "SELECT * FROM ts_approvals WHERE run_id = ? AND node_id = ?",
                (run_id, node_id),
            )
            row = cur.fetchone()
            return _row_to_approval(row) if row else None

    def list_pending_approvals(self, run_id: str) -> List[ApprovalRow]:
        with self.cursor() as cur:
            cur.execute(
                "SELECT * FROM ts_approvals "
                "WHERE run_id = ? AND status = 'pending' "
                "ORDER BY created_at ASC",
                (run_id,),
            )
            return [_row_to_approval(r) for r in cur.fetchall()]

    # ----- Signals -----------------------------------------------------------

    def insert_signal(
        self,
        run_id: str,
        *,
        event: str,
        correlation_id: Optional[str],
        payload: Dict[str, Any],
        source: str = "inline",
    ) -> SignalRow:
        signal_id = f"sig-{uuid.uuid4().hex[:12]}"
        now = time.time()
        with self.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ts_signals
                    (signal_id, run_id, event, correlation_id, payload_json,
                     created_at, source)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    run_id,
                    event,
                    correlation_id,
                    json.dumps(payload, default=_json_default),
                    now,
                    source,
                ),
            )
        return SignalRow(
            signal_id=signal_id,
            run_id=run_id,
            event=event,
            correlation_id=correlation_id,
            payload=payload,
            created_at=now,
            source=source,
        )

    def find_signal(
        self,
        run_id: str,
        *,
        event: str,
        correlation_id: Optional[str] = None,
    ) -> Optional[SignalRow]:
        with self.cursor() as cur:
            if correlation_id is None:
                cur.execute(
                    """
                    SELECT * FROM ts_signals
                     WHERE run_id = ? AND event = ?
                     ORDER BY created_at ASC LIMIT 1
                    """,
                    (run_id, event),
                )
            else:
                cur.execute(
                    """
                    SELECT * FROM ts_signals
                     WHERE run_id = ? AND event = ? AND correlation_id = ?
                     ORDER BY created_at ASC LIMIT 1
                    """,
                    (run_id, event, correlation_id),
                )
            row = cur.fetchone()
            if row is None:
                return None
            return SignalRow(
                signal_id=row["signal_id"],
                run_id=row["run_id"],
                event=row["event"],
                correlation_id=row["correlation_id"],
                payload=json.loads(row["payload_json"]),
                created_at=row["created_at"],
                source=row["source"],
            )

    def resolve_approval(
        self,
        run_id: str,
        *,
        node_id: Optional[str] = None,
        decision: str,  # 'approved' | 'denied'
        note: Optional[str] = None,
        decided_by: Optional[str] = None,
    ) -> ApprovalRow:
        if decision not in ("approved", "denied"):
            raise ValueError(f"decision must be 'approved' or 'denied', got {decision!r}")
        with self.cursor() as cur:
            if node_id is None:
                cur.execute(
                    "SELECT * FROM ts_approvals "
                    "WHERE run_id = ? AND status = 'pending'",
                    (run_id,),
                )
                rows = cur.fetchall()
                if len(rows) != 1:
                    raise WorkflowApprovalError(
                        f"Expected exactly one pending approval for run {run_id!r}, "
                        f"got {len(rows)}; pass node_id= to disambiguate."
                    )
                node_id = rows[0]["node_id"]
            now = time.time()
            cur.execute(
                """
                UPDATE ts_approvals
                   SET status = ?, note = ?, decided_by = ?, resolved_at = ?
                 WHERE run_id = ? AND node_id = ? AND status = 'pending'
                """,
                (decision, note, decided_by, now, run_id, node_id),
            )
            if cur.rowcount == 0:
                raise WorkflowApprovalError(
                    f"No pending approval for run={run_id!r} node={node_id!r}"
                )
            cur.execute(
                "SELECT * FROM ts_approvals WHERE run_id = ? AND node_id = ?",
                (run_id, node_id),
            )
            row = cur.fetchone()
            assert row is not None
            return _row_to_approval(row)


class WorkflowApprovalError(Exception):
    pass


def _row_to_output(row: sqlite3.Row) -> OutputRow:
    return OutputRow(
        run_id=row["run_id"],
        node_id=row["node_id"],
        iteration=row["iteration"],
        schema_version=row["schema_version"],
        output_name=row["output_name"],
        payload=json.loads(row["payload_json"]),
        created_at=row["created_at"],
    )


def _row_to_approval(row: sqlite3.Row) -> ApprovalRow:
    return ApprovalRow(
        approval_id=row["approval_id"],
        run_id=row["run_id"],
        node_id=row["node_id"],
        kind=row["kind"],
        title=row["title"],
        summary=row["summary"] or "",
        metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
        output_name=row["output_name"],
        on_deny=row["on_deny"],
        status=row["status"],
        note=row["note"],
        decided_by=row["decided_by"],
        created_at=row["created_at"],
        resolved_at=row["resolved_at"],
    )


def _json_default(obj: Any) -> Any:
    """Fallback for JSON-encoding Pydantic models and other Smithers objects."""
    try:
        from pydantic import BaseModel
        if isinstance(obj, BaseModel):
            return obj.model_dump()
    except Exception:
        pass
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)
