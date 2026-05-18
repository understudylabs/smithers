"""Tests for the scorers subsystem."""

from __future__ import annotations

import os
import tempfile

import pytest
from pydantic import BaseModel

from smithers_py.scorers import (
    SamplingConfig,
    ScoreLog,
    ScorerBinding,
    ScorerInput,
    aggregate,
    create_scorer,
    faithfulness_scorer,
    latency_scorer,
    llm_judge,
    relevancy_scorer,
    run_scorers_async,
    schema_adherence_scorer,
    toxicity_scorer,
)


# ----- fixtures -------------------------------------------------------------


@pytest.fixture
def log_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        yield path
    finally:
        for suffix in ("", "-wal", "-shm"):
            cand = path + suffix
            if os.path.exists(cand):
                try:
                    os.unlink(cand)
                except OSError:
                    pass


# ----- schema adherence ----------------------------------------------------


class Demo(BaseModel):
    name: str
    count: int


@pytest.mark.asyncio
async def test_schema_adherence_pass():
    scorer = schema_adherence_scorer()
    result = await scorer.score(
        ScorerInput(output={"name": "a", "count": 1}, output_schema=Demo)
    )
    assert result.score == 1.0


@pytest.mark.asyncio
async def test_schema_adherence_fail():
    scorer = schema_adherence_scorer()
    result = await scorer.score(
        ScorerInput(output={"name": "a", "count": "not-an-int"}, output_schema=Demo)
    )
    assert result.score == 0.0
    assert result.meta is not None
    assert "errors" in result.meta


@pytest.mark.asyncio
async def test_schema_adherence_no_schema_passes():
    scorer = schema_adherence_scorer()
    result = await scorer.score(ScorerInput(output={"anything": True}))
    assert result.score == 1.0


# ----- latency -------------------------------------------------------------


@pytest.mark.asyncio
async def test_latency_under_target():
    scorer = latency_scorer(target_ms=1000)
    result = await scorer.score(ScorerInput(latency_ms=500))
    assert result.score == 1.0


@pytest.mark.asyncio
async def test_latency_at_target():
    scorer = latency_scorer(target_ms=1000)
    result = await scorer.score(ScorerInput(latency_ms=1000))
    assert result.score == 1.0


@pytest.mark.asyncio
async def test_latency_2x_over_target():
    scorer = latency_scorer(target_ms=1000)
    result = await scorer.score(ScorerInput(latency_ms=2000))
    # 1x over target = half the score
    assert abs(result.score - 0.5) < 0.01


@pytest.mark.asyncio
async def test_latency_no_data_passes():
    scorer = latency_scorer(target_ms=1000)
    result = await scorer.score(ScorerInput())
    assert result.score == 1.0


def test_latency_rejects_zero_target():
    with pytest.raises(ValueError):
        latency_scorer(target_ms=0)


# ----- relevancy -----------------------------------------------------------


async def _identical_embed(texts):
    """Stub: every text gets the same simple vector based on its first char."""
    return [[float(ord(t[0]) if t else 0), 1.0, 1.0] for t in texts]


@pytest.mark.asyncio
async def test_relevancy_high_when_inputs_similar():
    scorer = relevancy_scorer(embed=_identical_embed)
    # Both start with 'a' → identical first component → high cosine.
    result = await scorer.score(
        ScorerInput(input="apple", output="appendix")
    )
    assert result.score > 0.9


@pytest.mark.asyncio
async def test_relevancy_missing_inputs():
    scorer = relevancy_scorer(embed=_identical_embed)
    result = await scorer.score(ScorerInput(input=None, output="x"))
    assert result.score == 0.5


# ----- LLM judges ----------------------------------------------------------


async def _fixed_judge_response(text):
    """Always returns "0.8"."""
    return "0.8"


async def _verbose_judge(prompt):
    """Returns prose containing a number."""
    return "Looking at the output, I'd rate it 0.75 out of 1.0 because..."


@pytest.mark.asyncio
async def test_llm_judge_parses_clean_number():
    scorer = llm_judge(judge=_fixed_judge_response, prompt="Rate {output}")
    result = await scorer.score(ScorerInput(output="hello"))
    assert result.score == 0.8


@pytest.mark.asyncio
async def test_llm_judge_parses_number_from_prose():
    scorer = llm_judge(judge=_verbose_judge, prompt="Rate {output}")
    result = await scorer.score(ScorerInput(output="hello"))
    assert result.score == 0.75


@pytest.mark.asyncio
async def test_llm_judge_fallback_on_garbage():
    async def garbage(prompt):
        return "no number here"

    scorer = llm_judge(judge=garbage, prompt="Rate {output}")
    result = await scorer.score(ScorerInput(output="hello"))
    assert result.score == 0.5  # fallback


@pytest.mark.asyncio
async def test_toxicity_scorer_uses_judge():
    async def judge(prompt):
        # Confirm the prompt mentions toxicity
        assert "toxicity" in prompt.lower()
        return "0.95"

    scorer = toxicity_scorer(judge=judge)
    result = await scorer.score(ScorerInput(output="hello world"))
    assert result.score == 0.95


@pytest.mark.asyncio
async def test_faithfulness_scorer_includes_ground_truth():
    async def judge(prompt):
        assert "ground" in prompt.lower() or "truth" in prompt.lower()
        return "0.9"

    scorer = faithfulness_scorer(judge=judge)
    result = await scorer.score(
        ScorerInput(output="alice is 30", ground_truth="alice is 30")
    )
    assert result.score == 0.9


