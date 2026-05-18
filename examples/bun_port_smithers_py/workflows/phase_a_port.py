"""Phase A — per-file Zig→Rust port.

Mirrors examples/bun-port-smithers/workflows/phase-a-port.tsx:
    Sequence
      ├── phase-a:plan                (normalize files; emit plan)
      ├── Parallel(per file in plan):
      │     Sequence
      │       ├── phase-a:<id>:implement    (agent)
      │       ├── phase-a:<id>:verify       (agent)
      │       └── (if must-fix) phase-a:<id>:fix  (agent)
      ├── phase-a:report
      └── phase-a:output (PhaseDone)
"""

from __future__ import annotations

from typing import Any, List

from smithers_py import (
    ParallelNode,
    SequenceNode,
    TaskNode,
    WorkflowNode,
    create_smithers,
)

from ..components.agents import agents_for_repo
from ..components.porting_rules import normalize_port_files, stable_node_id
from ..components.schemas import (
    PhaseAFix,
    PhaseAImplement,
    PhaseAInput,
    PhaseAPlan,
    PhaseAReport,
    PhaseDone,
    Review,
)


CONFIG = create_smithers(
    schemas={
        "input": PhaseAInput,
        "phaseAPlan": PhaseAPlan,
        "phaseAImplement": PhaseAImplement,
        "phaseAReview": Review,
        "phaseAFix": PhaseAFix,
        "phaseAReport": PhaseAReport,
        "output": PhaseDone,
    }
)
outputs = CONFIG.outputs


@CONFIG.workflow
def phase_a_port(ctx: Any) -> WorkflowNode:
    agents = agents_for_repo(ctx.input.repo)
    input_files = [f.model_dump() for f in ctx.input.files]
    normalized = normalize_port_files(input_files)

    def _plan() -> dict:
        return {"total": len(normalized), "files": normalized}

    plan_task = TaskNode(
        id="phase-a:plan",
        output=outputs.phaseAPlan,
        render=_plan,
    )

    per_file_sequences: List[SequenceNode] = []
    for f in normalized:
        zig = f["zig"]
        file_id = stable_node_id(zig)
        crate = f.get("crate") or ""

        implement = TaskNode(
            id=f"phase-a:{file_id}:implement",
            output=outputs.phaseAImplement,
            agent=agents["phaseAImplementer"],
            prompt=f"ZIG: {zig}\nCRATE: {crate}\nRS: {f['rs']}",
            max_attempts=2,
        )

        verify = TaskNode(
            id=f"phase-a:{file_id}:verify",
            output=outputs.phaseAReview,
            agent=agents["phaseAVerifier"],
            prompt=f"SUBJECT: {f['rs']}\nZIG: {zig}",
        )

        # In dry mode the verify result is always approved → no fix task
        # needs to be emitted unconditionally. We render the fix task
        # but it'll no-op in dry mode (applied=0, remaining=0).
        fix = TaskNode(
            id=f"phase-a:{file_id}:fix",
            output=outputs.phaseAFix,
            agent=agents["phaseAFixer"],
            prompt=f"ZIG: {zig}\nRS: {f['rs']}",
        )

        per_file_sequences.append(
            SequenceNode(children=[implement, verify, fix])
        )

    def _report() -> dict:
        impls = []
        reviews = []
        fixes = []
        for f in normalized:
            fid = stable_node_id(f["zig"])
            impl = ctx.output(f"phase-a:{fid}:implement")
            rev = ctx.output(f"phase-a:{fid}:verify")
            fx = ctx.output(f"phase-a:{fid}:fix")
            if impl: impls.append(impl)
            if rev: reviews.append(rev)
            if fx: fixes.append(fx)
        clean = sum(1 for r in reviews if r.get("approved") or r.get("ok"))
        fixed = sum(1 for fx in fixes if fx.get("remaining", 0) == 0)
        failed = sum(1 for im in impls if im.get("status") == "failed")
        todo = sum(im.get("todos", 0) for im in impls)
        return {
            "total": len(normalized),
            "clean": clean,
            "fixed": fixed,
            "failed": failed,
            "todoCount": todo,
            "summary": f"Phase A: {clean}/{len(normalized)} clean, {len(fixes)} fix task(s).",
        }

    report_task = TaskNode(
        id="phase-a:report",
        output=outputs.phaseAReport,
        render=_report,
    )

    def _emit_done() -> dict:
        rep = ctx.output("phase-a:report") or {}
        return {
            "phase": "phaseA",
            "status": "partial" if rep.get("failed", 0) > 0 else "completed",
            "summary": rep.get("summary", "Phase A finished."),
        }

    output_task = TaskNode(
        id="phase-a:output",
        output=outputs.output,
        render=_emit_done,
    )

    return WorkflowNode(
        name="bun-port-py-phase-a",
        children=[
            SequenceNode(
                children=[
                    plan_task,
                    ParallelNode(
                        max_concurrency=ctx.input.maxConcurrency,
                        children=per_file_sequences,
                    ),
                    report_task,
                    output_task,
                ]
            )
        ],
    )


__all__ = ["phase_a_port", "CONFIG", "outputs"]
