"""SSE event stream generator for workflow events."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from typing import AsyncIterator, Optional


async def generate_events_stream(
    db_path: str, run_id: str, after_seq: int = 0
) -> AsyncIterator[str]:
    """Poll events table and yield SSE-formatted chunks.

    Polls every 500ms. Closes when run reaches terminal state (finished, failed,
    cancelled). Sends keep-alive comment every 10s.
    """
    last_seq = after_seq
    last_keepalive = time.time()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        while True:
            # Check run status
            run_row = conn.execute(
                "SELECT status FROM ts_runs WHERE run_id = ?", (run_id,)
            ).fetchone()

            if run_row:
                status = run_row["status"]
                is_terminal = status in ("finished", "failed", "cancelled")
            else:
                is_terminal = True

            # Fetch new events
            rows = conn.execute(
                """
                SELECT id, execution_id, source, node_id, event_type, payload, timestamp
                FROM events
                WHERE execution_id = ? AND id > ?
                ORDER BY id ASC
                """,
                (run_id, last_seq),
            ).fetchall()

            for row in rows:
                last_seq = row["id"]
                payload_dict = json.loads(row["payload"]) if row["payload"] else {}
                event_data = {
                    "type": row["event_type"],
                    "runId": run_id,
                    "nodeId": row["node_id"],
                    "source": row["source"],
                    "timestamp": row["timestamp"],
                    **payload_dict,
                }
                yield f"event: smithers\n"
                yield f"data: {json.dumps(event_data)}\n"
                yield f"id: {last_seq}\n\n"

            # Send keep-alive comment every 10s
            now = time.time()
            if now - last_keepalive >= 10:
                yield ": keepalive\n\n"
                last_keepalive = now

            if is_terminal:
                break

            await asyncio.sleep(0.5)
    finally:
        conn.close()
