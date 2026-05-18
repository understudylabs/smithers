"""TS-shape workflow runner.

Walks a Workflow → Sequence/Parallel → Task/Subflow/ApprovalGate tree and
executes it against a SQLite-backed Store. Supports pause-on-approval,
resume by run id, and child runs for Subflow.

The runner is single-process and synchronous. ``ParallelNode`` runs its
children sequentially within a frame for the MVP; concurrency is the next
iteration. ``WorktreeNode`` and ``MergeQueueNode`` are honored
structurally (children execute under them) but the underlying VCS/queue
semantics are out of MVP scope.
"""

from __future__ import annotations

import os
import traceback
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, ValidationError

from ..nodes.ts_compat import (
    ApprovalGateNode,
    HumanTaskNode,
    MergeQueueNode,
    OutputRef,
    ParallelNode,
    SequenceNode,
    SubflowNode,
    TaskNode,
    WorkflowNode,
    WorktreeNode,
)
from .store import ApprovalRow, Store, WorkflowApprovalError


# ----- Public types -----------------------------------------------------------


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class RunResult:
    """Outcome of a ``run_workflow`` call.

    ``status`` is the terminal status reached. When ``PAUSED``, the run
    is waiting on at least one approval — ``pending_approvals`` lists
    them. Call ``approve_run`` (or ``deny_run``) and re-invoke
    ``run_workflow`` with ``resume=True`` to continue.
    """

    run_id: str
    workflow_name: str
    status: RunStatus
    output: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    pending_approvals: List[ApprovalRow] = field(default_factory=list)
    output_rows: List[Dict[str, Any]] = field(default_factory=list)


