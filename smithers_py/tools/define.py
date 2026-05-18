"""``define_tool`` factory and the persisted tool-call log.

User-defined tools wrap an async ``execute`` function with the metadata
that the runtime needs: a name, description, side-effect/idempotency
flags, and an optional input schema.

The factory also emits warnings at construction time when a tool
declares ``side_effect=True, idempotent=False`` but doesn't accept the
context parameter — the agent loop has no way to deduplicate retries
without an idempotency key, which is almost always a bug.
"""

from __future__ import annotations

import inspect
import json
import sqlite3
import time
import warnings
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from .types import (
    Tool,
    ToolCallRecord,
    ToolContext,
    ToolExecuteFn,
)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS ts_tool_calls (
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    iteration INTEGER NOT NULL DEFAULT 0,
    attempt INTEGER NOT NULL DEFAULT 0,
    seq INTEGER NOT NULL,
    tool_name TEXT NOT NULL,
    input_json TEXT NOT NULL,
    output_json TEXT,
    started_at_ms INTEGER NOT NULL,
    finished_at_ms INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'success',
    error_json TEXT,
    PRIMARY KEY (run_id, node_id, iteration, attempt, seq)
);

CREATE INDEX IF NOT EXISTS idx_ts_tool_calls_run_node
    ON ts_tool_calls(run_id, node_id);
CREATE INDEX IF NOT EXISTS idx_ts_tool_calls_tool
    ON ts_tool_calls(tool_name);
"""


@dataclass
class _DefinedTool:
    """Concrete ``Tool`` produced by ``define_tool``. Internal type;
    user code interacts via the ``Tool`` protocol."""

    name: str
    description: str
    side_effect: bool
    idempotent: bool
    execute_fn: ToolExecuteFn

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> Any:
        sig = inspect.signature(self.execute_fn)
        params = list(sig.parameters.keys())
        if len(params) >= 2:
            return await self.execute_fn(args, ctx)
        # User defined `async def execute(args):` without a ctx
        # parameter — call without ctx, but they've already been
        # warned at definition time if this is risky.
        return await self.execute_fn(args)  # type: ignore[call-arg]


def define_tool(
    *,
    name: str,
    description: str,
    execute: ToolExecuteFn,
    side_effect: bool = False,
    idempotent: bool = True,
) -> Tool:
    """Build a ``Tool`` instance from an execute function plus metadata.

    ``side_effect=True, idempotent=False`` indicates the tool mutates
    external state in a way that's unsafe to replay. If the
    ``execute`` function doesn't accept a ``ctx`` parameter in that
    case, a warning is emitted at construction time — the function
    needs ``ctx.idempotency_key`` to dedupe on retry.

    Pure reads should default to ``side_effect=False, idempotent=True``
    (the defaults). Sandboxed FS operations (``write``, ``edit``,
    ``bash``) are not considered side-effects because they're inside
    the sandbox and trivially reversible with git.
    """
    if side_effect and not idempotent:
        sig = inspect.signature(execute)
        if len(sig.parameters) < 2:
            warnings.warn(
                f"Tool {name!r} declares side_effect=True, idempotent=False "
                f"but execute() doesn't accept the ctx parameter. "
                f"You need ctx.idempotency_key to deduplicate retries safely. "
                f"This is almost always a bug.",
                stacklevel=2,
            )

    return _DefinedTool(
        name=name,
        description=description,
        side_effect=side_effect,
        idempotent=idempotent,
        execute_fn=execute,
    )


# ----- tool-call log -------------------------------------------------------


class ToolCallLog:
    """Persisted log of every tool invocation.

    Initializes the ``ts_tool_calls`` table on first connect (idempotent
    CREATE TABLE IF NOT EXISTS). Per-call writes are committed
    individually since tool calls within a task aren't transactional.

    The log is used for debugging (`smithers logs <run-id> --type
    tool-call`), retry warnings (`see tools already called in attempt
    N`), and observability metrics.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._init_schema()

    def record(self, entry: ToolCallRecord) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO ts_tool_calls (
                    run_id, node_id, iteration, attempt, seq,
                    tool_name, input_json, output_json,
                    started_at_ms, finished_at_ms,
                    status, error_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.run_id,
                    entry.node_id,
                    entry.iteration,
                    entry.attempt,
                    entry.seq,
                    entry.tool_name,
                    entry.input_json,
                    entry.output_json,
                    entry.started_at_ms,
                    entry.finished_at_ms,
                    entry.status,
                    entry.error_json,
                ),
            )

    def list_for_run(
        self,
        run_id: str,
        *,
        node_id: Optional[str] = None,
        tool_name: Optional[str] = None,
    ) -> list[ToolCallRecord]:
        clauses = ["run_id = ?"]
        params: list[Any] = [run_id]
        if node_id is not None:
            clauses.append("node_id = ?")
            params.append(node_id)
        if tool_name is not None:
            clauses.append("tool_name = ?")
            params.append(tool_name)
        sql = (
            "SELECT run_id, node_id, iteration, attempt, seq, "
            "tool_name, input_json, output_json, started_at_ms, "
            "finished_at_ms, status, error_json "
            "FROM ts_tool_calls WHERE " + " AND ".join(clauses) +
            " ORDER BY started_at_ms ASC, seq ASC"
        )
        with self._connect() as db:
            rows = db.execute(sql, params).fetchall()
        return [
            ToolCallRecord(
                run_id=r[0],
                node_id=r[1],
                iteration=r[2],
                attempt=r[3],
                seq=r[4],
                tool_name=r[5],
                input_json=r[6],
                output_json=r[7],
                started_at_ms=r[8],
                finished_at_ms=r[9],
                status=r[10],
                error_json=r[11],
            )
            for r in rows
        ]

    def _init_schema(self) -> None:
        with self._connect() as db:
            db.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self._db_path, isolation_level=None, timeout=30.0)
        try:
            db.execute("PRAGMA journal_mode = WAL")
            db.execute("PRAGMA synchronous = NORMAL")
            yield db
        finally:
            db.close()


