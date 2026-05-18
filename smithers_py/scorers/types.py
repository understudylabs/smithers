"""Shared types for the scorers subsystem.

Mirrors the upstream Smithers scorer surface (/llms-core.txt#scorers
and the ``smithers-orchestrator/scorers`` package). Every scorer
returns a float in [0, 1] alongside optional ``reason`` text and
arbitrary ``meta`` data persisted to the score row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class ScoreResult(BaseModel):
    """One scoring outcome. ``score`` is in [0, 1] by convention."""

    score: float = Field(..., ge=0.0, le=1.0)
    reason: Optional[str] = None
    meta: Optional[dict[str, Any]] = None


class ScorerInput(BaseModel):
    """Input passed to ``Scorer.score``. Fields are optional so a single
    scorer can be reused across tasks that produce different shapes.

    - ``input``: the workflow / task input
    - ``output``: the value the task produced (already schema-validated)
    - ``ground_truth``: optional reference value for faithfulness/etc.
    - ``context``: arbitrary additional context (e.g., a retrieval result)
    - ``latency_ms``: how long the task took
    - ``output_schema``: the Zod-equivalent Pydantic schema for schema
      adherence scoring
    """

    input: Any = None
    output: Any = None
    ground_truth: Any = None
    context: Any = None
    latency_ms: Optional[int] = None
    output_schema: Any = None

    model_config = {"arbitrary_types_allowed": True}


ScorerFn = Callable[[ScorerInput], Awaitable[ScoreResult]]
"""Signature for the user-supplied scoring function in ``create_scorer``."""


@runtime_checkable
class Scorer(Protocol):
    """Interface every scorer implements. Built-ins and customs both fit."""

    @property
    def id(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    async def score(self, input: ScorerInput) -> ScoreResult: ...


SamplingKind = Literal["all", "ratio", "none"]


@dataclass
class SamplingConfig:
    """Controls how often a scorer fires for a given task.

    - ``all``: every invocation
    - ``ratio``: rate is the fire probability (0.0..1.0)
    - ``none``: never; useful for disabling a scorer without removing
      its binding
    """

    kind: SamplingKind = "all"
    rate: float = 1.0

    def should_fire(self, rng: Optional[Callable[[], float]] = None) -> bool:
        if self.kind == "all":
            return True
        if self.kind == "none":
            return False
        # ratio
        if rng is None:
            import random as _random

            rng = _random.random
        return rng() < self.rate


@dataclass
class ScorerBinding:
    """A ``Scorer`` + a ``SamplingConfig``, as attached to a Task.

    Mirrors the TS shape:
    ``{ "latency": { "scorer": latencyScorer(...), "sampling": {...} } }``.
    """

    scorer: Scorer
    sampling: SamplingConfig = field(default_factory=SamplingConfig)


ScorersMap = dict[str, ScorerBinding]
"""Keyed bindings; key is the display name (``"latency"``, ``"schema"``,
…). Used as a Task prop."""


@dataclass
class ScoreRow:
    """One row in the persisted ``ts_scores`` table.

    Stored after every fired scorer invocation regardless of pass/fail.
    """

    run_id: str
    node_id: str
    iteration: int
    attempt: int
    scorer_id: str
    scorer_name: str
    score: float
    reason: Optional[str]
    meta_json: Optional[str]
    started_at_ms: int
    finished_at_ms: int
    status: str = "success"  # "success" | "error" | "skipped"
    error_json: Optional[str] = None
