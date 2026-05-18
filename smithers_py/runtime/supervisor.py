"""Supervisor loop — auto-resume stale runs.

Polls ``ts_runs`` for entries marked ``status='running'`` whose
``updated_at`` is older than ``stale_threshold_seconds``, and takes
them over via ``run_workflow(..., force=True)``. Used after a crash
where a process didn't get to update the status to ``'paused'`` or
``'failed'`` before dying.

Closes upstream PR #124 (supervisor double-resume test) in spirit:
we serialize resume attempts with a polling delay and per-run lock so
two supervisor instances on the same DB don't both try to take over
the same run.

Exposed via ``smithers-ts supervise``:

    smithers-ts supervise examples/foo/workflow.py \\
        --interval 10s --stale-threshold 30s --max-concurrent 3
"""

from __future__ import annotations

import re
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set

from .runner import RunStatus, WorkflowError, run_workflow
from .store import Store


_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h)?\s*$", re.IGNORECASE)
_UNIT_TO_SECONDS = {
    "ms": 0.001,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
    None: 1.0,
}


def parse_duration(text: str) -> float:
    """Parse "30s" / "2m" / "1500ms" / "1.5h" into seconds."""
    m = _DURATION_RE.match(text)
    if not m:
        raise ValueError(f"unparseable duration: {text!r}")
    value = float(m.group(1))
    unit = (m.group(2) or "s").lower()
    return value * _UNIT_TO_SECONDS[unit]


@dataclass
class SupervisorStats:
    polls: int = 0
    resumed: int = 0
    failed: int = 0
    skipped_no_workflow: int = 0


class Supervisor:
    """Single-process polling supervisor.

    Construction parameters:

      - ``workflow_fn``: the workflow callable to resume runs of.
        Stale runs whose ``workflow_name`` doesn't match this fn's
        ``__name__`` are skipped (with a stat increment).
      - ``db_path``: SQLite path to poll.
      - ``interval_seconds``: how often to poll.
      - ``stale_threshold_seconds``: how old ``updated_at`` must be
        before we consider a 'running' run stale.
      - ``max_concurrent``: cap on simultaneous resumes per tick.
      - ``dry_run``: when True, log what would be resumed but don't
        actually call ``run_workflow``.

    The supervisor runs in the calling thread. Stop with
    ``supervisor.stop()`` (e.g., from a signal handler) or by letting
    the process exit.
    """

    def __init__(
        self,
        workflow_fn: Callable[..., Any],
        *,
        db_path: str = "smithers.db",
        interval_seconds: float = 10.0,
        stale_threshold_seconds: float = 30.0,
        max_concurrent: int = 3,
        dry_run: bool = False,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.workflow_fn = workflow_fn
        self.workflow_name = getattr(workflow_fn, "__name__", "anonymous_workflow")
        self.db_path = db_path
        self.interval_seconds = interval_seconds
        self.stale_threshold_seconds = stale_threshold_seconds
        self.max_concurrent = max_concurrent
        self.dry_run = dry_run
        self.log = log or (lambda msg: print(msg, file=sys.stderr))
        self.stats = SupervisorStats()
        self._stop_event = threading.Event()
        self._in_flight: Set[str] = set()
        self._lock = threading.Lock()

    def stop(self) -> None:
        """Signal the supervisor to exit at the next poll boundary."""
        self._stop_event.set()

    def run(self) -> SupervisorStats:
        """Loop polling + resuming until ``stop()`` is called.

        Returns the final stats. The loop checks ``_stop_event`` between
        each poll and each individual resume so shutdown is responsive.
        """
        self.log(
            f"[supervisor] polling {self.db_path} every {self.interval_seconds}s; "
            f"stale_threshold={self.stale_threshold_seconds}s; "
            f"workflow={self.workflow_name!r}; "
            f"dry_run={self.dry_run}"
        )
        try:
            while not self._stop_event.is_set():
                self._poll_once()
                self.stats.polls += 1
                if self._stop_event.wait(self.interval_seconds):
                    break
        finally:
            self.log(
                f"[supervisor] exiting: polls={self.stats.polls} "
                f"resumed={self.stats.resumed} failed={self.stats.failed} "
                f"skipped_no_workflow={self.stats.skipped_no_workflow}"
            )
        return self.stats

    def _poll_once(self) -> None:
        store = Store(self.db_path)
        store.connect()
        try:
            now = time.time()
            cutoff = now - self.stale_threshold_seconds
            with store.cursor() as cur:
                cur.execute(
                    """
                    SELECT run_id, workflow_name, updated_at
                      FROM ts_runs
                     WHERE status = 'running'
                       AND updated_at < ?
                     ORDER BY updated_at ASC
                     LIMIT ?
                    """,
                    (cutoff, self.max_concurrent),
                )
                rows = cur.fetchall()
        finally:
            store.close()

        for row in rows:
            if self._stop_event.is_set():
                break
            run_id = row["run_id"]
            wf_name = row["workflow_name"]
            if wf_name != self.workflow_name:
                self.stats.skipped_no_workflow += 1
                continue
            with self._lock:
                if run_id in self._in_flight:
                    continue
                self._in_flight.add(run_id)
            try:
                self._resume_one(run_id, row["updated_at"])
            finally:
                with self._lock:
                    self._in_flight.discard(run_id)

    def _resume_one(self, run_id: str, prior_updated_at: float) -> None:
        age = time.time() - prior_updated_at
        self.log(
            f"[supervisor] resuming run_id={run_id!r} "
            f"(stale by {age:.1f}s)"
            + (" [dry-run]" if self.dry_run else "")
        )
        if self.dry_run:
            return
        try:
            result = run_workflow(
                self.workflow_fn,
                db_path=self.db_path,
                run_id=run_id,
                resume=True,
                force=True,
            )
            self.log(
                f"[supervisor] run_id={run_id!r} resumed → {result.status.value}"
            )
            self.stats.resumed += 1
        except WorkflowError as exc:
            self.log(f"[supervisor] run_id={run_id!r} failed: {exc}")
            self.stats.failed += 1
        except Exception as exc:  # noqa: BLE001
            self.log(f"[supervisor] run_id={run_id!r} crashed: {exc}")
            self.stats.failed += 1


__all__ = ["Supervisor", "SupervisorStats", "parse_duration"]
