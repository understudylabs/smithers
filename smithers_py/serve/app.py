"""FastAPI app factory and route handlers."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .auth import create_auth_dependency
from .events_stream import generate_events_stream


@dataclass
class ServeOptions:
    """Configuration for serve mode app."""

    db_path: str
    run_id: str
    auth_token: Optional[str] = None
    metrics: bool = True


class ApprovalRequest(BaseModel):
    """Request body for approve/deny endpoints."""

    iteration: Optional[int] = Field(default=0)
    note: Optional[str] = None
    decided_by: Optional[str] = None


class SignalRequest(BaseModel):
    """Request body for signal endpoint."""

    payload: Dict[str, Any] = Field(default_factory=dict)
    correlation_id: Optional[str] = None


class ErrorResponse(BaseModel):
    """Standard error envelope."""

    error: Dict[str, str]


def create_serve_app(opts: ServeOptions) -> FastAPI:
    """Create FastAPI app for single-workflow serve mode."""
    app = FastAPI(title="Smithers Serve", version="1.0.0")
    auth = create_auth_dependency(opts.auth_token)

    def get_db() -> sqlite3.Connection:
        conn = sqlite3.connect(opts.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @app.get("/health")
    async def health() -> Dict[str, bool]:
        """Liveness probe."""
        return {"ok": True}

    @app.get("/", dependencies=[Depends(auth)])
    async def get_run_status() -> Dict[str, Any]:
        """Get run status and node summary."""
        conn = get_db()
        try:
            run_row = conn.execute(
                """
                SELECT run_id, workflow_name, status, started_at, finished_at
                FROM ts_runs
                WHERE run_id = ?
                """,
                (opts.run_id,),
            ).fetchone()

            if not run_row:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": {
                            "code": "RUN_NOT_FOUND",
                            "message": f"run {opts.run_id} not found",
                        }
                    },
                )

            # Build node summary by querying events or output rows
            # For simplicity, we'll return a placeholder summary
            summary = {"finished": 0, "in-progress": 0, "pending": 0}

            return {
                "runId": run_row["run_id"],
                "workflowName": run_row["workflow_name"],
                "status": run_row["status"],
                "startedAtMs": int(run_row["started_at"] * 1000),
                "finishedAtMs": (
                    int(run_row["finished_at"] * 1000) if run_row["finished_at"] else None
                ),
                "summary": summary,
            }
        finally:
            conn.close()

    @app.get("/events", dependencies=[Depends(auth)])
    async def get_events(afterSeq: int = 0) -> StreamingResponse:
        """SSE stream of workflow lifecycle events."""
        return StreamingResponse(
            generate_events_stream(opts.db_path, opts.run_id, afterSeq),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/frames", dependencies=[Depends(auth)])
    async def get_frames(limit: int = 50, afterFrameNo: int = 0) -> Dict[str, Any]:
        """List committed frames."""
        conn = get_db()
        try:
            rows = conn.execute(
                """
                SELECT run_id, node_id, iteration, output_name, payload_json, created_at
                FROM ts_output_rows
                WHERE run_id = ? AND iteration > ?
                ORDER BY iteration ASC
                LIMIT ?
                """,
                (opts.run_id, afterFrameNo, limit),
            ).fetchall()

            frames = [
                {
                    "runId": r["run_id"],
                    "nodeId": r["node_id"],
                    "iteration": r["iteration"],
                    "outputName": r["output_name"],
                    "payload": json.loads(r["payload_json"]) if r["payload_json"] else None,
                    "createdAt": r["created_at"],
                }
                for r in rows
            ]

            return {"frames": frames}
        finally:
            conn.close()

    @app.post("/approve/{node_id}", dependencies=[Depends(auth)])
    async def approve_gate(node_id: str, req: ApprovalRequest = ApprovalRequest()) -> Dict[str, str]:
        """Approve a pending gate."""
        conn = get_db()
        try:
            # Check gate exists
            gate_row = conn.execute(
                """
                SELECT approval_id, status
                FROM ts_approvals
                WHERE run_id = ? AND node_id = ?
                """,
                (opts.run_id, node_id),
            ).fetchone()

            if not gate_row:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": {
                            "code": "NODE_NOT_FOUND",
                            "message": f"gate {node_id} not found",
                        }
                    },
                )

            if gate_row["status"] != "pending":
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": {
                            "code": "RUN_NOT_ACTIVE",
                            "message": f"gate {node_id} already resolved",
                        }
                    },
                )

            # Update approval
            conn.execute(
                """
                UPDATE ts_approvals
                SET status = 'approved', note = ?, decided_by = ?, resolved_at = ?
                WHERE approval_id = ?
                """,
                (req.note, req.decided_by, time.time(), gate_row["approval_id"]),
            )
            conn.commit()

            return {"runId": opts.run_id}
        finally:
            conn.close()

    @app.post("/deny/{node_id}", dependencies=[Depends(auth)])
    async def deny_gate(node_id: str, req: ApprovalRequest = ApprovalRequest()) -> Dict[str, str]:
        """Deny a pending gate."""
        conn = get_db()
        try:
            # Check gate exists
            gate_row = conn.execute(
                """
                SELECT approval_id, status
                FROM ts_approvals
                WHERE run_id = ? AND node_id = ?
                """,
                (opts.run_id, node_id),
            ).fetchone()

            if not gate_row:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": {
                            "code": "NODE_NOT_FOUND",
                            "message": f"gate {node_id} not found",
                        }
                    },
                )

            if gate_row["status"] != "pending":
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": {
                            "code": "RUN_NOT_ACTIVE",
                            "message": f"gate {node_id} already resolved",
                        }
                    },
                )

            # Update approval
            conn.execute(
                """
                UPDATE ts_approvals
                SET status = 'denied', note = ?, decided_by = ?, resolved_at = ?
                WHERE approval_id = ?
                """,
                (req.note, req.decided_by, time.time(), gate_row["approval_id"]),
            )
            conn.commit()

            return {"runId": opts.run_id}
        finally:
            conn.close()

    @app.post("/signal/{signal_name}", dependencies=[Depends(auth)])
    async def post_signal(signal_name: str, req: SignalRequest = SignalRequest()) -> Dict[str, str]:
        """Deliver a typed signal."""
        conn = get_db()
        try:
            signal_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO ts_signals (signal_id, run_id, event, correlation_id, payload_json, created_at, source)
                VALUES (?, ?, ?, ?, ?, ?, 'http')
                """,
                (
                    signal_id,
                    opts.run_id,
                    signal_name,
                    req.correlation_id,
                    json.dumps(req.payload),
                    time.time(),
                ),
            )
            conn.commit()

            return {"runId": opts.run_id, "signalId": signal_id}
        finally:
            conn.close()

    @app.post("/cancel", dependencies=[Depends(auth)])
    async def cancel_run() -> Dict[str, str]:
        """Cancel the run."""
        conn = get_db()
        try:
            conn.execute(
                """
                UPDATE ts_runs
                SET status = 'cancelled', finished_at = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (time.time(), time.time(), opts.run_id),
            )
            conn.commit()

            return {"runId": opts.run_id, "status": "cancelled"}
        finally:
            conn.close()

    @app.get("/metrics", dependencies=[Depends(auth)])
    async def get_metrics() -> str:
        """Prometheus exposition format (placeholder)."""
        if not opts.metrics:
            raise HTTPException(status_code=404, detail="metrics disabled")

        # Placeholder - would use prometheus_client in real implementation
        return "# TYPE smithers_serve_info gauge\nsmithers_serve_info 1\n"

    return app
