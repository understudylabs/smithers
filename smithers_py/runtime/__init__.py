"""Lightweight runtime for TS-shape smithers_py workflows.

This package walks ``WorkflowNode`` / ``SequenceNode`` / ``ParallelNode`` /
``TaskNode`` / ``SubflowNode`` / ``ApprovalGateNode`` / ``HumanTaskNode`` /
``WorktreeNode`` / ``MergeQueueNode`` trees built with ``create_smithers``,
persists outputs to SQLite, and supports pause/resume via the same
``approvals`` table the v1.0.0 tick loop uses.

It is intentionally independent from ``smithers_py.engine.tick_loop`` —
the v1.0.0 engine continues to handle ``PhaseNode``/``StepNode``/``Ralph``/
``Claude`` workflows unchanged. The runtime exposes a small surface:

    from smithers_py.runtime import run_workflow, approve_run, inspect_run

    result = run_workflow(my_workflow, input={"foo": "bar"}, db_path="x.db")
    if result.status == "paused":
        approve_run(result.run_id, db_path="x.db", note="lgtm")
        result = run_workflow(my_workflow, input=..., db_path="x.db",
                              run_id=result.run_id, resume=True)
"""

from .agents import AgentLike, AgentResult, AsyncAgentLike, DryAgent
from .runner import (
    NonRetryableError,
    RunResult,
    RunStatus,
    WorkflowError,
    approve_run,
    deny_run,
    inspect_run,
    list_runs,
    run_workflow,
)
from .store import Store

__all__ = [
    "AgentLike",
    "AgentResult",
    "AsyncAgentLike",
    "DryAgent",
    "NonRetryableError",
    "RunResult",
    "RunStatus",
    "WorkflowError",
    "Store",
    "approve_run",
    "deny_run",
    "inspect_run",
    "list_runs",
    "run_workflow",
]
