"""Run scorer bindings against a task output + persist results.

Two entry points:

- ``run_scorers_async(bindings, input, ...)`` — fires every binding
  whose sampling config says "go" and returns the ``ScoreResult``s.
- ``aggregate(results)`` — reduces a dict of results to a single
  summary (mean / min / by-name dict).

Persistence is via ``ScoreLog``, which writes to ``ts_scores``.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, Optional

from .types import (
    ScoreResult,
    ScoreRow,
    ScorerBinding,
    ScorerInput,
    ScorersMap,
)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS ts_scores (
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

CREATE INDEX IF NOT EXISTS idx_ts_scores_run_node
    ON ts_scores(run_id, node_id);
CREATE INDEX IF NOT EXISTS idx_ts_scores_scorer
    ON ts_scores(scorer_id);
"""


class ScoreLog:
    """Persists scorer results to the ``ts_scores`` table.

    Initializes the table on first connect (idempotent). One row per
    fired scorer per task attempt.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._init_schema()

    def record(self, row: ScoreRow) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO ts_scores (
                    run_id, node_id, iteration, attempt,
                    scorer_id, scorer_name, score, reason, meta_json,
                    started_at_ms, finished_at_ms, status, error_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.run_id,
                    row.node_id,
                    row.iteration,
                    row.attempt,
                    row.scorer_id,
                    row.scorer_name,
                    row.score,
                    row.reason,
                    row.meta_json,
                    row.started_at_ms,
                    row.finished_at_ms,
                    row.status,
                    row.error_json,
                ),
            )

    def list_for_run(
        self,
        run_id: str,
        *,
        node_id: Optional[str] = None,
    ) -> list[ScoreRow]:
        clauses = ["run_id = ?"]
        params: list = [run_id]
        if node_id is not None:
            clauses.append("node_id = ?")
            params.append(node_id)
        sql = (
            "SELECT run_id, node_id, iteration, attempt, scorer_id, "
            "scorer_name, score, reason, meta_json, started_at_ms, "
            "finished_at_ms, status, error_json FROM ts_scores "
            "WHERE " + " AND ".join(clauses) +
            " ORDER BY started_at_ms ASC"
        )
        with self._connect() as db:
            rows = db.execute(sql, params).fetchall()
        return [
            ScoreRow(
                run_id=r[0],
                node_id=r[1],
                iteration=r[2],
                attempt=r[3],
                scorer_id=r[4],
                scorer_name=r[5],
                score=r[6],
                reason=r[7],
                meta_json=r[8],
                started_at_ms=r[9],
                finished_at_ms=r[10],
                status=r[11],
                error_json=r[12],
            )
            for r in rows
        ]

    def _init_schema(self) -> None:
        with self._connect() as db:
            db.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self._db_path, isolation_level=None, timeout=30.0)
        try:
            db.execute("PRAGMA journal_mode = WAL")
            db.execute("PRAGMA synchronous = NORMAL")
            yield db
        finally:
            db.close()


@dataclass
class RunScorersResult:
    """Result of ``run_scorers_async``. One entry per binding key."""

    results: dict[str, ScoreResult] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


async def run_scorers_async(
    bindings: ScorersMap,
    input: ScorerInput,
    *,
    log: Optional[ScoreLog] = None,
    run_id: Optional[str] = None,
    node_id: Optional[str] = None,
    iteration: int = 0,
    attempt: int = 0,
) -> RunScorersResult:
    """Fire every binding whose sampling config says "go".

    Bindings run concurrently. Results are returned by binding key
    regardless of success/error. Errors are caught per-binding so one
    failing scorer doesn't sink the others; the offending error message
    lands in the ``errors`` dict.

    Pass ``log`` + ``run_id`` + ``node_id`` to persist rows.
    """
    fired: dict[str, ScorerBinding] = {
        key: b for key, b in bindings.items() if b.sampling.should_fire()
    }
    skipped = [key for key in bindings if key not in fired]

    async def _one(key: str, binding: ScorerBinding) -> tuple[str, ScoreResult]:
        result = await binding.scorer.score(input)
        return key, result

    results: dict[str, ScoreResult] = {}
    errors: dict[str, str] = {}

    if fired:
        tasks = [
            asyncio.create_task(
                _runWithCapture(key, binding, input, results, errors, log,
                                run_id, node_id, iteration, attempt)
            )
            for key, binding in fired.items()
        ]
        await asyncio.gather(*tasks)

    return RunScorersResult(results=results, skipped=skipped, errors=errors)


async def _runWithCapture(
    key: str,
    binding: ScorerBinding,
    input: ScorerInput,
    results: dict[str, ScoreResult],
    errors: dict[str, str],
    log: Optional[ScoreLog],
    run_id: Optional[str],
    node_id: Optional[str],
    iteration: int,
    attempt: int,
) -> None:
    started = int(time.time() * 1000)
    try:
        result = await binding.scorer.score(input)
        finished = int(time.time() * 1000)
        results[key] = result
        if log is not None and run_id and node_id:
            log.record(
                ScoreRow(
                    run_id=run_id,
                    node_id=node_id,
                    iteration=iteration,
                    attempt=attempt,
                    scorer_id=binding.scorer.id,
                    scorer_name=binding.scorer.name,
                    score=result.score,
                    reason=result.reason,
                    meta_json=(
                        json.dumps(result.meta, default=str)
                        if result.meta else None
                    ),
                    started_at_ms=started,
                    finished_at_ms=finished,
                    status="success",
                )
            )
    except Exception as exc:
        finished = int(time.time() * 1000)
        errors[key] = f"{type(exc).__name__}: {exc}"
        if log is not None and run_id and node_id:
            log.record(
                ScoreRow(
                    run_id=run_id,
                    node_id=node_id,
                    iteration=iteration,
                    attempt=attempt,
                    scorer_id=binding.scorer.id,
                    scorer_name=binding.scorer.name,
                    score=0.0,
                    reason=None,
                    meta_json=None,
                    started_at_ms=started,
                    finished_at_ms=finished,
                    status="error",
                    error_json=json.dumps(
                        {"type": type(exc).__name__, "message": str(exc)}
                    ),
                )
            )


@dataclass
class AggregateScore:
    """Summary across a set of scorer results."""

    mean: float
    minimum: float
    by_name: dict[str, float]
    pass_count: int  # scorers with score >= 0.5
    total: int


def aggregate(results: dict[str, ScoreResult], *, pass_threshold: float = 0.5) -> AggregateScore:
    """Reduce a set of ``ScoreResult``s to a single summary.

    Used by the engine to decide whether a task passed an SLA on its
    scorers without forcing the user to write boilerplate aggregation
    every time.
    """
    if not results:
        return AggregateScore(
            mean=1.0, minimum=1.0, by_name={}, pass_count=0, total=0
        )
    scores = [r.score for r in results.values()]
    return AggregateScore(
        mean=sum(scores) / len(scores),
        minimum=min(scores),
        by_name={k: r.score for k, r in results.items()},
        pass_count=sum(1 for s in scores if s >= pass_threshold),
        total=len(scores),
    )


__all__ = [
    "AggregateScore",
    "RunScorersResult",
    "ScoreLog",
    "aggregate",
    "run_scorers_async",
]
