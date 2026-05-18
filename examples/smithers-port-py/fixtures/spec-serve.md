# `smithers_py.serve` — single-workflow HTTP server

Mirrors upstream Smithers' "serve mode" (`createServeApp` /
`smithers up --serve`). FastAPI-based HTTP app that runs alongside a
single workflow and exposes REST + SSE endpoints for run lifecycle,
approvals, signals, and metrics.

## Public surface

```python
from smithers_py.serve import (
    ServeOptions,
    create_serve_app,
)

opts = ServeOptions(
    db_path="smithers.db",
    run_id="abc123",
    auth_token="sk-secret",        # None disables auth
    metrics=True,                  # exposes /metrics
)
app = create_serve_app(opts)

# Standard ASGI app — uvicorn / hypercorn / etc.
import uvicorn
uvicorn.run(app, host="127.0.0.1", port=7331)
```

## Routes

| Method | Path | Purpose | Auth |
| --- | --- | --- | --- |
| GET | `/health` | Liveness probe. Returns `{"ok": true}`. | none |
| GET | `/` | Run status + node summary. | bearer |
| GET | `/events?afterSeq=N` | SSE stream of lifecycle events. | bearer |
| GET | `/frames?limit=50&afterFrameNo=N` | List committed frames. | bearer |
| POST | `/approve/{node_id}` | Approve a pending gate. | bearer |
| POST | `/deny/{node_id}` | Deny a pending gate. | bearer |
| POST | `/signal/{signal_name}` | Deliver a typed signal. | bearer |
| POST | `/cancel` | Cancel the run. | bearer |
| GET | `/metrics` | Prometheus exposition. | bearer |

## Auth

When `auth_token` is not None, every request except `/health` must
include either:

- `Authorization: Bearer <token>`, or
- `x-smithers-key: <token>`

Missing or wrong token returns `401` with body:

```json
{"error": {"code": "UNAUTHORIZED", "message": "invalid or missing token"}}
```

## Body schemas

### `GET /` response

```json
{
  "runId": "abc123",
  "workflowName": "review",
  "status": "running",
  "startedAtMs": 1707500000000,
  "finishedAtMs": null,
  "summary": {"finished": 3, "in-progress": 1, "pending": 2}
}
```

### `POST /approve/{node_id}` request

All fields optional:

```json
{
  "iteration": 0,
  "note": "looks good",
  "decided_by": "alice"
}
```

Returns `{"runId": "..."}` on success. Returns `404` with code
`NODE_NOT_FOUND` if the gate doesn't exist.

### `GET /events` SSE

Standard `text/event-stream`. Each event:

```
event: smithers
data: {"type": "NodeStarted", "runId": "...", "nodeId": "...", ...}
id: 42

```

Polls the events table every 500 ms. Auto-closes when the run reaches
a terminal state (`finished`, `failed`, `cancelled`). Sends a comment
keep-alive every 10 s.

Reconnect with `?afterSeq=N` to resume from a known position.

## Error envelope

All non-2xx responses use:

```json
{"error": {"code": "ERROR_CODE", "message": "Human description"}}
```

Common codes:

| Code | Status |
| --- | --- |
| `INVALID_REQUEST` | 400 |
| `UNAUTHORIZED` | 401 |
| `NOT_FOUND` | 404 |
| `RUN_NOT_FOUND` | 404 |
| `NODE_NOT_FOUND` | 404 |
| `RUN_NOT_ACTIVE` | 409 |
| `SERVER_ERROR` | 500 |

## DB tables read

- `ts_runs` — run status, workflow name, started/finished timestamps
- `ts_approvals` — pending approval gates (for `/approve`, `/deny`)
- `ts_signals` — write here for `/signal/{name}`

The serve module does NOT own these tables — they're owned by
`smithers_py.runtime.store`. The serve module just queries them.

## Implementation notes

- Use **FastAPI** for the app, **Pydantic** models for request/response
  bodies (auto-generates OpenAPI).
- SSE uses `fastapi.responses.StreamingResponse` with media type
  `text/event-stream`.
- Auth via a single `Depends(auth_dependency)` function that reads
  `Authorization` / `x-smithers-key` headers.
- Polling for SSE: 500 ms interval, `asyncio.sleep`, query
  `_smithers_events` (or `ts_events` — match what exists). Close on
  terminal run state.
- Metrics: use `prometheus-client` if available, otherwise return an
  empty `text/plain` body.

## Files to produce

- `__init__.py` — public exports (`ServeOptions`, `create_serve_app`)
- `app.py` — the FastAPI factory + all route handlers
- `auth.py` — bearer-token dependency
- `events_stream.py` — SSE polling generator
- `test_serve.py` — pytest-asyncio + `httpx.AsyncClient` integration tests
  (auth, health, status, approve/deny, error envelope)
