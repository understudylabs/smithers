"""Pydantic mirrors of the Zod schemas in examples/bun-port-smithers/components/schemas.ts.

Each schema preserves the same field names and validation rules as its TS
counterpart so a graph authored on one runtime can be re-read on the other.

Two compatibility notes baked in:

1. Top-level floats in TS Smithers become INTEGER columns; rates are nested
   under ``metrics`` so they live in JSON columns. The Python side honors the
   same convention even though Pydantic has no such constraint, because the
   acceptance criterion for the port is *row-shape equality*, not
   "what Pydantic alone would have validated."
2. Reserved column names ``run_id``, ``node_id``, ``iteration`` MUST NOT
   appear as top-level fields on any output schema. We follow that rule.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# ----- Workflow phases --------------------------------------------------------

WorkflowPhase = Literal[
    "lifetimes",
    "phaseA",
    "compile",
    "ungate",
    "probes",
    "tests",
    "sweeps",
]


class ZigFileInput(BaseModel):
    zig: str
    loc: int = Field(default=0, ge=0)
    crate: Optional[str] = None


class LifetimeInput(BaseModel):
    repo: str = "."
    files: List[ZigFileInput] = Field(default_factory=list)
    sampleRate: float = Field(default=0.12, ge=0, le=1)
    unknownApprovalThreshold: float = Field(default=0.05, ge=0, le=1)
    portingRevision: str = ""
    lifetimeRevision: str = ""


# ----- Lifetime classification (Phase 1 — the birds-eye memory model) --------


LifetimeClass = Literal[
    "OWNED",
    "SHARED",
    "BORROW_PARAM",
    "BORROW_FIELD",
    "STATIC",
    "JSC_BORROW",
    "BACKREF",
    "INTRUSIVE",
    "FFI",
    "ARENA",
    "UNKNOWN",
]


class LifetimeField(BaseModel):
    struct: str
    field: str
    zigType: str
    class_: LifetimeClass = Field(..., alias="class")
    rustType: str
    evidence: str
    confidence: Literal["high", "low"]

    model_config = {"populate_by_name": True}


class LifetimeClassification(BaseModel):
    schema_version: Literal["smithers-bun-port-py-lifetime-classification-v0"] = (
        "smithers-bun-port-py-lifetime-classification-v0"
    )
    file: str
    crate: str
    fields: List[LifetimeField]


class LifetimeSelectionRow(BaseModel):
    key: str
    file: str
    struct: str
    field: str
    class_: str = Field(..., alias="class")
    rustType: str

    model_config = {"populate_by_name": True}


class LifetimeSelection(BaseModel):
    schema_version: Literal["smithers-bun-port-py-lifetime-selection-v0"] = (
        "smithers-bun-port-py-lifetime-selection-v0"
    )
    totalFields: int = Field(..., ge=0)
    selectedCount: int = Field(..., ge=0)
    selected: List[LifetimeSelectionRow]


class LifetimeVote(BaseModel):
    schema_version: Literal["smithers-bun-port-py-lifetime-vote-v0"] = (
        "smithers-bun-port-py-lifetime-vote-v0"
    )
    key: str
    voter: str
    refuted: bool
    correctClass: str
    reason: str


class LifetimeSummaryMetrics(BaseModel):
    """All fractional metrics live in this nested object so they map to a
    JSON column on the TS side and we don't trip the float→INTEGER trap."""

    unknownRate: float = Field(..., ge=0, le=1)


class LifetimeSummary(BaseModel):
    schema_version: Literal["smithers-bun-port-py-lifetime-summary-v0"] = (
        "smithers-bun-port-py-lifetime-summary-v0"
    )
    totalFields: int = Field(..., ge=0)
    verifiedCount: int = Field(..., ge=0)
    overturned: int = Field(..., ge=0)
    refutedKeys: List[str]
    tsvPreview: str
    tsv: str
    metrics: LifetimeSummaryMetrics


class PhaseDone(BaseModel):
    """Generic 'child workflow finished' shape used by Subflows.

    Carries the per-phase summary plus enough scalar metrics for the parent
    workflow's gates to fire on (e.g. ApprovalGate when unknownRate > 5%).
    """

    schema_version: Literal["smithers-bun-port-py-phase-done-v0"] = (
        "smithers-bun-port-py-phase-done-v0"
    )
    phase: WorkflowPhase
    status: Literal["completed", "partial", "skipped", "cancelled"]
    summary: str
    metrics: LifetimeSummaryMetrics
    totalFields: int = 0
    refutedKeys: List[str] = Field(default_factory=list)


# ----- Top-level workflow contract --------------------------------------------


class BunPortInput(BaseModel):
    repo: str = "."
    phases: List[WorkflowPhase] = Field(
        default_factory=lambda: [
            "lifetimes",
            "phaseA",
            "compile",
            "ungate",
            "probes",
            "tests",
            "sweeps",
        ]
    )
    requireOperatorPlan: bool = False
    baseBranch: str = "main"
    files: List[ZigFileInput] = Field(default_factory=list)
    useWorktrees: bool = True
    awaitExternalCiSignal: bool = False
    unknownApprovalThreshold: float = Field(default=0.05, ge=0, le=1)
    broadGateApprovalThreshold: int = Field(default=20, ge=0)
    maxConcurrency: int = Field(default=8, ge=1)


class OperatorPlan(BaseModel):
    approved: bool
    comments: Optional[str] = None
    runLifetimes: bool = True
    runPhaseA: bool = True
    runCompile: bool = True
    runUngate: bool = True
    runProbes: bool = True
    runTests: bool = True
    runSweeps: bool = True


class Approval(BaseModel):
    approved: bool
    note: str = ""


class BunPortFinal(BaseModel):
    schema_version: Literal["smithers-bun-port-py-final-v0"] = (
        "smithers-bun-port-py-final-v0"
    )
    status: Literal["completed", "cancelled", "partial"]
    phasesRun: List[WorkflowPhase]
    summary: str
    nextActions: List[str] = Field(default_factory=list)
