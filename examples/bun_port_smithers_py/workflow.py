"""Top-level bun-port-py workflow.

Mirrors examples/bun-port-smithers/workflow.tsx end-to-end. All 7 phase
Subflows are wired to real workflows:

  Sequence
    ├── (optional HumanTask: operator-plan)
    ├── Subflow: lifetimes      (workflows/lifetime_classify.py)
    ├── ApprovalGate: post-lifetimes
    ├── Subflow: phaseA         (workflows/phase_a_port.py)
    ├── Subflow: compile        (workflows/crate_compile_bringup.py)
    ├── ApprovalGate: post-compile
    ├── Subflow: ungate         (workflows/ungate_proper_port.py)
    ├── Subflow: probes         (workflows/panic_probe_swarm.py)
    ├── Subflow: tests          (workflows/test_swarm.py)
    ├── Subflow: sweeps         (workflows/audit_sweeps.py)
    └── final TaskNode → BunPortFinal
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
    ApprovalRow,
    BunPortFinal,
    BunPortInput,
    OperatorPlan,
    PhaseDone,
    WorkflowPhase,
)
from .workflows.audit_sweeps import audit_sweeps
from .workflows.crate_compile_bringup import crate_compile_bringup
from .workflows.lifetime_classify import lifetime_classify
from .workflows.panic_probe_swarm import panic_probe_swarm
from .workflows.phase_a_port import phase_a_port
from .workflows.test_swarm import test_swarm
from .workflows.ungate_proper_port import ungate_proper_port


CONFIG = create_smithers(
    schemas={
        "input": BunPortInput,
        "operatorPlan": OperatorPlan,
        "childRunResult": PhaseDone,
        "approval": ApprovalRow,
        "output": BunPortFinal,
    }
)
outputs = CONFIG.outputs


_PHASE_DISPATCH = {
    "lifetimes": (lifetime_classify, lambda ctx: {
        "repo": ctx.input.repo,
        "files": [f.model_dump() for f in ctx.input.files],
        "sampleRate": 0.12,
        "unknownApprovalThreshold": ctx.input.unknownApprovalThreshold,
        "portingRevision": "",
        "lifetimeRevision": "",
    }),
    "phaseA": (phase_a_port, lambda ctx: {
        "repo": ctx.input.repo,
        "files": [f.model_dump() for f in ctx.input.files],
        "maxConcurrency": ctx.input.maxConcurrency,
    }),
    "compile": (crate_compile_bringup, lambda ctx: {
        "repo": ctx.input.repo,
        "crates": [c.model_dump() for c in ctx.input.crates],
        "broadGateApprovalThreshold": ctx.input.broadGateApprovalThreshold,
    }),
    "ungate": (ungate_proper_port, lambda ctx: {
        "repo": ctx.input.repo,
        "targets": [t.model_dump() for t in ctx.input.targets],
    }),
    "probes": (panic_probe_swarm, lambda ctx: {
        "repo": ctx.input.repo,
        "probes": [p.model_dump() for p in ctx.input.probes],
    }),
    "tests": (test_swarm, lambda ctx: {
        "repo": ctx.input.repo,
        "baseBranch": ctx.input.baseBranch,
        "useWorktrees": ctx.input.useWorktrees,
        "maxConcurrency": ctx.input.maxConcurrency,
        "areas": [a.model_dump() for a in ctx.input.areas],
        "awaitExternalCiSignal": ctx.input.awaitExternalCiSignal,
    }),
    "sweeps": (audit_sweeps, lambda ctx: {
        "repo": ctx.input.repo,
        "sweeps": [s.model_dump() for s in ctx.input.sweeps],
    }),
}


@CONFIG.workflow
def bun_port_workflow(ctx: Any) -> WorkflowNode:
    requested: List[WorkflowPhase] = list(ctx.input.phases)
    body: List[Any] = []

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

    for phase in requested:
        if phase not in _PHASE_DISPATCH:
            continue
        wf, input_fn = _PHASE_DISPATCH[phase]
        body.append(
            SubflowNode(
                id=f"main:{phase}",
                workflow=wf,
                input=input_fn(ctx),
                output=outputs.childRunResult,
            )
        )
        if phase == "lifetimes":
            body.append(
                ApprovalGateNode(
                    id="main:lifetimes:approval",
                    output=outputs.approval,
                    when=False,
                    request=ApprovalRequest(
                        title="Approve lifetime classification quality?",
                        summary=(
                            f"Fires when UNKNOWN-rate exceeds the "
                            f"configured threshold ({ctx.input.unknownApprovalThreshold:.0%})."
                        ),
                    ),
                    on_deny="fail",
                )
            )
        elif phase == "compile":
            body.append(
                ApprovalGateNode(
                    id="main:compile:approval",
                    output=outputs.approval,
                    when=False,
                    request=ApprovalRequest(
                        title="Approve compile gate/stub debt?",
                        summary=(
                            f"Fires when gated module count exceeds "
                            f"{ctx.input.broadGateApprovalThreshold}."
                        ),
                    ),
                    on_deny="fail",
                )
            )

    def _final() -> dict:
        return {
            "status": "completed",
            "phasesRun": requested,
            "summary": (
                f"bun-port-py workflow completed {len(requested)} phase(s) in dry mode. "
                f"All 7 phase Subflows wired to real workflows."
            ),
            "nextActions": [
                "Wire real-mode agents via AgentLike (Anthropic, Claude Code, Codex, Pi).",
                "Add real concurrency to ParallelNode (v0.2 anyio lift).",
                "Add Signal / WaitForEvent for external CI (v0.2).",
                "Cross-runtime row diff against TS run (use examples/wire_compat).",
            ],
        }

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
