"""Phase: crate compile bring-up.

Mirrors examples/bun-port-smithers/workflows/crate-compile-bringup.tsx:
    Sequence
      ├── compile:plan                (group crates by tier)
      ├── Per tier (in order):
      │     Sequence
      │       └── Parallel(per crate):
      │             Loop(maxIterations=maxRounds, until=compiles)
      │               └── crate-check Task (agent)
      ├── compile:report
      └── compile:output (PhaseDone)
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
from ..components.porting_rules import plan_crates_by_tier
from ..components.schemas import (
    CompileReport,
    CrateCheck,
    CrateCompileInput,
    CratePlan,
    PhaseDone,
)


CONFIG = create_smithers(
    schemas={
        "input": CrateCompileInput,
        "cratePlan": CratePlan,
        "crateCheck": CrateCheck,
        "compileReport": CompileReport,
        "output": PhaseDone,
    }
)
outputs = CONFIG.outputs


@CONFIG.workflow
def crate_compile_bringup(ctx: Any) -> WorkflowNode:
    agents = agents_for_repo(ctx.input.repo)
    plan = plan_crates_by_tier([c.model_dump() for c in ctx.input.crates])

    plan_task = TaskNode(
        id="compile:plan",
        output=outputs.cratePlan,
        render=lambda: plan,
    )

    tier_children: List[Any] = []
    for tier in plan["tiers"]:
        crate_loops: List[LoopNode] = []
        for c in tier["crates"]:
            name = c["name"]
            tier_n = c.get("tier", 0)
            crate_loops.append(
                LoopNode(
                    id=f"compile:{name}",
                    maxIterations=max(1, ctx.input.maxRounds),
                    until=lambda c_ctx, n=name: (
                        (c_ctx.output(f"compile:{n}:check") or {}).get("compiles", False)
                    ),
                    onMaxReached="return-last",
                    children=[
                        TaskNode(
                            id=f"compile:{name}:check",
                            output=outputs.crateCheck,
                            agent=agents["crateChecker"],
                            prompt=f"CRATE: {name}\nTIER: {tier_n}",
                        )
                    ],
                )
            )
        tier_children.append(
            SequenceNode(
                children=[
                    ParallelNode(
                        max_concurrency=max(1, len(crate_loops)),
                        children=crate_loops,
                    )
                ]
            )
        )

    def _report() -> dict:
        green: List[str] = []
        failing: List[str] = []
        gated = 0
        for tier in plan["tiers"]:
            for c in tier["crates"]:
                name = c["name"]
                check = ctx.output(f"compile:{name}:check") or {}
                if check.get("compiles"):
                    green.append(name)
                else:
                    failing.append(name)
                gated += len(check.get("gatedModules") or [])
        return {
            "totalCrates": plan["totalCrates"],
            "green": len(green),
            "failing": len(failing),
            "gatedModules": gated,
            "greenCrates": green,
            "failingCrates": failing,
            "summary": f"Compile: {len(green)}/{plan['totalCrates']} crates green, {gated} gated modules.",
        }

    report_task = TaskNode(
        id="compile:report",
        output=outputs.compileReport,
        render=_report,
    )

    def _emit_done() -> dict:
        rep = ctx.output("compile:report") or {}
        status = "partial" if rep.get("failing", 0) > 0 else "completed"
        return {"phase": "compile", "status": status, "summary": rep.get("summary", "Compile phase finished.")}

    output_task = TaskNode(
        id="compile:output",
        output=outputs.output,
        render=_emit_done,
    )

    return WorkflowNode(
        name="bun-port-py-compile",
        children=[
            SequenceNode(
                children=[plan_task, *tier_children, report_task, output_task]
            )
        ],
    )


__all__ = ["crate_compile_bringup", "CONFIG", "outputs"]
