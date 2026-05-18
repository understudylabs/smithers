"""Phase: test swarm with worktree + merge queue.

Mirrors examples/bun-port-smithers/workflows/test-swarm.tsx, simplified
for the v0.1 runtime:

- Per-area Loop running the test-area agent (drives bun test).
- WorktreeNode wraps each area's runs (structural pass-through in v0.1).
- MergeQueueNode serializes merges of green areas (structural pass-
  through in v0.1).
- Signal/WaitForEvent for external CI is deferred to v0.2 — the runtime
  doesn't have Signal/WaitForEvent node types yet. ``awaitExternalCiSignal``
  in the input is honored as a flag but the workflow doesn't actually wait.
"""

from __future__ import annotations

from typing import Any, List

from smithers_py import (
    LoopNode,
    MergeQueueNode,
    ParallelNode,
    SequenceNode,
    TaskNode,
    WorkflowNode,
    WorktreeNode,
    create_smithers,
)

from ..components.agents import agents_for_repo
from ..components.porting_rules import stable_node_id
from ..components.schemas import (
    MergeResult,
    PhaseDone,
    TestAreaResult,
    TestSwarmInput,
    TestSwarmReport,
)


CONFIG = create_smithers(
    schemas={
        "input": TestSwarmInput,
        "testAreaResult": TestAreaResult,
        "mergeResult": MergeResult,
        "testSwarmReport": TestSwarmReport,
        "output": PhaseDone,
    }
)
outputs = CONFIG.outputs


@CONFIG.workflow
def test_swarm(ctx: Any) -> WorkflowNode:
    agents = agents_for_repo(ctx.input.repo)
    areas = [a.model_dump() for a in ctx.input.areas]

    area_branches: List[Any] = []
    for a in areas:
        aid = a["id"]
        nid = stable_node_id(aid)
        run_loop = LoopNode(
            id=f"test-swarm:{nid}:loop",
            maxIterations=max(1, ctx.input.maxIterations),
            until=lambda c_ctx, n=nid: (
                (c_ctx.output(f"test-swarm:{n}:run") or {}).get("allPass", False)
            ),
            onMaxReached="return-last",
            children=[
                TaskNode(
                    id=f"test-swarm:{nid}:run",
                    output=outputs.testAreaResult,
                    agent=agents["testAreaWorker"],
                    prompt=f"AREA: {aid}\nGLOB: {a.get('glob', '')}\nBRANCH: bun-port/{aid}",
                )
            ],
        )
        if ctx.input.useWorktrees:
            area_branches.append(
                WorktreeNode(
                    id=f"test-swarm:{nid}:wt",
                    path=f"./.tmp/bun-port-{aid}",
                    branch=f"bun-port/{aid}",
                    baseBranch=ctx.input.baseBranch,
                    children=[run_loop],
                )
            )
        else:
            area_branches.append(run_loop)

    parallel_areas = ParallelNode(
        max_concurrency=ctx.input.maxConcurrency,
        children=area_branches,
    )

    merge_children: List[TaskNode] = []
    for a in areas:
        aid = a["id"]
        nid = stable_node_id(aid)
        merge_children.append(
            TaskNode(
                id=f"test-swarm:{nid}:merge",
                output=outputs.mergeResult,
                agent=agents["mergeAgent"],
                prompt=f"SUBJECT: {aid}",
            )
        )

    merge_queue = MergeQueueNode(
        id="test-swarm:merge-queue",
        max_concurrency=1,
        base_branch=ctx.input.baseBranch,
        require_green=ctx.input.requireGreenBeforeMerge,
        children=merge_children,
    )

    def _report() -> dict:
        all_pass = partial = merged = 0
        for a in areas:
            nid = stable_node_id(a["id"])
            r = ctx.output(f"test-swarm:{nid}:run") or {}
            if r.get("allPass"):
                all_pass += 1
            else:
                partial += 1
            if ctx.output(f"test-swarm:{nid}:merge"):
                merged += 1
        return {
            "areas": len(areas),
            "allPass": all_pass,
            "partial": partial,
            "merged": merged,
            "summary": f"Test swarm: {all_pass}/{len(areas)} areas green, {merged} merged.",
        }

    report_task = TaskNode(
        id="test-swarm:report",
        output=outputs.testSwarmReport,
        render=_report,
    )

    def _emit_done() -> dict:
        rep = ctx.output("test-swarm:report") or {}
        status = "completed" if rep.get("partial", 0) == 0 else "partial"
        return {"phase": "tests", "status": status, "summary": rep.get("summary", "Tests finished.")}

    output_task = TaskNode(
        id="test-swarm:output",
        output=outputs.output,
        render=_emit_done,
    )

    return WorkflowNode(
        name="bun-port-py-tests",
        children=[
            SequenceNode(
                children=[parallel_areas, merge_queue, report_task, output_task]
            )
        ],
    )


__all__ = ["test_swarm", "CONFIG", "outputs"]