class WorkflowError(Exception):
    """Raised when a workflow fails for any reason other than approval denial."""

    def __init__(
        self,
        message: str,
        *,
        node_id: Optional[str] = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        super().__init__(message)
        self.node_id = node_id
        self.cause = cause


# ----- Walker state -----------------------------------------------------------


@dataclass
class _Ctx:
    """Context object passed to the user's workflow function.

    Mirrors the TS ``ctx`` shape minimally:
      - ``ctx.input`` is the validated input payload
      - ``ctx.output(node_id)`` returns the persisted output of a prior
        node (or None if not yet executed)
      - ``ctx.run_id`` is the current run id
    """

    input: Any
    run_id: str
    store: Store
    _output_cache: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def output(self, node_id: str) -> Optional[Dict[str, Any]]:
        if node_id in self._output_cache:
            return self._output_cache[node_id]
        row = self.store.get_output_row(self.run_id, node_id)
        if row is None:
            return None
        self._output_cache[node_id] = row.payload
        return row.payload


@dataclass
class _WalkResult:
    paused: bool = False
    pending_approvals: List[ApprovalRow] = field(default_factory=list)


# ----- Runner -----------------------------------------------------------------


def run_workflow(
    workflow_fn: Callable[[_Ctx], WorkflowNode],
    *,
    input: Optional[Dict[str, Any]] = None,
    db_path: str = "smithers.db",
    run_id: Optional[str] = None,
    resume: bool = False,
    parent_run_id: Optional[str] = None,
) -> RunResult:
    """Execute a TS-shape workflow function.

    ``workflow_fn`` is a callable that takes a context and returns a
    ``WorkflowNode``. Typically obtained from ``@config.workflow``.

    On first invocation, omit ``run_id`` to have one generated. To resume
    a paused run, pass the original ``run_id`` and ``resume=True``; the
    runner pulls the original input from the stored run row so callers
    don't have to repass it.
    """
    store = Store(db_path)
    store.connect()
    workflow_name = _workflow_name(workflow_fn)

    if run_id is None:
        run_id = _new_run_id(workflow_name)
    existing = store.get_run(run_id)
    if existing is None:
        if resume:
            raise WorkflowError(f"Cannot resume unknown run id {run_id!r}")
        if input is None:
            input = {}
        store.create_run(run_id, workflow_name, input, parent_run_id=parent_run_id)
    else:
        # Resuming: pull stored input unless caller explicitly overrides.
        if input is None:
            input = existing.input
        store.update_run_status(run_id, "running")

    config = getattr(workflow_fn, "_smithers_config", None)
    try:
        validated_input = _validate_input(input, config)
        ctx = _Ctx(input=validated_input, run_id=run_id, store=store)
        tree = workflow_fn(ctx)
        if not isinstance(tree, WorkflowNode):
            raise WorkflowError(
                f"Workflow {workflow_name!r} must return a WorkflowNode, "
                f"got {type(tree).__name__}"
            )
        walk_result = _walk(tree, ctx, parent_path="main")
    except WorkflowError as exc:
        error = {"message": str(exc), "node_id": exc.node_id}
        store.update_run_status(run_id, "failed", error=error)
        rows = [_output_row_dict(r) for r in store.list_output_rows(run_id)]
        return RunResult(
            run_id=run_id,
            workflow_name=workflow_name,
            status=RunStatus.FAILED,
            error=error,
            output_rows=rows,
        )
    except Exception as exc:
        error = {
            "message": str(exc),
            "type": type(exc).__name__,
            "traceback": traceback.format_exc(),
        }
        store.update_run_status(run_id, "failed", error=error)
        rows = [_output_row_dict(r) for r in store.list_output_rows(run_id)]
        return RunResult(
            run_id=run_id,
            workflow_name=workflow_name,
            status=RunStatus.FAILED,
            error=error,
            output_rows=rows,
        )

    if walk_result.paused:
        store.update_run_status(run_id, "paused")
        rows = [_output_row_dict(r) for r in store.list_output_rows(run_id)]
        return RunResult(
            run_id=run_id,
            workflow_name=workflow_name,
            status=RunStatus.PAUSED,
            pending_approvals=walk_result.pending_approvals,
            output_rows=rows,
        )

    # Completed. Terminal output is the row written under the workflow's
    # last task that bound to ``outputs.output`` if registered, else the
    # last output row written.
    terminal = _find_terminal_output(store, run_id, config)
    store.update_run_status(run_id, "completed", output=terminal)
    rows = [_output_row_dict(r) for r in store.list_output_rows(run_id)]
    return RunResult(
        run_id=run_id,
        workflow_name=workflow_name,
        status=RunStatus.COMPLETED,
        output=terminal,
        output_rows=rows,
    )


# ----- Walk -------------------------------------------------------------------


def _walk(node: Any, ctx: _Ctx, parent_path: str) -> _WalkResult:
    if isinstance(node, WorkflowNode):
        return _walk_children(node.children, ctx, parent_path)

    if isinstance(node, SequenceNode):
        return _walk_children(node.children, ctx, parent_path)

    if isinstance(node, ParallelNode):
        # MVP: still sequential within a frame. Mark via a path suffix so
        # node ids remain unique.
        return _walk_children(node.children, ctx, parent_path)

    if isinstance(node, (WorktreeNode, MergeQueueNode)):
        # Honor structurally; real VCS/queue semantics are a v0.2 concern.
        return _walk_children(node.children, ctx, parent_path)

    if isinstance(node, TaskNode):
        return _run_task(node, ctx, parent_path)

    if isinstance(node, SubflowNode):
        return _run_subflow(node, ctx, parent_path)

    if isinstance(node, ApprovalGateNode):
        return _run_approval_gate(node, ctx, parent_path)

    if isinstance(node, HumanTaskNode):
        return _run_human_task(node, ctx, parent_path)

    # Anything else (existing v1.0.0 nodes) is treated as a pass-through
    # container for the MVP — walk children if any. Engine integration
    # for the v1.0.0 nodes is a separate piece of work.
    children = getattr(node, "children", [])
    return _walk_children(children, ctx, parent_path)


def _walk_children(
    children: List[Any], ctx: _Ctx, parent_path: str
) -> _WalkResult:
    accumulated_pending: List[ApprovalRow] = []
    for child in children:
        result = _walk(child, ctx, parent_path)
        accumulated_pending.extend(result.pending_approvals)
        if result.paused:
            return _WalkResult(paused=True, pending_approvals=accumulated_pending)
    return _WalkResult(paused=False, pending_approvals=accumulated_pending)


def _run_task(node: TaskNode, ctx: _Ctx, parent_path: str) -> _WalkResult:
    node_id = _node_id(parent_path, node.id)

    # Resume: skip already-completed tasks.
    existing = ctx.store.get_output_row(ctx.run_id, node_id)
    if existing is not None:
        ctx._output_cache[node_id] = existing.payload
        # Also cache under the bare task id so downstream ctx.output(id) hits.
        ctx._output_cache[node.id] = existing.payload
        return _WalkResult()

    payload = _compute_task_payload(node)
    schema = _resolve_schema(node)
    validated = _validate_payload(payload, schema, node_id=node_id)
    schema_version = _extract_schema_version(validated)
    output_name = node.output_target.name if node.output_target else None

    ctx.store.insert_output_row(
        ctx.run_id,
        node_id,
        validated,
        schema_version=schema_version,
        output_name=output_name,
    )
    ctx._output_cache[node_id] = validated
    ctx._output_cache[node.id] = validated
    return _WalkResult()


def _compute_task_payload(node: TaskNode) -> Dict[str, Any]:
    if node.render is not None:
        result = node.render()
        return _to_dict(result)
    if node.agent is not None:
        generate = getattr(node.agent, "generate", None)
        if generate is None:
            raise WorkflowError(
                f"TaskNode {node.id!r}.agent has no .generate(prompt=...) method"
            )
        produced = generate(prompt=node.prompt or "")
        if isinstance(produced, dict) and "output" in produced:
            return _to_dict(produced["output"])
        return _to_dict(produced)
    if node.children:
        # Static literal-children payload — TS bun-port pattern for
        # deterministic "render this dict" tasks.
        raise WorkflowError(
            f"TaskNode {node.id!r}: static-children pattern not yet supported "
            "in the MVP runtime; use render=callable instead"
        )
    raise WorkflowError(
        f"TaskNode {node.id!r} has no agent, render, or children to compute"
    )


def _run_subflow(node: SubflowNode, ctx: _Ctx, parent_path: str) -> _WalkResult:
    node_id = _node_id(parent_path, node.id)

    # Subflow's child runs under its own run id so it has its own row set;
    # the parent caches the terminal output keyed by node_id.
    existing = ctx.store.get_output_row(ctx.run_id, node_id)
    if existing is not None:
        ctx._output_cache[node_id] = existing.payload
        ctx._output_cache[node.id] = existing.payload
        return _WalkResult()

    child_run_id = f"{ctx.run_id}::sub::{_safe_id(node.id)}"
    child_exists = ctx.store.get_run(child_run_id) is not None
    child_result = run_workflow(
        node.workflow,
        input=node.input,
        db_path=ctx.store.db_path,
        run_id=child_run_id,
        resume=child_exists,
        parent_run_id=ctx.run_id,
    )

    if child_result.status == RunStatus.PAUSED:
        return _WalkResult(
            paused=True,
            pending_approvals=child_result.pending_approvals,
        )

    if child_result.status == RunStatus.FAILED:
        raise WorkflowError(
            f"Subflow {node.id!r} failed: "
            f"{(child_result.error or {}).get('message', 'unknown error')}",
            node_id=node_id,
        )

    terminal = child_result.output or {}
    schema_version = terminal.get("schema_version") if isinstance(terminal, dict) else None
    output_name = node.output_target.name if node.output_target else None
    ctx.store.insert_output_row(
        ctx.run_id,
        node_id,
        terminal,
        schema_version=schema_version,
        output_name=output_name,
    )
    ctx._output_cache[node_id] = terminal
    ctx._output_cache[node.id] = terminal
    return _WalkResult()


def _run_approval_gate(
    node: ApprovalGateNode, ctx: _Ctx, parent_path: str
) -> _WalkResult:
    node_id = _node_id(parent_path, node.id)
    existing = ctx.store.get_approval(ctx.run_id, node_id)

    if existing is None:
        if not node.when:
            # Gate condition false → auto-pass, persist a synthetic
            # "auto-approved" output row.
            payload = {"approved": True, "auto": True, "node_id": node.id}
            ctx.store.insert_output_row(
                ctx.run_id,
                node_id,
                payload,
                schema_version="smithers-py-approval-v0",
                output_name=node.output_target.name if node.output_target else None,
            )
            ctx._output_cache[node_id] = payload
            ctx._output_cache[node.id] = payload
            return _WalkResult()

        # Gate fires — write a pending approval and pause.
        approval = ctx.store.insert_approval(
            ctx.run_id,
            node_id,
            kind="approval_gate",
            title=node.request.title,
            summary=node.request.summary,
            metadata=node.request.metadata,
            output_name=node.output_target.name if node.output_target else None,
            on_deny=node.on_deny,
        )
        return _WalkResult(paused=True, pending_approvals=[approval])

    if existing.status == "pending":
        return _WalkResult(paused=True, pending_approvals=[existing])

    # Resolved — write output row reflecting the decision and continue.
    approved = existing.status == "approved"
    if not approved and node.on_deny == "fail":
        raise WorkflowError(
            f"ApprovalGate {node.id!r} denied "
            f"(note={existing.note or ''!r}); workflow failed per on_deny='fail'",
            node_id=node_id,
        )
    payload = {
        "approved": approved,
        "note": existing.note or "",
        "decided_by": existing.decided_by or "",
        "node_id": node.id,
    }
    if ctx.store.get_output_row(ctx.run_id, node_id) is None:
        ctx.store.insert_output_row(
            ctx.run_id,
            node_id,
            payload,
            schema_version="smithers-py-approval-v0",
            output_name=node.output_target.name if node.output_target else None,
        )
    ctx._output_cache[node_id] = payload
    ctx._output_cache[node.id] = payload
    return _WalkResult()


def _run_human_task(
    node: HumanTaskNode, ctx: _Ctx, parent_path: str
) -> _WalkResult:
    node_id = _node_id(parent_path, node.id)
    existing = ctx.store.get_approval(ctx.run_id, node_id)
    if existing is None:
        prompt_summary = str(node.prompt)[:240]
        approval = ctx.store.insert_approval(
            ctx.run_id,
            node_id,
            kind="human_task",
            title=f"HumanTask {node.id}",
            summary=prompt_summary,
            metadata={},
            output_name=node.output_target.name if node.output_target else None,
            on_deny="fail",
        )
        return _WalkResult(paused=True, pending_approvals=[approval])
    if existing.status == "pending":
        return _WalkResult(paused=True, pending_approvals=[existing])
    if existing.status == "denied":
        raise WorkflowError(
            f"HumanTask {node.id!r} denied (note={existing.note or ''!r})",
            node_id=node_id,
        )
    # Approved — for HumanTask, the responder's note is the structured
    # payload. Validate against output_schema if provided.
    payload: Dict[str, Any] = {"approved": True, "node_id": node.id}
    if existing.note:
        try:
            import json as _json
            payload.update(_json.loads(existing.note))
        except Exception:
            payload["note"] = existing.note
    schema = node.output_target.schema_ if node.output_target else node.output_schema
    if schema is not None:
        try:
            validated = schema.model_validate(payload)
            payload = validated.model_dump()
        except ValidationError as exc:
            raise WorkflowError(
                f"HumanTask {node.id!r} payload failed schema validation: {exc}",
                node_id=node_id,
            ) from exc
    if ctx.store.get_output_row(ctx.run_id, node_id) is None:
        schema_version = (
            payload.get("schema_version") if isinstance(payload, dict) else None
        )
        ctx.store.insert_output_row(
            ctx.run_id,
            node_id,
            payload,
            schema_version=schema_version,
            output_name=node.output_target.name if node.output_target else None,
        )
    ctx._output_cache[node_id] = payload
    ctx._output_cache[node.id] = payload
    return _WalkResult()


# ----- Approve / deny / inspect ----------------------------------------------


def approve_run(
    run_id: str,
    *,
    db_path: str = "smithers.db",
    node_id: Optional[str] = None,
    note: Optional[str] = None,
    decided_by: Optional[str] = None,
) -> ApprovalRow:
    """Resolve a pending approval as approved."""
    store = Store(db_path)
    store.connect()
    return store.resolve_approval(
        run_id,
        node_id=node_id,
        decision="approved",
        note=note,
        decided_by=decided_by,
    )


def deny_run(
    run_id: str,
    *,
    db_path: str = "smithers.db",
    node_id: Optional[str] = None,
    note: Optional[str] = None,
    decided_by: Optional[str] = None,
) -> ApprovalRow:
    """Resolve a pending approval as denied."""
    store = Store(db_path)
    store.connect()
    return store.resolve_approval(
        run_id,
        node_id=node_id,
        decision="denied",
        note=note,
        decided_by=decided_by,
    )


def inspect_run(run_id: str, *, db_path: str = "smithers.db") -> Dict[str, Any]:
    """Return the run row + output rows + pending approvals for diagnostics."""
    store = Store(db_path)
    store.connect()
    run = store.get_run(run_id)
    if run is None:
        raise WorkflowError(f"Run {run_id!r} not found")
    return {
        "run": run.__dict__,
        "output_rows": [_output_row_dict(r) for r in store.list_output_rows(run_id)],
        "pending_approvals": [
            a.__dict__ for a in store.list_pending_approvals(run_id)
        ],
    }


def list_runs(
    *, db_path: str = "smithers.db", status: Optional[str] = None, limit: int = 50
) -> List[Dict[str, Any]]:
    store = Store(db_path)
    store.connect()
    return [r.__dict__ for r in store.list_runs(status=status, limit=limit)]


# ----- Internals --------------------------------------------------------------


def _workflow_name(workflow_fn: Callable[..., Any]) -> str:
    return getattr(workflow_fn, "__name__", "anonymous_workflow")


def _new_run_id(workflow_name: str) -> str:
    return f"{_safe_id(workflow_name)}-{uuid.uuid4().hex[:8]}"


def _safe_id(text: str) -> str:
    out = []
    for ch in text:
        if ch.isalnum() or ch in ("-", "_", ".", ":"):
            out.append(ch)
        else:
            out.append("_")
    return "".join(out)[-64:] or "x"


def _node_id(parent_path: str, local_id: Optional[str]) -> str:
    return f"{parent_path}/{local_id or 'anon-' + uuid.uuid4().hex[:8]}"


def _resolve_schema(node: TaskNode):
    if node.output_target is not None:
        return node.output_target.schema_
    return node.output_schema


def _validate_input(
    payload: Any, config: Any
) -> Any:
    if config is None:
        return payload
    schema = config.schemas.get("input") if hasattr(config, "schemas") else None
    if schema is None:
        return payload
    try:
        if isinstance(payload, schema):
            return payload
        return schema.model_validate(payload)
    except ValidationError as exc:
        raise WorkflowError(f"Workflow input failed schema validation: {exc}") from exc


def _validate_payload(
    payload: Dict[str, Any],
    schema: Optional[type],
    *,
    node_id: str,
) -> Dict[str, Any]:
    if schema is None:
        return _to_dict(payload)
    try:
        if isinstance(payload, BaseModel):
            validated = schema.model_validate(payload.model_dump())
        else:
            validated = schema.model_validate(payload)
    except ValidationError as exc:
        raise WorkflowError(
            f"Task {node_id!r} output failed validation against "
            f"{schema.__name__}: {exc}",
            node_id=node_id,
        ) from exc
    return validated.model_dump(by_alias=True, exclude_none=False)


def _to_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    return {"value": value}


def _extract_schema_version(payload: Dict[str, Any]) -> Optional[str]:
    if isinstance(payload, dict):
        sv = payload.get("schema_version")
        if isinstance(sv, str):
            return sv
    return None


def _find_terminal_output(
    store: Store, run_id: str, config: Any
) -> Optional[Dict[str, Any]]:
    """Pick the terminal output for a run.

    Preference order:
      1. Output row whose ``output_name == 'output'`` (TS convention).
      2. Last-written output row.
    """
    rows = store.list_output_rows(run_id)
    if not rows:
        return None
    for r in reversed(rows):
        if r.output_name == "output":
            return r.payload
    return rows[-1].payload


def _output_row_dict(row) -> Dict[str, Any]:
    return {
        "run_id": row.run_id,
        "node_id": row.node_id,
        "iteration": row.iteration,
        "schema_version": row.schema_version,
        "output_name": row.output_name,
        "payload": row.payload,
    }
