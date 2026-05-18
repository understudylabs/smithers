"""Single-workflow HTTP server for smithers_py.

Mirrors upstream Smithers' "serve mode" (createServeApp / smithers up --serve).
FastAPI-based HTTP app that runs alongside a single workflow and exposes REST +
SSE endpoints for run lifecycle, approvals, signals, and metrics.

```python
from smithers_py.serve import ServeOptions, create_serve_app

opts = ServeOptions(
    db_path="smithers.db",
    run_id="abc123",
    auth_token="sk-secret",  # None disables auth
    metrics=True,
)
app = create_serve_app(opts)

import uvicorn
uvicorn.run(app, host="127.0.0.1", port=7331)
```

Routes: /health, /, /events, /frames, /approve/{node_id}, /deny/{node_id},
/signal/{signal_name}, /cancel, /metrics.
"""

from __future__ import annotations

from .app import ServeOptions, create_serve_app

__all__ = [
    "ServeOptions",
    "create_serve_app",
]
