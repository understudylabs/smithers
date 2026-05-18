# `smithers_py.scorers` — evaluation hooks for task outputs

Five built-in scorers plus generic LLM-judge builders. Mirrors
upstream Smithers' scorer surface (/llms-core.txt#scoring-tasks).

## Public surface

```python
from smithers_py.scorers import (
    ScoreResult, ScorerInput, Scorer,
    ScorerBinding, SamplingConfig, ScorersMap,
    schema_adherence_scorer,
    latency_scorer,
    relevancy_scorer,
    toxicity_scorer,
    faithfulness_scorer,
    llm_judge,
    create_scorer,
    run_scorers_async,
    aggregate,
    ScoreLog,
)

bindings = {
    "schema": ScorerBinding(scorer=schema_adherence_scorer()),
    "latency": ScorerBinding(scorer=latency_scorer(target_ms=5000)),
    "quality": ScorerBinding(
        scorer=llm_judge(judge=my_judge_fn, prompt="Rate 0-1..."),
        sampling=SamplingConfig(kind="ratio", rate=0.1),
    ),
}
result = await run_scorers_async(bindings, ScorerInput(output=..., latency_ms=...))
```

## Types

```python
class ScoreResult(BaseModel):
    score: float = Field(..., ge=0.0, le=1.0)
    reason: Optional[str] = None
    meta: Optional[dict[str, Any]] = None

class ScorerInput(BaseModel):
    input: Any = None
    output: Any = None
    ground_truth: Any = None
    context: Any = None
    latency_ms: Optional[int] = None
    output_schema: Any = None
    # arbitrary_types_allowed = True

class Scorer(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def name(self) -> str: ...
    @property
    def description(self) -> str: ...
    async def score(self, input: ScorerInput) -> ScoreResult: ...

@dataclass
class SamplingConfig:
    kind: Literal["all", "ratio", "none"] = "all"
    rate: float = 1.0
    def should_fire(self, rng=None) -> bool: ...

@dataclass
class ScorerBinding:
    scorer: Scorer
    sampling: SamplingConfig = field(default_factory=SamplingConfig)

ScorersMap = dict[str, ScorerBinding]
```

## Built-in scorers (all return [0, 1])

### `schema_adherence_scorer()`
Validates `ScorerInput.output` against `ScorerInput.output_schema`
(Pydantic class). 1.0 on pass, 0.0 on `ValidationError` with the
errors list captured in `.meta["errors"]`. Returns 1.0 with reason
"no schema declared" when schema is None.

### `latency_scorer(*, target_ms)`
Exponential decay around `target_ms`. 1.0 at or below target; every
additional `target_ms` halves the score:
`score = exp(-(over / target_ms) * ln(2))` clamped to [0, 1].
Returns 1.0 with reason "no latency_ms; pass" when latency not set.
Raises `ValueError` if `target_ms <= 0`.

### `relevancy_scorer(*, embed)`
Cosine similarity between embedded input and output. Maps from
[-1, 1] to [0, 1]. `embed` is a callable
`Callable[[list[str]], Awaitable[list[list[float]]]]`. Returns 0.5
when input or output missing.

### `toxicity_scorer(*, judge)` and `faithfulness_scorer(*, judge)`
LLM-judge scorers. `judge` is
`Callable[[str], Awaitable[str]]` — takes the rendered prompt,
returns text. Prompt asks for 0-1 score; 0-1 number extracted from
the response (first match in [0, 1]). Falls back to 0.5 with reason
"no parseable score" if no number found.

Faithfulness uses `ScorerInput.ground_truth` in its prompt.

### `llm_judge(*, judge, prompt, id="llm-judge", name="LLM Judge", description=...)`
Generic LLM-judge factory. `prompt` is a template string with
`{input}`, `{output}`, `{ground_truth}`, `{context}` placeholders.

### `create_scorer(*, id, name, description, judge, criteria, examples=None)`
Criteria-based judge factory. `criteria` describes what to evaluate;
`examples` is a list of `{input, output, score, explanation}` rows
folded into the prompt as few-shot anchors.

## `run_scorers_async`

```python
async def run_scorers_async(
    bindings: ScorersMap,
    input: ScorerInput,
    *,
    log: Optional[ScoreLog] = None,
    run_id: Optional[str] = None,
    node_id: Optional[str] = None,
    iteration: int = 0,
    attempt: int = 0,
) -> RunScorersResult: ...
```

Fires every binding whose `sampling.should_fire()` returns True
concurrently via `asyncio.gather`. Catches per-binding errors so one
failing scorer doesn't sink others — error message lands in
`result.errors[key]`. Persists when `log + run_id + node_id` provided.

```python
@dataclass
class RunScorersResult:
    results: dict[str, ScoreResult]
    skipped: list[str]
    errors: dict[str, str]
```

## `aggregate`

```python
@dataclass
class AggregateScore:
    mean: float
    minimum: float
    by_name: dict[str, float]
    pass_count: int  # scorers with score >= threshold
    total: int

def aggregate(results: dict[str, ScoreResult], *, pass_threshold: float = 0.5) -> AggregateScore: ...
```

Returns `AggregateScore(mean=1.0, minimum=1.0, by_name={}, pass_count=0, total=0)`
for empty results.

## SQLite persistence — `ts_scores`

```sql
CREATE TABLE ts_scores (
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    iteration INTEGER NOT NULL DEFAULT 0,
    attempt INTEGER NOT NULL DEFAULT 0,
    scorer_id TEXT NOT NULL,
    scorer_name TEXT NOT NULL,
    score REAL NOT NULL,
    reason TEXT,
    meta_json TEXT,
    started_at_ms INTEGER NOT NULL,
    finished_at_ms INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'success',
    error_json TEXT,
    PRIMARY KEY (run_id, node_id, iteration, attempt, scorer_id)
);
```

`ScoreLog` class wraps it (init schema on first connect, WAL mode):

- `record(row: ScoreRow)` — single-row insert
- `list_for_run(run_id, *, node_id=None) -> list[ScoreRow]`

## Files to produce

- `__init__.py` — public exports
- `types.py` — Pydantic + dataclass types listed above
- `builtins.py` — 5 built-in scorers + `llm_judge` + `create_scorer`,
  the `_parse_score_from_response` helper, `_cosine` helper
- `runner.py` — `run_scorers_async`, `aggregate`, `ScoreLog`,
  `RunScorersResult`, `AggregateScore`
- `test_scorers.py` — pytest-asyncio + tempfile fixtures. Cover:
  every built-in (pass/fail/edge cases), latency math (under target,
  at target, 2x over → 0.5), relevancy with stub embedding,
  `llm_judge` response parsing (clean number, embedded in prose,
  fallback to 0.5 on garbage), `create_scorer` with criteria +
  examples (verify prompt contains criteria text), sampling modes
  (all/none/ratio at rate=0 and rate=1), `run_scorers_async`
  fires-all / skips-none / isolates-errors, aggregate empty + non-
  empty, `ScoreLog` persists success and error rows.