@pytest.mark.asyncio
async def test_create_scorer_includes_criteria_and_examples():
    captured_prompt = []

    async def judge(prompt):
        captured_prompt.append(prompt)
        return "0.7"

    scorer = create_scorer(
        id="my-criteria",
        name="My Criteria",
        description="test",
        judge=judge,
        criteria="The output must be polite.",
        examples=[
            {
                "input": "hi",
                "output": "hello!",
                "score": 1.0,
                "explanation": "polite",
            },
        ],
    )
    result = await scorer.score(ScorerInput(input="hi", output="ugh, fine"))
    assert result.score == 0.7
    prompt = captured_prompt[0]
    assert "polite" in prompt
    assert "hi" in prompt
    assert "hello!" in prompt


# ----- sampling ------------------------------------------------------------


def test_sampling_all_fires():
    assert SamplingConfig(kind="all").should_fire() is True


def test_sampling_none_never_fires():
    assert SamplingConfig(kind="none").should_fire() is False


def test_sampling_ratio_zero_never_fires():
    cfg = SamplingConfig(kind="ratio", rate=0.0)
    # rng returns 0.5; 0.5 < 0.0 is False
    assert cfg.should_fire(rng=lambda: 0.5) is False


def test_sampling_ratio_one_always_fires():
    cfg = SamplingConfig(kind="ratio", rate=1.0)
    assert cfg.should_fire(rng=lambda: 0.99) is True


# ----- run_scorers_async ---------------------------------------------------


@pytest.mark.asyncio
async def test_run_scorers_fires_all_by_default():
    bindings = {
        "schema": ScorerBinding(scorer=schema_adherence_scorer()),
        "latency": ScorerBinding(scorer=latency_scorer(target_ms=1000)),
    }
    result = await run_scorers_async(
        bindings, ScorerInput(latency_ms=500)
    )
    assert "schema" in result.results
    assert "latency" in result.results
    assert result.skipped == []


@pytest.mark.asyncio
async def test_run_scorers_skips_none_sampling():
    bindings = {
        "skipped": ScorerBinding(
            scorer=schema_adherence_scorer(),
            sampling=SamplingConfig(kind="none"),
        ),
        "fired": ScorerBinding(scorer=latency_scorer(target_ms=1000)),
    }
    result = await run_scorers_async(bindings, ScorerInput(latency_ms=500))
    assert "fired" in result.results
    assert "skipped" not in result.results
    assert "skipped" in result.skipped


@pytest.mark.asyncio
async def test_run_scorers_isolates_errors():
    class _Boom:
        @property
        def id(self):
            return "boom"

        @property
        def name(self):
            return "boom"

        @property
        def description(self):
            return ""

        async def score(self, input):
            raise RuntimeError("intentional failure")

    bindings = {
        "ok": ScorerBinding(scorer=latency_scorer(target_ms=1000)),
        "bad": ScorerBinding(scorer=_Boom()),
    }
    result = await run_scorers_async(bindings, ScorerInput(latency_ms=500))
    # Good scorer still produced a result.
    assert "ok" in result.results
    # Bad scorer's error captured.
    assert "bad" in result.errors
    assert "intentional failure" in result.errors["bad"]


# ----- aggregate -----------------------------------------------------------


def test_aggregate_empty():
    summary = aggregate({})
    assert summary.total == 0
    assert summary.mean == 1.0


def test_aggregate_mean_min_pass_count():
    from smithers_py.scorers import ScoreResult

    results = {
        "a": ScoreResult(score=1.0),
        "b": ScoreResult(score=0.6),
        "c": ScoreResult(score=0.3),
    }
    summary = aggregate(results, pass_threshold=0.5)
    assert abs(summary.mean - (1.0 + 0.6 + 0.3) / 3) < 0.001
    assert summary.minimum == 0.3
    assert summary.pass_count == 2  # a and b
    assert summary.total == 3
    assert summary.by_name["a"] == 1.0


# ----- persistence ---------------------------------------------------------


@pytest.mark.asyncio
async def test_score_log_persists(log_path):
    log = ScoreLog(log_path)
    bindings = {
        "latency": ScorerBinding(scorer=latency_scorer(target_ms=1000)),
    }
    await run_scorers_async(
        bindings,
        ScorerInput(latency_ms=500),
        log=log,
        run_id="r1",
        node_id="n1",
    )
    rows = log.list_for_run("r1")
    assert len(rows) == 1
    assert rows[0].scorer_id == "latency-1000"
    assert rows[0].score == 1.0
    assert rows[0].status == "success"


@pytest.mark.asyncio
async def test_score_log_records_errors(log_path):
    class _Boom:
        @property
        def id(self):
            return "boom"

        @property
        def name(self):
            return "boom"

        @property
        def description(self):
            return ""

        async def score(self, _):
            raise RuntimeError("nope")

    log = ScoreLog(log_path)
    bindings = {"bad": ScorerBinding(scorer=_Boom())}
    await run_scorers_async(
        bindings, ScorerInput(), log=log, run_id="r1", node_id="n1"
    )
    rows = log.list_for_run("r1")
    assert len(rows) == 1
    assert rows[0].status == "error"
    assert rows[0].error_json is not None
    assert "nope" in rows[0].error_json
