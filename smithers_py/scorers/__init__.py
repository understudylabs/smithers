"""Scorers — evaluation hooks for task outputs.

Mirrors the upstream Smithers scorer surface
(/llms-core.txt#scoring-tasks and the
``smithers-orchestrator/scorers`` package). Attach scorers to a
``TaskNode`` via the ``scorers`` prop; the engine fires them after the
task completes and persists results to ``ts_scores``.

```python
from smithers_py.scorers import (
    ScorerBinding,
    SamplingConfig,
    latency_scorer,
    schema_adherence_scorer,
    llm_judge,
    create_scorer,
)

bindings = {
    "schema": ScorerBinding(scorer=schema_adherence_scorer()),
    "latency": ScorerBinding(scorer=latency_scorer(target_ms=5000)),
    "quality": ScorerBinding(
        scorer=llm_judge(judge=my_judge_fn, prompt="Rate 0-1..."),
        sampling=SamplingConfig(kind="ratio", rate=0.1),
    ),
}
```

Five built-in scorers:
- ``schema_adherence_scorer()`` — validate output against Pydantic schema.
- ``latency_scorer(target_ms=...)`` — exponential decay around target.
- ``relevancy_scorer(embed=...)`` — input/output embedding similarity.
- ``toxicity_scorer(judge=...)`` — LLM judge for output safety.
- ``faithfulness_scorer(judge=...)`` — LLM judge for ground-truth alignment.

Plus ``llm_judge(...)`` and ``create_scorer(...)`` for custom judges.
"""

from __future__ import annotations

from .builtins import (
    EmbedFn,
    JudgeFn,
    create_scorer,
    faithfulness_scorer,
    latency_scorer,
    llm_judge,
    relevancy_scorer,
    schema_adherence_scorer,
    toxicity_scorer,
)
from .runner import (
    AggregateScore,
    RunScorersResult,
    ScoreLog,
    aggregate,
    run_scorers_async,
)
from .types import (
    SamplingConfig,
    SamplingKind,
    ScoreResult,
    ScoreRow,
    Scorer,
    ScorerBinding,
    ScorerFn,
    ScorerInput,
    ScorersMap,
)

__all__ = [
    # types
    "SamplingConfig",
    "SamplingKind",
    "ScoreResult",
    "ScoreRow",
    "Scorer",
    "ScorerBinding",
    "ScorerFn",
    "ScorerInput",
    "ScorersMap",
    # builtins
    "EmbedFn",
    "JudgeFn",
    "create_scorer",
    "faithfulness_scorer",
    "latency_scorer",
    "llm_judge",
    "relevancy_scorer",
    "schema_adherence_scorer",
    "toxicity_scorer",
    # runner + persistence
    "AggregateScore",
    "RunScorersResult",
    "ScoreLog",
    "aggregate",
    "run_scorers_async",
]
