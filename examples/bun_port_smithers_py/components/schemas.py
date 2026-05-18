"""Pydantic mirrors of every Zod schema in the upstream bun-port example.

Field names match the upstream Zod schemas in
``examples/bun-port-smithers/components/schemas.ts`` so a workflow
authored on one runtime can be re-read on the other.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

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


# ----- Shared shapes ----------------------------------------------------------


class ZigFileInput(BaseModel):
    zig: str
    loc: int = Field(default=0, ge=0)
    crate: Optional[str] = None


class Issue(BaseModel):
    severity: Literal["must-fix", "should-fix", "nit"]
    rule: str
    detail: str
    fix: Optional[str] = None


class Review(BaseModel):
    subject: str
    reviewer: str = "reviewer"
    approved: bool = False
    ok: bool
    issues: List[Issue] = Field(default_factory=list)
    feedback: str = ""


class Approval(BaseModel):
    approved: bool = False
    note: Optional[str] = None
    decidedBy: Optional[str] = None
    decidedAt: Optional[str] = None


# ----- Phase 1: lifetime classification --------------------------------------


LifetimeClass = Literal[
    "OWNED", "SHARED", "BORROW_PARAM", "BORROW_FIELD", "STATIC",
    "JSC_BORROW", "BACKREF", "INTRUSIVE", "FFI", "ARENA", "UNKNOWN",
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


class LifetimeInput(BaseModel):
    repo: str = "."
    files: List[ZigFileInput] = Field(default_factory=list)
    sampleRate: float = Field(default=0.12, ge=0, le=1)
    unknownApprovalThreshold: float = Field(default=0.05, ge=0, le=1)
    portingRevision: str = ""
    lifetimeRevision: str = ""


class LifetimeClassification(BaseModel):
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
    totalFields: int = Field(..., ge=0)
    selectedCount: int = Field(..., ge=0)
    selected: List[LifetimeSelectionRow]


class LifetimeVote(BaseModel):
    key: str
    voter: str
    refuted: bool
    correctClass: str
    reason: str


class LifetimeSummary(BaseModel):
    totalFields: int = Field(..., ge=0)
    unknownRate: float
    verifiedCount: int = Field(..., ge=0)
    overturned: int = Field(..., ge=0)
    byClass: Dict[str, int] = Field(default_factory=dict)
    tsvPreview: str
    tsv: str
    refutedKeys: List[str] = Field(default_factory=list)


# ----- Phase A: per-file port -------------------------------------------------


class PhaseAInput(BaseModel):
    repo: str = "."
    files: List[ZigFileInput] = Field(default_factory=list)
    maxConcurrency: int = Field(default=8, gt=0)


class PhaseAPlanFile(BaseModel):
    zig: str
    rs: str
    loc: int = Field(default=0, ge=0)
    crate: str


class PhaseAPlan(BaseModel):
    total: int = Field(..., ge=0)
    files: List[PhaseAPlanFile]


class PhaseAImplement(BaseModel):
    zig: str
    rs: str
    status: Literal["drafted", "skipped", "failed"]
    confidence: Literal["high", "medium", "low"]
    todos: int = Field(..., ge=0)
    rsLoc: int = Field(..., ge=0)
    note: str


class PhaseAFix(BaseModel):
    zig: str
    rs: str
    applied: int = Field(..., ge=0)
    remaining: int = Field(..., ge=0)
    note: str


class PhaseAReport(BaseModel):
    total: int = Field(..., ge=0)
    clean: int = Field(..., ge=0)
    fixed: int = Field(..., ge=0)
    failed: int = Field(..., ge=0)
    todoCount: int = Field(..., ge=0)
    summary: str


# ----- Phase: crate compile bring-up -----------------------------------------


class CrateSpec(BaseModel):
    name: str
    tier: int = Field(default=0, ge=0)


class CrateCompileInput(BaseModel):
    repo: str = "."
    crates: List[CrateSpec] = Field(default_factory=list)
    maxRounds: int = Field(default=25, gt=0)
    broadGateApprovalThreshold: int = Field(default=20, ge=0)


class CrateTier(BaseModel):
    tier: int = Field(..., ge=0)
    crates: List[CrateSpec]


class CratePlan(BaseModel):
    tiers: List[CrateTier]
    totalCrates: int = Field(..., ge=0)


class CrateCheck(BaseModel):
    crate: str
    tier: int = Field(..., ge=0)
    compiles: bool
    errorCount: int = Field(..., ge=0)
    rounds: int = Field(..., ge=0)
    gatedModules: List[str] = Field(default_factory=list)
    blockedOn: List[str] = Field(default_factory=list)
    notes: str


class CompileReport(BaseModel):
    totalCrates: int = Field(..., ge=0)
    green: int = Field(..., ge=0)
    failing: int = Field(..., ge=0)
    gatedModules: int = Field(..., ge=0)
    greenCrates: List[str] = Field(default_factory=list)
    failingCrates: List[str] = Field(default_factory=list)
    summary: str


# ----- Phase: ungate / proper-port -------------------------------------------


class UngateTarget(BaseModel):
    id: str
    crate: str
    file: str
    reason: str = "ungate/proper-port"


class UngateInput(BaseModel):
    repo: str = "."
    targets: List[UngateTarget] = Field(default_factory=list)
    maxRounds: int = Field(default=5, gt=0)


class TargetSurveyRow(BaseModel):
    id: str
    crate: str
    file: str
    reason: str


class TargetSurvey(BaseModel):
    totalTargets: int = Field(..., ge=0)
    targets: List[TargetSurveyRow]


class PatchResult(BaseModel):
    targetId: str
    status: Literal["patched", "skipped", "failed"]
    filesChanged: List[str] = Field(default_factory=list)
    summary: str


class SpecReview(BaseModel):
    targetId: str
    reviewer: str
    approved: bool
    issues: List[Issue] = Field(default_factory=list)
    feedback: str


class SpecDecision(BaseModel):
    targetId: str
    approved: bool
    approvals: int = Field(..., ge=0)
    rejections: int = Field(..., ge=0)
    issues: List[Issue] = Field(default_factory=list)
    feedback: str = ""


class UngateReport(BaseModel):
    totalTargets: int = Field(..., ge=0)
    patched: int = Field(..., ge=0)
    approved: int = Field(..., ge=0)
    rejected: int = Field(..., ge=0)
    summary: str


# ----- Phase: panic probe swarm ----------------------------------------------


class ProbeSpec(BaseModel):
    id: str
    cmd: str
    expect: Optional[str] = None


class ProbeInput(BaseModel):
    repo: str = "."
    probes: List[ProbeSpec] = Field(default_factory=list)
    maxRounds: int = Field(default=5, gt=0)


class BuildResult(BaseModel):
    ok: bool
    command: str
    summary: str


class ProbeResult(BaseModel):
    probeId: str
    command: str
    passed: bool
    panicLocation: Optional[str] = None
    assertion: Optional[str] = None
    signal: Optional[str] = None
    durationMs: int = Field(..., ge=0)
    output: str


class FailureRow(BaseModel):
    failureKey: str
    probeId: str
    command: str
    panicLocation: Optional[str] = None
    assertion: Optional[str] = None


class FailureSet(BaseModel):
    totalFailures: int = Field(..., ge=0)
    failures: List[FailureRow]


class FailureFix(BaseModel):
    failureKey: str
    status: Literal["fixed", "skipped", "failed"]
    filesChanged: List[str] = Field(default_factory=list)
    summary: str


class ProbeReport(BaseModel):
    totalProbes: int = Field(..., ge=0)
    passed: int = Field(..., ge=0)
    uniqueFailures: int = Field(..., ge=0)
    fixes: int = Field(..., ge=0)
    summary: str


# ----- Phase: test swarm ------------------------------------------------------


class TestArea(BaseModel):
    id: str
    glob: str
    crate: str


class TestSwarmInput(BaseModel):
    repo: str = "."
    baseBranch: str = "main"
    useWorktrees: bool = True
    maxIterations: int = Field(default=30, gt=0)
    maxConcurrency: int = Field(default=8, gt=0)
    requireGreenBeforeMerge: bool = True
    awaitExternalCiSignal: bool = False
    ciCorrelationId: str = "bun-port-test-swarm"
    areas: List[TestArea] = Field(default_factory=list)


class TestAreaResult(BaseModel):
    areaId: str
    pass_: int = Field(..., ge=0, alias="pass")
    fail: int = Field(..., ge=0)
    total: int = Field(..., ge=0)
    allPass: bool
    bughuntBugs: int = Field(default=0, ge=0)
    commits: List[str] = Field(default_factory=list)
    branch: str
    notes: str
    model_config = {"populate_by_name": True}


class MergeResult(BaseModel):
    id: str
    picked: int = Field(..., ge=0)
    conflicts: int = Field(default=0, ge=0)
    notes: str


class CiSignal(BaseModel):
    status: Literal["passed", "failed", "cancelled"]
    url: str = ""
    summary: str = ""


class TestSwarmReport(BaseModel):
    areas: int = Field(..., ge=0)
    allPass: int = Field(..., ge=0)
    partial: int = Field(..., ge=0)
    merged: int = Field(..., ge=0)
    summary: str


# ----- Phase: audit sweeps ----------------------------------------------------


class SweepSpec(BaseModel):
    id: str
    kind: str
    pattern: str
    scope: str


class SweepInput(BaseModel):
    repo: str = "."
    sweeps: List[SweepSpec] = Field(default_factory=list)


class SweepSurvey(BaseModel):
    sweeps: List[SweepSpec]
    total: int = Field(..., ge=0)


class SweepResult(BaseModel):
    sweepId: str
    kind: str
    candidates: int = Field(..., ge=0)
    fixed: int = Field(..., ge=0)
    skipped: int = Field(..., ge=0)
    summary: str


class SweepReport(BaseModel):
    totalSweeps: int = Field(..., ge=0)
    fixed: int = Field(..., ge=0)
    skipped: int = Field(..., ge=0)
    summary: str


# ----- Top-level / operator -------------------------------------------------


class OperatorPlan(BaseModel):
    approved: bool
    comments: str = ""
    runLifetimes: bool = True
    runPhaseA: bool = True
    runCompile: bool = True
    runUngate: bool = True
    runProbes: bool = True
    runTests: bool = True
    runSweeps: bool = True


class PhaseDone(BaseModel):
    """Generic per-phase output. Each phase Subflow emits one of these."""

    model_config = {"extra": "allow"}

    phase: str
    status: Literal["completed", "partial", "failed"] = "completed"
    summary: str


class BunPortInput(BaseModel):
    repo: str = "."
    phases: List[WorkflowPhase] = Field(
        default_factory=lambda: [
            "lifetimes", "phaseA", "compile", "ungate", "probes", "tests", "sweeps",
        ]
    )
    requireOperatorPlan: bool = True
    baseBranch: str = "main"
    files: List[ZigFileInput] = Field(default_factory=list)
    crates: List[CrateSpec] = Field(default_factory=list)
    targets: List[UngateTarget] = Field(default_factory=list)
    probes: List[ProbeSpec] = Field(default_factory=list)
    areas: List[TestArea] = Field(default_factory=list)
    sweeps: List[SweepSpec] = Field(default_factory=list)
    maxConcurrency: int = Field(default=8, gt=0)
    useWorktrees: bool = True
    awaitExternalCiSignal: bool = False
    unknownApprovalThreshold: float = Field(default=0.05, ge=0, le=1)
    broadGateApprovalThreshold: int = Field(default=20, ge=0)


class BunPortFinal(BaseModel):
    status: Literal["completed", "cancelled", "partial"] = "completed"
    phasesRun: List[str] = Field(default_factory=list)
    summary: str
    nextActions: List[str] = Field(default_factory=list)


class ApprovalRow(BaseModel):
    """Wire-compat approval shape — matches TS Drizzle approval row."""

    model_config = {"extra": "allow"}

    approved: bool


__all__ = [
    "ApprovalRow", "Approval", "BuildResult", "BunPortFinal", "BunPortInput",
    "CiSignal", "CompileReport", "CratePlan", "CrateCheck", "CrateCompileInput",
    "CrateSpec", "CrateTier", "FailureFix", "FailureRow", "FailureSet",
    "Issue", "LifetimeClassification", "LifetimeField", "LifetimeInput",
    "LifetimeSelection", "LifetimeSelectionRow", "LifetimeSummary",
    "LifetimeVote", "MergeResult", "OperatorPlan", "PatchResult", "PhaseAFix",
    "PhaseAImplement", "PhaseAInput", "PhaseAPlan", "PhaseAPlanFile",
    "PhaseAReport", "PhaseDone", "ProbeInput", "ProbeReport", "ProbeResult",
    "ProbeSpec", "Review", "SpecDecision", "SpecReview", "SweepInput",
    "SweepReport", "SweepResult", "SweepSpec", "SweepSurvey",
    "TargetSurvey", "TargetSurveyRow", "TestArea", "TestAreaResult",
    "TestSwarmInput", "TestSwarmReport", "UngateInput", "UngateReport",
    "UngateTarget", "WorkflowPhase", "ZigFileInput",
]
