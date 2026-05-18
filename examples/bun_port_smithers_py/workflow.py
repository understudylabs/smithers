"""Top-level bun-port workflow, ported to Python.

Mirrors examples/bun-port-smithers/workflow.tsx in shape: a `Sequence` of
phase Subflows guarded by an operator HumanTask and gated by per-phase
ApprovalGates with numeric thresholds.

Today only the lifetime-classify phase is fleshed out; the other phases
are scaffolded as Subflow placeholders so the parent graph shape matches
the TS reference end-to-end. As each phase workflow lands, drop in the
import and replace the placeholder.
"""

from __future__ import annotations

from typing import Any, Dict, List

from smithers_py import (
    ApprovalGateNode,
    ApprovalRequest,
    HumanTaskNode,
    SequenceNode,
    SubflowNode,
    TaskNode,
    WorkflowNode,
    create_smithers,
)

from .components.schemas import (
    Approval,
    BunPortFinal,
    BunPortInput,
    OperatorPlan,
    PhaseDone,
    WorkflowPhase,
)
from .workflows.lifetime_classify import lifetime_classify


CONFIG = create_smithers(
    schemas={
        "input": BunPortInput,
        "operatorPlan": OperatorPlan,
        "childRunResult": PhaseDone,
        "approval": Approval,
        "output": BunPortFinal,
    },
    db_path="smithers.db",
)
outputs = CONFIG.outputs


PHASE_TO_FLAG = {
    "lifetimes": "runLifetimes",
    "phaseA": "runPhaseA",
    "compile": "runCompile",
    "ungate": "runUngate",
    "probes": "runProbes",
    "tests": "runTests",
    "sweeps": "runSweeps",
}


def _phase_subflow(phase: WorkflowPhase, ctx_input: BunPortInput) -> SubflowNode:
    """Build the Subflow node for one phase.

    For now, only ``lifetimes`` has a real child workflow wired in; the rest
    point at the same workflow as placeholders so the parent graph is
    structurally complete. Replace as each phase ports.
    """
    if phase == "lifetimes":
        child = lifetime_classify
        input_payload: Dict[str, Any] = {
            "repo": ctx_input.repo,
            "files": [f.model_dump() for f in ctx_input.files],
            "sampleRate": 0.12,
            "unknownApprovalThreshold": ctx_input.unknownApprovalThreshold,
            "portingRevision": "",
            "lifetimeRevision": "",
        }
    else:
        child = lifetime_classify  # placeholder until phase X lands
        input_payload = {"phase": phase}
    return SubflowNode(
        id=f"main:{phase}",
        workflow=child,
        input=input_payload,
        output=outputs.childRunResult,
    )


@CONFIG.workflow
def bun_port_workflow(ctx: Any) -> WorkflowNode:
    """Top-level workflow definition."""
    requested: List[WorkflowPhase] = list(ctx.input.phases)
    body: List[Any] = []

    # Operator plan — optional gate at the very start.
    if ctx.input.requireOperatorPlan:
        body.append(
            HumanTaskNode(
                id="main:operator-plan",
                output=outputs.operatorPlan,
                output_schema=OperatorPlan,
                prompt=(
                    f"Operator approval required for bun-port run.\n"
                    f"Repo: {ctx.input.repo}\n"
                    f"Phases: {', '.join(requested)}\n"
                    f"useWorktrees: {ctx.input.useWorktrees}"
                ),
                max_attempts=5,
                timeout_ms=7 * 24 * 60 * 60_000,
            )
        )

    # One Subflow per requested phase.
    for phase in requested:
        body.append(_phase_subflow(phase, ctx.input))

        # ApprovalGate after lifetimes when UNKNOWN rate exceeds threshold.
        if phase == "lifetimes":
            body.append(
                ApprovalGateNode(
                    id="main:lifetimes:approval",
                    when=True,  # engine wires actual condition from prior output
                    request=ApprovalRequest(
                        title="Approve lifetime classification quality?",
                        summary=(
                            "ApprovalGate fires when UNKNOWN-rate exceeds the "
                            f"configured threshold ({ctx.input.unknownApprovalThreshold:.0%})."
                        ),
                    ),
                    on_deny="fail",
                    output=outputs.approval,
                )
            )

    # Terminal node: emits the BunPortFinal record.
    def _final() -> dict:
        return BunPortFinal(
            status="completed",
            phasesRun=requested,
            summary=(
                f"bun-port-py workflow completed {len(requested)} phase(s). "
                f"Lifetime classifier emitted; downstream phases use placeholder "
                f"subflows until the rest of the port lands."
            ),
            nextActions=[
                "Wire phaseA, compile, ungate, probes, tests, sweeps workflows.",
                "Add engine dispatch for TaskNode.agent + ApprovalGateNode.",
                "Cross-runtime resume test against TS Smithers.",
            ],
        ).model_dump()

    body.append(
        TaskNode(
            id="main:final",
            output=outputs.output,
            render=_final,
        )
    )

    return WorkflowNode(
        name="bun-port-py",
        children=[SequenceNode(children=body)],
    )


__all__ = ["bun_port_workflow", "CONFIG", "outputs"]