async def invoke_tool(
    tool: Tool,
    args: dict[str, Any],
    ctx: ToolContext,
    *,
    log: Optional[ToolCallLog] = None,
    seq: int = 0,
) -> Any:
    """Invoke a tool with logging. Records success / error rows.

    ``seq`` is the index of this tool call within the task attempt; the
    runtime increments it monotonically. ``log`` is optional — when
    omitted, the tool runs without persistence (useful for tests and
    one-off calls outside a workflow).
    """
    started_at = int(time.time() * 1000)
    input_json = json.dumps(args, default=str)

    try:
        output = await tool.execute(args, ctx)
        finished_at = int(time.time() * 1000)
        if log is not None and ctx.run_id and ctx.node_id:
            output_json: Optional[str]
            try:
                output_json = json.dumps(output, default=str)
            except (TypeError, ValueError):
                output_json = json.dumps(repr(output))
            log.record(
                ToolCallRecord(
                    run_id=ctx.run_id,
                    node_id=ctx.node_id,
                    iteration=ctx.iteration,
                    attempt=ctx.attempt,
                    seq=seq,
                    tool_name=tool.name,
                    input_json=input_json,
                    output_json=output_json,
                    started_at_ms=started_at,
                    finished_at_ms=finished_at,
                    status="success",
                )
            )
        return output
    except Exception as exc:
        finished_at = int(time.time() * 1000)
        error_json = json.dumps(
            {"type": type(exc).__name__, "message": str(exc)}
        )
        if log is not None and ctx.run_id and ctx.node_id:
            log.record(
                ToolCallRecord(
                    run_id=ctx.run_id,
                    node_id=ctx.node_id,
                    iteration=ctx.iteration,
                    attempt=ctx.attempt,
                    seq=seq,
                    tool_name=tool.name,
                    input_json=input_json,
                    output_json=None,
                    started_at_ms=started_at,
                    finished_at_ms=finished_at,
                    status="error",
                    error_json=error_json,
                )
            )
        raise


__all__ = ["ToolCallLog", "define_tool", "invoke_tool"]
