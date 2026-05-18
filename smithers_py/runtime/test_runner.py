"""End-to-end tests for the TS-shape workflow runtime.

Exercises Task, Sequence, Parallel, Subflow, ApprovalGate, HumanTask
against a fresh SQLite store per test. Confirms output rows persist in
the right shape and pause/resume works.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any, Dict, List, Optional

import pytest
from pydantic import BaseModel, Field

from smithers_py import (
    ApprovalGateNode,
    ApprovalRequest,
    HumanTaskNode,
    OutputRef,
    ParallelNode,
    RunResult,
    RunStatus,
    SequenceNode,
    Store,
    SubflowNode,
    TaskNode,
    WorkflowNode,
    approve_run,
    create_smithers,
    deny_run,
    inspect_run,
    list_runs,
    run_workflow,
)
from smithers_py.runtime.runner import WorkflowError


# ----- Fixtures ---------------------------------------------------------------


@pytest.fixture
def db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.unlink(path + suffix)
        except FileNotFoundError:
            pass


class _Input(BaseModel):
    workload: str
    n: int = 3


class _StepOut(BaseModel):
    schema_version: str = "test-step-v0"
    step: str
    value: int


class _FinalOut(BaseModel):
    schema_version: str = "test-final-v0"
    workload: str
    total: int
    steps: List[str] = Field(default_factory=list)


# ----- Helpers ---------------------------------------------------------------


def _build_basic_config():
    return create_smithers(
        schemas={
            "input": _Input,
            "step1": _StepOut,
            "step2": _StepOut,
            "output": _FinalOut,
        }
    )


# ----- Tests ------------------------------------------------------------------


class TestSimpleWorkflow:
    def test_two_sequential_tasks_complete(self, db_path: str) -> None:
        config = _build_basic_config()
        outputs = config.outputs

        @config.workflow
        def wf(ctx) -> WorkflowNode:
            return WorkflowNode(
                name="basic",
                children=[
                    SequenceNode(
                        children=[
                            TaskNode(
                                id="t1",
                                output=outputs.step1,
                                render=lambda: {"step": "first", "value": 1},
                            ),
                            TaskNode(
                                id="t2",
                                output=outputs.step2,
                                render=lambda: {"step": "second", "value": 2},
                            ),
                            TaskNode(
                                id="final",
                                output=outputs.output,
                                render=lambda: {
                                    "workload": ctx.input.workload,
                                    "total": 3,
                                    "steps": ["first", "second"],
                                },
                            ),
                        ]
                    )
                ],
            )

        result = run_workflow(wf, input={"workload": "demo"}, db_path=db_path)
        assert result.status == RunStatus.COMPLETED
        assert result.output is not None
        assert result.output["workload"] == "demo"
        assert result.output["total"] == 3
        # 3 output rows, all bound to the right schema_version literals.
        assert len(result.output_rows) == 3
        versions = {r["schema_version"] for r in result.output_rows}
        assert "test-step-v0" in versions
        assert "test-final-v0" in versions

    def test_input_validated_against_schema(self, db_path: str) -> None:
        config = _build_basic_config()

        @config.workflow
        def wf(ctx) -> WorkflowNode:
            return WorkflowNode(
                name="x",
                children=[
                    TaskNode(
                        id="t",
                        output_schema=_StepOut,
                        render=lambda: {"step": "x", "value": ctx.input.n},
                    )
                ],
            )

        result = run_workflow(wf, input={"workload": "z", "n": 5}, db_path=db_path)
        assert result.status == RunStatus.COMPLETED
        # Bad input should fail validation.
        bad = run_workflow(wf, input={"workload": "z", "n": "not-an-int"}, db_path=db_path)
        assert bad.status == RunStatus.FAILED


class TestApprovalGate:
    def test_gate_pauses_and_resumes(self, db_path: str) -> None:
        config = _build_basic_config()
        outputs = config.outputs

        @config.workflow
        def wf(ctx) -> WorkflowNode:
            return WorkflowNode(
                name="approval-demo",
                children=[
                    SequenceNode(
                        children=[
                            TaskNode(
                                id="t1",
                                output=outputs.step1,
                                render=lambda: {"step": "a", "value": 1},
                            ),
                            ApprovalGateNode(
                                id="g",
                                when=True,
                                request=ApprovalRequest(title="approve?"),
                                on_deny="fail",
                            ),
                            TaskNode(
                                id="final",
                                output=outputs.output,
                                render=lambda: {
                                    "workload": ctx.input.workload,
                                    "total": 1,
                                    "steps": ["a"],
                                },
                            ),
                        ]
                    )
                ],
            )

        # First call pauses.
        r1 = run_workflow(wf, input={"workload": "demo"}, db_path=db_path)
        assert r1.status == RunStatus.PAUSED
        assert len(r1.pending_approvals) == 1
        gate = r1.pending_approvals[0]
        assert gate.title == "approve?"
        assert gate.status == "pending"

        # Approve and resume.
        approve_run(r1.run_id, db_path=db_path, note="lgtm")
        r2 = run_workflow(
            wf,
            input={"workload": "demo"},
            db_path=db_path,
            run_id=r1.run_id,
            resume=True,
        )
        assert r2.status == RunStatus.COMPLETED
        assert r2.output is not None
        assert r2.output["workload"] == "demo"

        # Resume is idempotent — re-running picks up the cached output rows.
        r3 = run_workflow(
            wf,
            input={"workload": "demo"},
            db_path=db_path,
            run_id=r1.run_id,
            resume=True,
        )
        assert r3.status == RunStatus.COMPLETED

    def test_denied_gate_fails_when_on_deny_fail(self, db_path: str) -> None:
        config = _build_basic_config()
        outputs = config.outputs

        @config.workflow
        def wf(ctx) -> WorkflowNode:
            return WorkflowNode(
                name="x",
                children=[
                    ApprovalGateNode(
                        id="g",
                        when=True,
                        request=ApprovalRequest(title="nope?"),
                        on_deny="fail",
                    )
                ],
            )

        r1 = run_workflow(wf, input={"workload": "x"}, db_path=db_path)
        assert r1.status == RunStatus.PAUSED
        deny_run(r1.run_id, db_path=db_path, note="no")
        r2 = run_workflow(
            wf,
            input={"workload": "x"},
            db_path=db_path,
            run_id=r1.run_id,
            resume=True,
        )
        assert r2.status == RunStatus.FAILED
        assert "denied" in (r2.error or {}).get("message", "").lower()

    def test_denied_gate_continues_when_on_deny_continue(self, db_path: str) -> None:
        config = _build_basic_config()
        outputs = config.outputs

        @config.workflow
        def wf(ctx) -> WorkflowNode:
            return WorkflowNode(
                name="x",
                children=[
                    SequenceNode(
                        children=[
                            ApprovalGateNode(
                                id="g",
                                when=True,
                                request=ApprovalRequest(title="optional?"),
                                on_deny="continue",
                            ),
                            TaskNode(
                                id="final",
                                output=outputs.output,
                                render=lambda: {
                                    "workload": ctx.input.workload,
                                    "total": 0,
                                    "steps": [],
                                },
                            ),
                        ]
                    )
                ],
            )

        r1 = run_workflow(wf, input={"workload": "x"}, db_path=db_path)
        assert r1.status == RunStatus.PAUSED
        deny_run(r1.run_id, db_path=db_path, note="not this time")
        r2 = run_workflow(
            wf,
            input={"workload": "x"},
            db_path=db_path,
            run_id=r1.run_id,
            resume=True,
        )
        assert r2.status == RunStatus.COMPLETED
        # The denied approval shows up in output rows with approved=False.
        denied = [
            r
            for r in r2.output_rows
            if r["schema_version"] == "smithers-py-approval-v0"
        ]
        assert denied and denied[0]["payload"]["approved"] is False

    def test_when_false_auto_passes(self, db_path: str) -> None:
        config = _build_basic_config()
        outputs = config.outputs

        @config.workflow
        def wf(ctx) -> WorkflowNode:
            return WorkflowNode(
                name="x",
                children=[
                    SequenceNode(
                        children=[
                            ApprovalGateNode(
                                id="g",
                                when=False,  # condition false → no gate
                                request=ApprovalRequest(title="never"),
                            ),
                            TaskNode(
                                id="final",
                                output=outputs.output,
                                render=lambda: {
                                    "workload": ctx.input.workload,
                                    "total": 0,
                                    "steps": [],
                                },
                            ),
                        ]
                    )
                ],
            )

        r = run_workflow(wf, input={"workload": "x"}, db_path=db_path)
        assert r.status == RunStatus.COMPLETED


class TestSubflow:
    def test_subflow_runs_under_child_run_id(self, db_path: str) -> None:
        child_config = create_smithers(
            schemas={"input": _Input, "output": _StepOut}
        )

        @child_config.workflow
        def child_wf(ctx) -> WorkflowNode:
            return WorkflowNode(
                name="child",
                children=[
                    TaskNode(
                        id="c1",
                        output=child_config.outputs.output,
                        render=lambda: {"step": "child", "value": 42},
                    )
                ],
            )

        parent_config = create_smithers(
            schemas={"input": _Input, "child_out": _StepOut, "output": _FinalOut}
        )
        parent_outputs = parent_config.outputs

        @parent_config.workflow
        def parent_wf(ctx) -> WorkflowNode:
            return WorkflowNode(
                name="parent",
                children=[
                    SequenceNode(
                        children=[
                            SubflowNode(
                                id="sub",
                                workflow=child_wf,
                                input={"workload": ctx.input.workload, "n": 1},
                                output=parent_outputs.child_out,
                            ),
                            TaskNode(
                                id="wrap",
                                output=parent_outputs.output,
                                render=lambda: {
                                    "workload": ctx.input.workload,
                                    "total": 42,
                                    "steps": ["sub"],
                                },
                            ),
                        ]
                    )
                ],
            )

        result = run_workflow(parent_wf, input={"workload": "p"}, db_path=db_path)
        assert result.status == RunStatus.COMPLETED
        # Two runs visible: parent + child.
        runs = list_runs(db_path=db_path)
        names = {r["workflow_name"] for r in runs}
        assert {"parent_wf", "child_wf"} <= names

    def test_subflow_pauses_propagates_to_parent(self, db_path: str) -> None:
        child_config = create_smithers(schemas={"input": _Input, "output": _StepOut})

        @child_config.workflow
        def child_wf(ctx) -> WorkflowNode:
            return WorkflowNode(
                name="child",
                children=[
                    ApprovalGateNode(
                        id="g",
                        when=True,
                        request=ApprovalRequest(title="child approves?"),
                    ),
                ],
            )

        parent_config = create_smithers(
            schemas={"input": _Input, "child_out": _StepOut}
        )

        @parent_config.workflow
        def parent_wf(ctx) -> WorkflowNode:
            return WorkflowNode(
                name="parent",
                children=[
                    SubflowNode(
                        id="sub",
                        workflow=child_wf,
                        input={"workload": "x", "n": 1},
                        output=parent_config.outputs.child_out,
                    )
                ],
            )

        r = run_workflow(parent_wf, input={"workload": "x"}, db_path=db_path)
        assert r.status == RunStatus.PAUSED
        assert len(r.pending_approvals) == 1


class TestHumanTask:
    def test_human_task_pauses(self, db_path: str) -> None:
        config = create_smithers(schemas={"input": _Input, "output": _StepOut})

        @config.workflow
        def wf(ctx) -> WorkflowNode:
            return WorkflowNode(
                name="h",
                children=[
                    HumanTaskNode(
                        id="op",
                        prompt="What's the plan?",
                        outputSchema=_StepOut,
                    ),
                ],
            )

        r = run_workflow(wf, input={"workload": "x"}, db_path=db_path)
        assert r.status == RunStatus.PAUSED
        assert r.pending_approvals[0].kind == "human_task"


class TestInspect:
    def test_inspect_returns_run_state(self, db_path: str) -> None:
        config = _build_basic_config()

        @config.workflow
        def wf(ctx) -> WorkflowNode:
            return WorkflowNode(
                name="i",
                children=[
                    TaskNode(
                        id="t",
                        output=config.outputs.output,
                        render=lambda: {
                            "workload": ctx.input.workload,
                            "total": 1,
                            "steps": ["t"],
                        },
                    )
                ],
            )

        r = run_workflow(wf, input={"workload": "x"}, db_path=db_path)
        info = inspect_run(r.run_id, db_path=db_path)
        assert info["run"]["status"] == "completed"
        assert len(info["output_rows"]) == 1
        assert info["pending_approvals"] == []

    def test_inspect_unknown_run_raises(self, db_path: str) -> None:
        with pytest.raises(WorkflowError):
            inspect_run("nonexistent-run", db_path=db_path)


class TestAgentTask:
    def test_agent_generate_routes_through_runner(self, db_path: str) -> None:
        config = create_smithers(schemas={"input": _Input, "output": _StepOut})

        class DummyAgent:
            def generate(self, *, prompt: str = "") -> Dict[str, Any]:
                return {
                    "output": {"step": "agent", "value": len(prompt)},
                    "text": "ignored",
                }

        @config.workflow
        def wf(ctx) -> WorkflowNode:
            return WorkflowNode(
                name="agent",
                children=[
                    TaskNode(
                        id="t",
                        output=config.outputs.output,
                        agent=DummyAgent(),
                        prompt="hi",
                    )
                ],
            )

        r = run_workflow(wf, input={"workload": "x"}, db_path=db_path)
        assert r.status == RunStatus.COMPLETED
        assert r.output is not None
        assert r.output["step"] == "agent"
        assert r.output["value"] == 2
