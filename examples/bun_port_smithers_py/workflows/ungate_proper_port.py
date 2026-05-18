"""Phase: ungate / proper-port.

Mirrors examples/bun-port-smithers/workflows/ungate-proper-port.tsx:
    Sequence
      ├── ungate:survey
      ├── Parallel(per target):
      │     Loop(maxRounds):
      │       Sequence
      │         ├── patch Task (agent)
      │         ├── Parallel(2 reviewers)
      │         └── spec-decision Task (agent)
      ├── ungate:report
      └── ungate:output (PhaseDone)
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
from ..components.porting_rules import stable_node_id, survey_targets
from ..components.schemas import (
    PatchResult,
    PhaseDone,
    SpecDecision,
    SpecReview,
    TargetSurvey,
    UngateInput,
    UngateReport,
)


CONFIG = create_smithers(
    schemas={
        "input": UngateInput,
        "targetSurvey": TargetSurvey,
        "patchResult": PatchResult,
        "specReview": SpecReview,
        "specDecision": SpecDecision,
        "ungateReport": UngateReport,
        "output": PhaseDone,
    }
)
outputs = CONFIG.outputs


@CONFIG.workflow
def ungate_proper_port(ctx: Any) -> WorkflowNode:
    agents = agents_for_repo(ctx.input.repo)
    targets = [t.model_dump() for t in ctx.input.targets]
    survey = survey_targets(targets)

    survey_task = TaskNode(
        id="ungate:survey",
        output=outputs.targetSurvey,
        render=lambda: survey,
    )

    target_loops: List[LoopNode] = []
    for t in targets:
        tid = t["id"]
        nid = stable_node_id(tid)
        target_loops.append(
            LoopNode(
                id=f"ungate:{nid}",
                maxIterations=max(1, ctx.input.maxRounds),
                until=lambda c_ctx, n=nid: (
                    (c_ctx.output(f"ungate:{n}:decide") or {}).get("approved", False)
                ),
                onMaxReached="return-last",
                children=[
                    SequenceNode(
                        children=[
                            TaskNode(
                                id=f"ungate:{nid}:patch",
                                output=outputs.patchResult,
                                agent=agents["properPorter"],
                                prompt=f"TARGET: {tid}\nCRATE: {t.get('crate', '')}\nFILE: {t.get('file', '')}",
                            ),
                            ParallelNode(
                                max_concurrency=2,
                                children=[
                                    TaskNode(
                                        id=f"ungate:{nid}:review:1",
                                        output=outputs.specReview,
                                        agent=agents["specReviewer"],
                                        prompt=f"TARGET: {tid}\nVOTER: r1",
                                    ),
                                    TaskNode(
                                        id=f"ungate:{nid}:review:2",
                                        output=outputs.specReview,
                                        agent=agents["specReviewer"],
                                        prompt=f"TARGET: {tid}\nVOTER: r2",
                                    ),
                                ],
                            ),
                            TaskNode(
                                id=f"ungate:{nid}:decide",
                                output=outputs.specDecision,
                                agent=agents["specDecider"],
                                prompt=f"TARGET: {tid}",
                            ),
                        ]
                    )
                ],
            )
        )

    def _report() -> dict:
        patched = approved = rejected = 0
        for t in targets:
            nid = stable_node_id(t["id"])
            p = ctx.output(f"ungate:{nid}:patch") or {}
            d = ctx.output(f"ungate:{nid}:decide") or {}
            if p.get("status") == "patched":
                patched += 1
            if d.get("approved"):
                approved += 1
            else:
                rejected += 1
        return {
            "totalTargets": survey["totalTargets"],
            "patched": patched,
            "approved": approved,
            "rejected": rejected,
            "summary": f"Ungate: {approved}/{survey['totalTargets']} approved, {patched} patched.",
        }

    report_task = TaskNode(
        id="ungate:report",
        output=outputs.ungateReport,
        render=_report,
    )

    def _emit_done() -> dict:
        rep = ctx.output("ungate:report") or {}
        status = "partial" if rep.get("rejected", 0) > 0 else "completed"
        return {"phase": "ungate", "status": status, "summary": rep.get("summary", "Ungate finished.")}

    output_task = TaskNode(
        id="ungate:output",
        output=outputs.output,
        render=_emit_done,
    )

    return WorkflowNode(
        name="bun-port-py-ungate",
        children=[
            SequenceNode(
                children=[
                    survey_task,
                    ParallelNode(
                        max_concurrency=max(1, len(target_loops)),
                        children=target_loops,
                    ),
                    report_task,
                    output_task,
                ]
            )
        ],
    )


__all__ = ["ungate_proper_port", "CONFIG", "outputs"]
