"""Tool definition factory and execution logging.

Provides define_tool for creating custom tools, ToolCallLog for persistence,
and invoke_tool for wrapped execution with automatic logging.
"""

from __future__ import annotations

import inspect
import json
import sqlite3
import time
import warnings
from dataclasses import dataclass
from typing import Any, Optional

from .types import Tool, ToolCallRecord, ToolContext, ToolExecuteFn

# SQL schema for tool call log
_TOOL_CALL_LOG_SCHEMA = """
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
"""


@dataclass
class _DefinedTool:
    """Internal implementation of Tool protocol via define_tool."""

    name: str
    description: str
    side_effect: bool
    idempotent: bool
    _execute_fn: ToolExecuteFn
    _takes_ctx: bool

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> Any:
        """Execute the wrapped function with or without ctx parameter."""
        if self._takes_ctx:
            return await self._execute_fn(args, ctx)
        else:
            return await self._execute_fn(args)


def define_tool(
    *,
    name: str,
    description: str,
    execute: ToolExecuteFn,
    side_effect: bool = False,
    idempotent: bool = True,
) -> Tool:
    """Factory for creating custom tools.

    Args:
        name: Unique tool identifier
        description: Human-readable description
        execute: Async function taking (args) or (args, ctx)
        side_effect: True if tool mutates external state (not just sandbox)
        idempotent: True if safe to retry with same args

    Returns:
        Tool satisfying the Tool protocol

    Warns:
        UserWarning: If side_effect=True, idempotent=False but execute
            doesn't accept ctx parameter (needed for idempotency_key)
    """
    # Detect signature
    sig = inspect.signature(execute)
    params = list(sig.parameters.values())

    # Check if ctx parameter exists
    takes_ctx = len(params) >= 2

    # Warn if non-idempotent side-effect tool doesn't take ctx
    if side_effect and not idempotent and not takes_ctx:
        warnings.warn(
            f"Tool '{name}' is marked side_effect=True, idempotent=False "
            f"but execute function doesn't accept ctx parameter. "
            f"Runtime needs ctx.idempotency_key to safely dedupe retries.",
            UserWarning,
            stacklevel=2,
        )

    return _DefinedTool(
        name=name,
        description=description,
        side_effect=side_effect,
        idempotent=idempotent,
        _execute_fn=execute,
        _takes_ctx=takes_ctx,
    )


class ToolCallLog:
    """Persisted log of tool invocations in ts_tool_calls table."""

    def __init__(self, db_path: str):
        """Initialize log with SQLite database.

        Args:
            db_path: Path to SQLite file (created if missing)
        """
        self.db_path = db_path
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create ts_tool_calls table if it doesn't exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(_TOOL_CALL_LOG_SCHEMA)
            conn.commit()

    def record(self, row: ToolCallRecord) -> None:
        """Persist a tool call record.

        Args:
            row: Completed ToolCallRecord with all fields populated
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO ts_tool_calls (
                    run_id, node_id, iteration, attempt, seq,
                    tool_name, input_json, output_json,
                    started_at_ms, finished_at_ms, status, error_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.run_id,
                    row.node_id,
                    row.iteration,
                    row.attempt,
                    row.seq,
                    row.tool_name,
                    row.input_json,
                    row.output_json,
                    row.started_at_ms,
                    row.finished_at_ms,
                    row.status,
                    row.error_json,
                ),
            )
            conn.commit()

    def list_for_run(
        self,
        run_id: str,
        *,
        node_id: Optional[str] = None,
        tool_name: Optional[str] = None,
    ) -> list[ToolCallRecord]:
        """Query tool calls for a run, optionally filtered.

        Args:
            run_id: Run identifier (required)
            node_id: Filter to specific node (optional)
            tool_name: Filter to specific tool (optional)

        Returns:
            List of ToolCallRecord ordered by (iteration, attempt, seq)
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            where_clauses = ["run_id = ?"]
            params: list[Any] = [run_id]

            if node_id is not None:
                where_clauses.append("node_id = ?")
                params.append(node_id)

            if tool_name is not None:
                where_clauses.append("tool_name = ?")
                params.append(tool_name)

            query = f"""
                SELECT * FROM ts_tool_calls
                WHERE {' AND '.join(where_clauses)}
                ORDER BY iteration, attempt, seq
            """

            cursor = conn.execute(query, params)
            rows = cursor.fetchall()

            return [
                ToolCallRecord(
                    run_id=row["run_id"],
                    node_id=row["node_id"],
                    iteration=row["iteration"],
                    attempt=row["attempt"],
                    seq=row["seq"],
                    tool_name=row["tool_name"],
                    input_json=row["input_json"],
                    output_json=row["output_json"],
                    started_at_ms=row["started_at_ms"],
                    finished_at_ms=row["finished_at_ms"],
                    status=row["status"],
                    error_json=row["error_json"],
                )
                for row in rows
            ]


async def invoke_tool(
    tool: Tool,
    args: dict[str, Any],
    ctx: ToolContext,
    *,
    log: Optional[ToolCallLog] = None,
    seq: int = 0,
) -> Any:
    """Execute tool with logging wrapper.

    Args:
        tool: Tool to execute
        args: Arguments dictionary
        ctx: Execution context
        log: Optional ToolCallLog for persistence
        seq: Sequence number within (run, node, iteration, attempt)

    Returns:
        Tool execution result

    Raises:
        Re-raises any exception from tool.execute after logging
    """
    started_at_ms = int(time.time() * 1000)
    output: Any = None
    error: Optional[Exception] = None
    status = "success"

    try:
        output = await tool.execute(args, ctx)
        return output
    except Exception as e:
        error = e
        status = "error"
        raise
    finally:
        finished_at_ms = int(time.time() * 1000)

        if log and ctx.run_id and ctx.node_id:
            record = ToolCallRecord(
                run_id=ctx.run_id,
                node_id=ctx.node_id,
                iteration=ctx.iteration,
                attempt=ctx.attempt,
                seq=seq,
                tool_name=tool.name,
                input_json=json.dumps(args),
                output_json=json.dumps(output) if output is not None else None,
                started_at_ms=started_at_ms,
                finished_at_ms=finished_at_ms,
                status=status,
                error_json=json.dumps({"message": str(error)}) if error else None,
            )
            log.record(record)
