"""Phase 1 — Lifetime classification.

Mirrors examples/bun-port-smithers/workflows/lifetime-classify.tsx.
"""

from __future__ import annotations

from typing import Any, Dict, List

from smithers_py import (
    ParallelNode,
    SequenceNode,
    TaskNode,
    WorkflowNode,
    create_smithers,
)

from ..components.agents import agents_for_repo
from ..components.porting_rules import (
    cache_key_for_file,
    field_key,
    lifetime_tsv,
    select_lifetime_verification_rows,
    stable_node_id,
    summarize_lifetime_rows,
    tsv_preview,
)
from ..components.schemas import (
    LifetimeClassification,
    LifetimeInput,
    LifetimeSelection,
    LifetimeSummary,
    LifetimeVote,
    PhaseDone,
)


CONFIG = create_smithers(
    schemas={
        "input": LifetimeInput,
        "lifetimeClassification": LifetimeClassification,
        "lifetimeSelection": LifetimeSelection,
        "lifetimeVote": LifetimeVote,
        "lifetimeSummary": LifetimeSummary,
        "output": PhaseDone,
    }
)
outputs = CONFIG.outputs


@CONFIG.workflow
def lifetime_classify(ctx: Any) -> WorkflowNode:
    files: List[Dict[str, Any]] = [f.model_dump() for f in ctx.input.files]
    agents = agents_for_repo(ctx.input.repo)

    classify_tasks: List[TaskNode] = []
    for f in files:
        zig = f["zig"]
        crate = f.get("crate") or ""
        ck = cache_key_for_file(
            repo=ctx.input.repo,
            zig=zig,
            crate=crate,
            porting_revision=ctx.input.portingRevision,
            lifetime_revision=ctx.input.lifetimeRevision,
        )
        classify_tasks.append(
            TaskNode(
                id=f"lifetime:classify:{stable_node_id(zig)}",
                output=outputs.lifetimeClassification,
                agent=agents["lifetimeClassifier"],
                prompt=f"ZIG: {zig}\nCRATE: {crate}\nCACHE_KEY: {ck}",
            )
        )

    def _all_fields() -> List[dict]:
        rows: List[dict] = []
        for t in classify_tasks:
            payload = ctx.output(t.id)
            if not payload:
                continue
            for f in (payload.get("fields") or []):
                rows.append({**f, "file": payload["file"], "crate": payload["crate"]})
        return rows

    def _select_rows() -> dict:
        rows = _all_fields()
        selected = select_lifetime_verification_rows(rows, ctx.input.sampleRate)
        return {
            "totalFields": len(rows),
            "selectedCount": len(selected),
            "selected": [
                {
                    "key": field_key(s),
                    "file": s["file"],
                    "struct": s["struct"],
                    "field": s["field"],
                    "class": s.get("class") or s.get("class_") or "",
                    "rustType": s.get("rustType", ""),
                }
                for s in selected
            ],
        }

    select_task = TaskNode(
        id="lifetime:select-verify",
        output=outputs.lifetimeSelection,
        render=_select_rows,
    )

    def _synthesize() -> dict:
        rows = _all_fields()
        base = summarize_lifetime_rows(rows)
        tsv = lifetime_tsv(rows)
        return {
            "totalFields": base["totalFields"],
            "unknownRate": base["unknownRate"],
            "verifiedCount": 0,
            "overturned": 0,
            "byClass": base["byClass"],
            "tsvPreview": tsv_preview(tsv),
            "tsv": tsv,
            "refutedKeys": [],
        }

    synthesize_task = TaskNode(
        id="lifetime:synthesize",
        output=outputs.lifetimeSummary,
        render=_synthesize,
    )

    def _emit_phase_done() -> dict:
        summary = ctx.output("lifetime:synthesize") or {}
        return {
            "phase": "lifetimes",
            "status": "completed",
            "summary": (
                f"Lifetime classification produced {summary.get('totalFields', 0)} "
                f"field row(s); UNKNOWN rate {summary.get('unknownRate', 0.0):.3f}"
            ),
        }

    output_task = TaskNode(
        id="lifetime:output",
        output=outputs.output,
        render=_emit_phase_done,
    )

    return WorkflowNode(
        name="bun-port-py-lifetime-classify",
        children=[
            SequenceNode(
                children=[
                    ParallelNode(
                        max_concurrency=max(1, len(classify_tasks) or 1),
                        children=classify_tasks,
                    ),
                    select_task,
                    synthesize_task,
                    output_task,
                ]
            )
        ],
    )


__all__ = ["lifetime_classify", "CONFIG", "outputs"]
