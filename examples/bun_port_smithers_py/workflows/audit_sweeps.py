"""Phase: audit sweeps.

Mirrors examples/bun-port-smithers/workflows/audit-sweeps.tsx:
    Sequence
      ├── sweeps:survey
      ├── Parallel(per sweep)
      ├── sweeps:report
      └── sweeps:output (PhaseDone)
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
from ..components.porting_rules import stable_node_id, survey_sweeps
from ..components.schemas import (
    PhaseDone,
    SweepInput,
    SweepReport,
    SweepResult,
    SweepSurvey,
)


CONFIG = create_smithers(
    schemas={
        "input": SweepInput,
        "sweepSurvey": SweepSurvey,
        "sweepResult": SweepResult,
        "sweepReport": SweepReport,
        "output": PhaseDone,
    }
)
outputs = CONFIG.outputs


@CONFIG.workflow
def audit_sweeps(ctx: Any) -> WorkflowNode:
    agents = agents_for_repo(ctx.input.repo)
    sweeps = [s.model_dump() for s in ctx.input.sweeps]
    survey = survey_sweeps(sweeps)

    survey_task = TaskNode(
        id="sweeps:survey",
        output=outputs.sweepSurvey,
        render=lambda: survey,
    )

    sweep_tasks: List[TaskNode] = []
    for s in sweeps:
        sid = s["id"]
        nid = stable_node_id(sid)
        sweep_tasks.append(
            TaskNode(
                id=f"sweeps:{nid}:run",
                output=outputs.sweepResult,
                agent=agents["sweepAgent"],
                prompt=f"SWEEP: {sid}\nKIND: {s.get('kind', '')}",
            )
        )

    def _report() -> dict:
        results = [ctx.output(t.id) for t in sweep_tasks]
        fixed = sum(r.get("fixed", 0) for r in results if r)
        skipped = sum(r.get("skipped", 0) for r in results if r)
        return {
            "totalSweeps": len(sweeps),
            "fixed": fixed,
            "skipped": skipped,
            "summary": f"Sweeps: {fixed} fixed across {len(sweeps)} sweep(s).",
        }

    report_task = TaskNode(
        id="sweeps:report",
        output=outputs.sweepReport,
        render=_report,
    )

    def _emit_done() -> dict:
        rep = ctx.output("sweeps:report") or {}
        return {
            "phase": "sweeps",
            "status": "completed",
            "summary": rep.get("summary", "Sweeps finished."),
        }

    output_task = TaskNode(
        id="sweeps:output",
        output=outputs.output,
        render=_emit_done,
    )

    return WorkflowNode(
        name="bun-port-py-sweeps",
        children=[
            SequenceNode(
                children=[
                    survey_task,
                    ParallelNode(
                        max_concurrency=max(1, len(sweep_tasks)),
                        children=sweep_tasks,
                    ),
                    report_task,
                    output_task,
                ]
            )
        ],
    )


__all__ = ["audit_sweeps", "CONFIG", "outputs"]
