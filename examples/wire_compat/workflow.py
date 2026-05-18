"""Canonical wire-compat workflow.

Exercises every TS-shape primitive that's currently in scope for v0.1
parity, with deterministic inputs and outputs so the resulting row set
is reproducible. The output rows are what we diff against the TS
runtime's rows to prove the port behaves identically at the durable-
state level.

Primitives covered:
    WorkflowNode, SequenceNode, ParallelNode, BranchNode, LoopNode,
    TaskNode (render path), TaskNode with agent (DryAgent),
    SubflowNode (with child run), ApprovalGateNode (auto-pass branch).

Not covered here:
    HumanTaskNode — always pauses, not suitable for a unit-test
    snapshot. WorktreeNode / MergeQueueNode — structural pass-throughs
    with no observable row contribution.

The snapshot lives at ``examples/wire_compat/snapshot.json`` and is
normalized to strip run_id and timestamps before comparison.
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field

from smithers_py import (
    ApprovalGateNode,
    ApprovalRequest,
    BranchNode,
    DryAgent,
    LoopNode,
    ParallelNode,
    SequenceNode,
    SubflowNode,
    TaskNode,
    WorkflowNode,
    create_smithers,
)


# ----- Schemas ---------------------------------------------------------------


class WireInput(BaseModel):
    workload: str
    branch: bool = True
    iterations: int = 3


class StepOut(BaseModel):
    schema_version: str = "wire-compat-step-v0"
    step: str
    value: int


class ChildOut(BaseModel):
    schema_version: str = "wire-compat-child-v0"
    label: str
    payload: List[str]


class FinalOut(BaseModel):
    schema_version: str = "wire-compat-final-v0"
    workload: str
    sequence_total: int
    parallel_total: int
    branch_step: str
    loop_iterations: int


class ApprovalRow(BaseModel):
    """Permissive approval shape matching TS's Drizzle approval row.

    The TS Zod schema for the registered ``approval`` output is
    ``z.object({approved: z.boolean()}).loose()`` — the wire-compat
    contract is that an ApprovalGate's resolved row has at minimum
    ``approved: bool``. Python mirrors that exactly.
    """

    model_config = {"extra": "allow"}

    approved: bool


# ----- Child workflow --------------------------------------------------------


CHILD_CONFIG = create_smithers(
    schemas={
        "input": WireInput,
        "output": ChildOut,
    }
)


@CHILD_CONFIG.workflow
def child_workflow(ctx) -> WorkflowNode:
    return WorkflowNode(
        name="wire-compat-child",
        children=[
            TaskNode(
                id="child-emit",
                output=CHILD_CONFIG.outputs.output,
                render=lambda: {
                    "label": f"child-of-{ctx.input.workload}",
                    "payload": ["alpha", "beta", "gamma"],
                },
            )
        ],
    )


# ----- Parent workflow -------------------------------------------------------


CONFIG = create_smithers(
    schemas={
        "input": WireInput,
        "seq1": StepOut,
        "seq2": StepOut,
        "par1": StepOut,
        "par2": StepOut,
        "par3": StepOut,
        "branch_step": StepOut,
        "loop_step": StepOut,
        "child_out": ChildOut,
        "approval": ApprovalRow,
        "output": FinalOut,
    }
)
outputs = CONFIG.outputs


@CONFIG.workflow
def wire_compat_workflow(ctx) -> WorkflowNode:
    """All primitives in one deterministic graph."""
    return WorkflowNode(
        name="wire-compat",
        children=[
            SequenceNode(
                children=[
                    # Sequential tasks
                    TaskNode(
                        id="seq-1",
                        output=outputs.seq1,
                        render=lambda: {"step": "seq-1", "value": 100},
                    ),
                    TaskNode(
                        id="seq-2",
                        output=outputs.seq2,
                        agent=DryAgent(
                            id="seq-2-dry",
                            output={"step": "seq-2", "value": 200},
                        ),
                        prompt="dry",
                    ),
                    # Parallel fan-out — 3 children execute (sequentially
                    # for v0.1, in parallel post-concurrency lift).
                    ParallelNode(
                        max_concurrency=4,
                        children=[
                            TaskNode(
                                id=f"par-{i}",
                                output=getattr(outputs, f"par{i}"),
                                render=lambda i=i: {
                                    "step": f"par-{i}",
                                    "value": 10 * i,
                                },
                            )
                            for i in (1, 2, 3)
                        ],
                    ),
                    # Branch: condition is taken from ctx.input.
                    BranchNode(
                        **{"if": ctx.input.branch},
                        then=TaskNode(
                            id="branch-then",
                            output=outputs.branch_step,
                            render=lambda: {"step": "branch-then", "value": 1},
                        ),
                        else_child=TaskNode(
                            id="branch-else",
                            output=outputs.branch_step,
                            render=lambda: {"step": "branch-else", "value": 0},
                        ),
                    ),
                    # Loop: a deterministic counter that exits after N
                    # iterations.
                    LoopNode(
                        id="loop",
                        maxIterations=ctx.input.iterations,
                        until=lambda c: False,  # exhaust loop deterministically
                        onMaxReached="return-last",
                        children=[
                            TaskNode(
                                id="loop-step",
                                output=outputs.loop_step,
                                render=lambda: {"step": "loop-tick", "value": 1},
                            )
                        ],
                    ),
                    # Approval gate with when=False so it auto-passes
                    # (snapshot stays deterministic).
                    ApprovalGateNode(
                        id="gate",
                        output=outputs.approval,
                        when=False,
                        request=ApprovalRequest(title="auto-pass"),
                        on_deny="continue",
                    ),
                    # Subflow with child run.
                    SubflowNode(
                        id="sub",
                        workflow=child_workflow,
                        input={
                            "workload": ctx.input.workload,
                            "branch": True,
                            "iterations": 1,
                        },
                        output=outputs.child_out,
                    ),
                    # Terminal output.
                    TaskNode(
                        id="final",
                        output=outputs.output,
                        render=lambda: {
                            "workload": ctx.input.workload,
                            "sequence_total": 300,
                            "parallel_total": 60,
                            "branch_step": "branch-then" if ctx.input.branch else "branch-else",
                            "loop_iterations": ctx.input.iterations,
                        },
                    ),
                ]
            )
        ],
    )


__all__ = [
    "wire_compat_workflow",
    "child_workflow",
    "CONFIG",
    "CHILD_CONFIG",
    "outputs",
]
