"""Tests for serve subsystem."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from smithers_py.serve import ServeOptions, create_serve_app


@pytest.fixture
def db_path():
    """Create temporary test database with schema."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".db", delete=False) as f:
        db_file = f.name

    conn = sqlite3.connect(db_file)

    # Create schema
    conn.executescript(
        """
        CREATE TABLE ts_runs (
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

        CREATE TABLE ts_approvals (
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

        CREATE TABLE ts_signals (
            signal_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            event TEXT NOT NULL,
            correlation_id TEXT,
            payload_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            source TEXT NOT NULL DEFAULT 'inline'
        );

        CREATE TABLE ts_output_rows (
            run_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            iteration INTEGER NOT NULL DEFAULT 0,
            schema_version TEXT,
            output_name TEXT,
            payload_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (run_id, node_id, iteration)
        );

        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            execution_id TEXT NOT NULL,
            source TEXT NOT NULL,
            node_id TEXT,
            event_type TEXT NOT NULL,
            payload TEXT,
            timestamp TEXT DEFAULT (datetime('now'))
        );
        """
    )

    # Insert test data
    now = time.time()
    conn.execute(
        "INSERT INTO ts_runs (run_id, workflow_name, status, input_json, started_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("test-run-1", "test-workflow", "running", "{}", now, now),
    )
    conn.execute(
        "INSERT INTO ts_approvals (approval_id, run_id, node_id, kind, title, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("appr-1", "test-run-1", "gate-1", "manual", "Test Gate", "pending", now),
    )
    conn.commit()
    conn.close()

    yield db_file

    Path(db_file).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_health_no_auth(db_path):
    """Health endpoint should work without auth."""
    opts = ServeOptions(db_path=db_path, run_id="test-run-1", auth_token=None)
    app = create_serve_app(opts)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


@pytest.mark.asyncio
async def test_health_auth_bypassed(db_path):
    """Health endpoint should bypass auth even when token is set."""
    opts = ServeOptions(db_path=db_path, run_id="test-run-1", auth_token="secret")
    app = create_serve_app(opts)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


@pytest.mark.asyncio
async def test_get_run_status(db_path):
    """GET / should return run summary."""
    opts = ServeOptions(db_path=db_path, run_id="test-run-1", auth_token="secret")
    app = create_serve_app(opts)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/", headers={"Authorization": "Bearer secret"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["runId"] == "test-run-1"
        assert data["workflowName"] == "test-workflow"
        assert data["status"] == "running"
        assert "summary" in data


@pytest.mark.asyncio
async def test_unauthorized_missing_token(db_path):
    """Protected endpoints should return 401 on missing token."""
    opts = ServeOptions(db_path=db_path, run_id="test-run-1", auth_token="secret")
    app = create_serve_app(opts)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/")
        assert resp.status_code == 401
        data = resp.json()
        assert data["detail"]["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_unauthorized_wrong_token(db_path):
    """Protected endpoints should return 401 on wrong token."""
    opts = ServeOptions(db_path=db_path, run_id="test-run-1", auth_token="secret")
    app = create_serve_app(opts)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_auth_with_x_smithers_key(db_path):
    """Auth should accept x-smithers-key header."""
    opts = ServeOptions(db_path=db_path, run_id="test-run-1", auth_token="secret")
    app = create_serve_app(opts)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/", headers={"x-smithers-key": "secret"})
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_approve_gate_happy_path(db_path):
    """POST /approve/{node_id} should approve pending gate."""
    opts = ServeOptions(db_path=db_path, run_id="test-run-1", auth_token="secret")
    app = create_serve_app(opts)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/approve/gate-1",
            headers={"Authorization": "Bearer secret"},
            json={"note": "looks good", "decided_by": "alice"},
        )
        assert resp.status_code == 200
        assert resp.json()["runId"] == "test-run-1"

    # Verify approval was recorded
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT status, note, decided_by FROM ts_approvals WHERE node_id = ?", ("gate-1",)
    ).fetchone()
    assert row["status"] == "approved"
    assert row["note"] == "looks good"
    assert row["decided_by"] == "alice"
    conn.close()


@pytest.mark.asyncio
async def test_deny_gate_happy_path(db_path):
    """POST /deny/{node_id} should deny pending gate."""
    opts = ServeOptions(db_path=db_path, run_id="test-run-1", auth_token="secret")
    app = create_serve_app(opts)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/deny/gate-1",
            headers={"Authorization": "Bearer secret"},
            json={"note": "not ready"},
        )
        assert resp.status_code == 200

    # Verify denial was recorded
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT status, note FROM ts_approvals WHERE node_id = ?", ("gate-1",)
    ).fetchone()
    assert row["status"] == "denied"
    assert row["note"] == "not ready"
    conn.close()


@pytest.mark.asyncio
async def test_approve_unknown_gate(db_path):
    """Approving unknown gate should return 404 with NODE_NOT_FOUND."""
    opts = ServeOptions(db_path=db_path, run_id="test-run-1", auth_token="secret")
    app = create_serve_app(opts)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/approve/unknown",
            headers={"Authorization": "Bearer secret"},
            json={},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"]["code"] == "NODE_NOT_FOUND"


@pytest.mark.asyncio
async def test_post_signal(db_path):
    """POST /signal/{name} should create signal record."""
    opts = ServeOptions(db_path=db_path, run_id="test-run-1", auth_token="secret")
    app = create_serve_app(opts)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/signal/user.clicked",
            headers={"Authorization": "Bearer secret"},
            json={"payload": {"button": "submit"}, "correlation_id": "abc"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["runId"] == "test-run-1"
        assert "signalId" in data

    # Verify signal was recorded
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT event, payload_json, correlation_id, source FROM ts_signals WHERE run_id = ?",
        ("test-run-1",),
    ).fetchone()
    assert row["event"] == "user.clicked"
    assert json.loads(row["payload_json"]) == {"button": "submit"}
    assert row["correlation_id"] == "abc"
    assert row["source"] == "http"
    conn.close()


@pytest.mark.asyncio
async def test_cancel_run(db_path):
    """POST /cancel should mark run as cancelled."""
    opts = ServeOptions(db_path=db_path, run_id="test-run-1", auth_token="secret")
    app = create_serve_app(opts)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/cancel", headers={"Authorization": "Bearer secret"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    # Verify run was cancelled
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT status FROM ts_runs WHERE run_id = ?", ("test-run-1",)).fetchone()
    assert row["status"] == "cancelled"
    conn.close()


@pytest.mark.asyncio
async def test_error_envelope_shape(db_path):
    """All errors should use standard envelope format."""
    opts = ServeOptions(db_path=db_path, run_id="test-run-1", auth_token="secret")
    app = create_serve_app(opts)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Test 401
        resp = await client.get("/")
        assert resp.status_code == 401
        data = resp.json()
        assert "detail" in data
        assert "error" in data["detail"]
        assert "code" in data["detail"]["error"]
        assert "message" in data["detail"]["error"]

        # Test 404
        resp = await client.post(
            "/approve/nope", headers={"Authorization": "Bearer secret"}, json={}
        )
        assert resp.status_code == 404
        data = resp.json()
        assert data["detail"]["error"]["code"] == "NODE_NOT_FOUND"
