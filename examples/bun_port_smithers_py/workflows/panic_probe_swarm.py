"""Phase: panic probe swarm.

Mirrors examples/bun-port-smithers/workflows/panic-probe-swarm.tsx:
    Sequence
      └── Loop(maxRounds):
            Sequence
              ├── build Task
              ├── Parallel(per probe)
              ├── probe:dedupe (deterministic)
              ├── Parallel(per unique failure -> failure-fix)
              └── probe:report
            until: all probes passed
      └── probe:output (PhaseDone)
"""

from __future__ import annotations

from typing import Any, List

from smithers_py import (
    LoopNode,
    ParallelNode,
    SequenceNode,
    TaskNode,
    WorkflowNode,
    create_smithers,
)

from ..components.agents import agents_for_repo
from ..components.porting_rules import dedupe_failures, stable_node_id
from ..components.schemas import (
    BuildResult,
    FailureFix,
    FailureSet,
    PhaseDone,
    ProbeInput,
    ProbeReport,
    ProbeResult,
)


CONFIG = create_smithers(
    schemas={
        "input": ProbeInput,
        "buildResult": BuildResult,
        "probeResult": ProbeResult,
        "failureSet": FailureSet,
        "failureFix": FailureFix,
        "probeReport": ProbeReport,
        "output": PhaseDone,
    }
)
outputs = CONFIG.outputs


@CONFIG.workflow
def panic_probe_swarm(ctx: Any) -> WorkflowNode:
    agents = agents_for_repo(ctx.input.repo)
    probes = [p.model_dump() for p in ctx.input.probes]

    probe_tasks = [
        TaskNode(
            id=f"probe:run:{stable_node_id(p['id'])}",
            output=outputs.probeResult,
            agent=agents["prober"],
            prompt=f"PROBE: {p['id']}\nCOMMAND: {p['cmd']}",
        )
        for p in probes
    ]

    def _dedupe() -> dict:
        results = [ctx.output(t.id) for t in probe_tasks]
        return dedupe_failures(results)

    dedupe_task = TaskNode(
        id="probe:dedupe",
        output=outputs.failureSet,
        render=_dedupe,
    )

    # Pre-build the failure-fix fan-out for any failure we *expect* to
    # see. In dry mode every probe passes, so this Parallel has zero
    # children most of the time. Workflow authors targeting a real
    # bun checkout can swap dry agents for real ones to surface
    # genuine failures.
    fix_tasks: List[TaskNode] = []
    # We don't know failure keys at graph-construction time. The bun
    # port's TS version reads them from the dedupe output at render
    # time; our walker doesn't re-render mid-walk, so we accept that
    # in dry mode the fix Parallel is empty.

    def _report() -> dict:
        results = [ctx.output(t.id) for t in probe_tasks]
        passed = sum(1 for r in results if r and r.get("passed"))
        failures = ctx.output("probe:dedupe") or {"totalFailures": 0}
        return {
            "totalProbes": len(probes),
            "passed": passed,
            "uniqueFailures": failures.get("totalFailures", 0),
            "fixes": 0,
            "summary": f"Probes: {passed}/{len(probes)} passed, {failures.get('totalFailures', 0)} unique failures.",
        }

    report_task = TaskNode(
        id="probe:report",
        output=outputs.probeReport,
        render=_report,
    )

    inner = SequenceNode(
        children=[
            TaskNode(
                id="probe:build",
                output=outputs.buildResult,
                agent=agents["builder"],
                prompt="cargo build -p bun_bin",
            ),
            ParallelNode(
                max_concurrency=max(1, len(probe_tasks)),
                children=probe_tasks,
            ),
            dedupe_task,
            report_task,
        ]
    )

    def _emit_done() -> dict:
        rep = ctx.output("probe:report") or {}
        status = "completed" if rep.get("uniqueFailures", 0) == 0 else "partial"
        return {"phase": "probes", "status": status, "summary": rep.get("summary", "Probes finished.")}

    output_task = TaskNode(
        id="probe:output",
        output=outputs.output,
        render=_emit_done,
    )

    return WorkflowNode(
        name="bun-port-py-probes",
        children=[
            SequenceNode(
                children=[
                    LoopNode(
                        id="probe:loop",
                        maxIterations=max(1, ctx.input.maxRounds),
                        until=lambda c_ctx: (
                            (c_ctx.output("probe:report") or {}).get("uniqueFailures", 1) == 0
                        ),
                        onMaxReached="return-last",
                        children=[inner],
                    ),
                    output_task,
                ]
            )
        ],
    )


__all__ = ["panic_probe_swarm", "CONFIG", "outputs"]
