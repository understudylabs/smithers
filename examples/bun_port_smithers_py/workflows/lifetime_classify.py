"""Phase 1 — Lifetime classification, ported from
examples/bun-port-smithers/workflows/lifetime-classify.tsx.

Quoting Cory's tweet thread on why this phase comes first:

    "Step 1 is a Lifetime classifier. This is the most important part. The
    agents will have tunnel vision on whatever file they are looking at.
    This lifetime classifier will process the higher birds eye view of how
    memory is managed into a lookup table so the individual agents can
    efficiently lookup how they should handle memory."

This is the Python translation of the TS workflow that produces that
lookup table. It uses the TS-compatibility node types added to
``smithers_py`` on the ``port/resume`` branch.

The shape mirrors the TS reference 1:1:
    Sequence
      ├── Parallel (per-file lifetime classification, sampled cache keys)
      ├── select_lifetime_verification_rows (deterministic, no LLM)
      ├── Parallel (3 voters × N selected rows)
      └── synthesize TSV + UNKNOWN-rate gate input

When the engine learns to dispatch on TaskNode.agent + render, this will
run end-to-end. Today it builds a typed graph the engine can introspect.
"""

from __future__ import annotations

from typing import Any, Dict, List

from smithers_py import (
    ParallelNode,
    SequenceNode,
    SubflowNode,
    TaskNode,
    WorkflowNode,
    create_smithers,
)
from smithers_py.facade import SmithersConfig

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


def build_config() -> SmithersConfig:
    """One createSmithers per workflow file — mirrors the TS pattern of each
    sub-workflow declaring its own typed output map."""
    return create_smithers(
        schemas={
            "input": LifetimeInput,
            "lifetimeClassification": LifetimeClassification,
            "lifetimeSelection": LifetimeSelection,
            "lifetimeVote": LifetimeVote,
            "lifetimeSummary": LifetimeSummary,
            "output": PhaseDone,
        },
        db_path="smithers.db",
    )


CONFIG = build_config()
outputs = CONFIG.outputs


@CONFIG.workflow
def lifetime_classify(ctx: Any) -> WorkflowNode:
    """Build the lifetime-classify phase graph.

    ``ctx.input`` is a ``LifetimeInput`` (validated by the facade against
    the registered schema). The function returns the workflow tree; the
    engine consumes it.
    """
    files: List[Dict[str, Any]] = [f.model_dump() for f in ctx.input.files]
    agents = agents_for_repo(ctx.input.repo)

    # Per-file classification fan-out. Each Task is keyed by a deterministic
    # cache key so re-runs don't reclassify unchanged files.
    classify_tasks: List[TaskNode] = []
    for f in files:
        zig = f["zig"]
        crate = f.get("crate", "")
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
                timeout_ms=20 * 60_000,
                cache={"by": ck, "version": "v2"},
            )
        )

    # Deterministic selection step — no LLM. Reads whatever the per-file
    # classifications produced and samples for verification.
    def _select_verification_rows() -> dict:
        # In a real engine run this reads ctx.outputMaybe of every classify
        # task; for the structural port the function shape is what matters.
        all_fields: List[dict] = []  # populated by ctx in real run
        rows = select_lifetime_verification_rows(all_fields, ctx.input.sampleRate)
        return LifetimeSelection(
            totalFields=len(all_fields),
            selectedCount=len(rows),
            selected=[
                {
                    "key": field_key(field),
                    "file": field["file"],
                    "struct": field["struct"],
                    "field": field["field"],
                    "class": field.get("class") or field.get("class_", ""),
                    "rustType": field.get("rustType", ""),
                }
                for field in rows
            ],
        ).model_dump(by_alias=True)

    select_task = TaskNode(
        id="lifetime:select-verify",
        output=outputs.lifetimeSelection,
        render=_select_verification_rows,
    )

    # Three-voter verification fan-out happens at runtime once selection has
    # produced rows. We emit a placeholder ParallelNode the engine fills in
    # from outputs.lifetimeSelection.
    verify_parallel = ParallelNode(
        max_concurrency=max(1, ctx.input.maxConcurrency if hasattr(ctx.input, "maxConcurrency") else 8),
        children=[],
        # The engine reads the `expands_from` prop to know which output
        # row drives child generation. Convention only — no enforcement
        # at construction time.
        props={"expands_from": "lifetime:select-verify"},
    )

    # Synthesis step: assembles the final TSV + the UNKNOWN-rate gate input.
    def _synthesize() -> dict:
        all_fields: List[dict] = []  # populated by ctx in real run
        base = summarize_lifetime_rows(all_fields)
        tsv = lifetime_tsv(all_fields)
        return LifetimeSummary(
            totalFields=base["totalFields"],
            verifiedCount=0,
            overturned=0,
            refutedKeys=[],
            tsvPreview=tsv_preview(tsv),
            tsv=tsv,
            metrics={"unknownRate": base["unknownRate"]},
        ).model_dump()

    synthesize_task = TaskNode(
        id="lifetime:synthesize",
        output=outputs.lifetimeSummary,
        render=_synthesize,
    )

    # Phase output — exposes the bits the parent workflow's ApprovalGate
    # branches on.
    def _emit_phase_done() -> dict:
        summary = _synthesize()  # in a real run this would be ctx.output("lifetime:synthesize")
        return PhaseDone(
            phase="lifetimes",
            status="completed",
            summary=(
                f"Lifetime classification produced {summary['totalFields']} field row(s); "
                f"UNKNOWN rate {summary['metrics']['unknownRate']:.3f}"
            ),
            metrics=summary["metrics"],
            totalFields=summary["totalFields"],
            refutedKeys=summary["refutedKeys"],
        ).model_dump()

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
                    verify_parallel,
                    synthesize_task,
                    output_task,
                ]
            )
        ],
    )


__all__ = ["lifetime_classify", "CONFIG", "outputs"]
