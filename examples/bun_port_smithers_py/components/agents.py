"""Dry-mode agents for the Python bun-port example.

Mirrors examples/bun-port-smithers/components/agents.ts:
- Real-mode (``BUN_PORT_SMITHERS_PY_REAL_AGENTS=1``) instantiates real LLM
  agents (Claude Code, etc.) against the upstream Bun checkout.
- Dry-mode returns deterministic fixture outputs based on tagged fields
  in the prompt, so the workflow shape can be validated end-to-end with
  zero LLM spend.

The dry shape is intentionally identical to the TS reference: same agent
names (``lifetimeClassifier``, ``lifetimeVerifier``, etc.), same prompt
tags (``ZIG``, ``CRATE``, ``FIELD_KEY``), same return shapes.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional


use_real_agents = os.environ.get("BUN_PORT_SMITHERS_PY_REAL_AGENTS") == "1"


@dataclass
class _LocalAgent:
    """A minimal agent shape the TaskNode/SubflowNode runtime can route to.

    Real-mode agents (Claude Code, Codex CLI, Pi) will be wired in once the
    smithers_py engine learns to dispatch on the ``agent`` prop.
    """

    id: str
    generate: Callable[..., Dict[str, Any]]


def _read_tag(prompt: str, name: str, fallback: str = "") -> str:
    """Extract a TAG: value field from a tagged prompt body."""
    match = re.search(
        rf"{name}:\s*([\s\S]*?)(?=(?:\s+|)[A-Z_]+:|$)",
        prompt,
    )
    return (match.group(1).strip() if match else fallback) or fallback


def _dry_output(kind: str, prompt: str) -> Dict[str, Any]:
    zig = _read_tag(prompt, "ZIG", "src/example/example.zig")
    rs = _read_tag(prompt, "RS", zig.replace(".zig", ".rs"))
    crate = _read_tag(prompt, "CRATE", "example")
    target_id = _read_tag(prompt, "TARGET", "target")
    subject = _read_tag(prompt, "SUBJECT", target_id)
    area_id = _read_tag(prompt, "AREA", "area")
    branch = _read_tag(prompt, "BRANCH", f"bun-port/{area_id}")
    probe_id = _read_tag(prompt, "PROBE", "probe")
    command = _read_tag(prompt, "COMMAND", "--help")
    failure_key = _read_tag(prompt, "FAILURE", "failure")
    sweep_id = _read_tag(prompt, "SWEEP", "sweep")
    key = _read_tag(prompt, "FIELD_KEY", f"{zig}|Example|ptr")
    voter = _read_tag(prompt, "VOTER", "dry")
    tier = int(_read_tag(prompt, "TIER", "0") or "0")
    file_path = _read_tag(prompt, "FILE", "src/example.rs")
    kind_field = _read_tag(prompt, "KIND", "sweep")

    if kind == "lifetime-classify":
        return {
            "file": zig,
            "crate": crate,
            "fields": [
                {
                    "struct": "Example",
                    "field": "ptr",
                    "zigType": "?*Thing",
                    "class": "UNKNOWN",
                    "rustType": "Option<NonNull<Thing>>",
                    "evidence": f"{zig}:1 dry-run fixture",
                    "confidence": "low",
                }
            ],
        }
    if kind == "lifetime-verify":
        return {
            "key": key,
            "voter": voter,
            "refuted": False,
            "correctClass": "UNKNOWN",
            "reason": "dry-run accepted",
        }
    if kind == "phase-a-implement":
        return {
            "zig": zig,
            "rs": rs,
            "status": "drafted",
            "confidence": "medium",
            "todos": 0,
            "rsLoc": 12,
            "note": "dry-run draft",
        }
    if kind == "phase-a-verify":
        return {
            "subject": rs,
            "reviewer": "phase-a-dry-reviewer",
            "approved": True,
            "ok": True,
            "issues": [],
            "feedback": "dry-run approved",
        }
    if kind == "phase-a-fix":
        return {
            "zig": zig,
            "rs": rs,
            "applied": 0,
            "remaining": 0,
            "note": "no dry-run fixes required",
        }
    if kind == "crate-check":
        return {
            "crate": crate,
            "tier": tier if tier else 0,
            "compiles": True,
            "errorCount": 0,
            "rounds": 1,
            "gatedModules": [],
            "blockedOn": [],
            "notes": "dry-run cargo check green",
        }
    if kind == "proper-port":
        return {
            "targetId": target_id,
            "status": "patched",
            "filesChanged": [file_path],
            "summary": "dry-run patch",
        }
    if kind == "spec-review":
        return {
            "targetId": target_id,
            "reviewer": voter,
            "approved": True,
            "issues": [],
            "feedback": "dry-run approved",
        }
    if kind == "spec-decision":
        return {
            "targetId": target_id,
            "approved": True,
            "approvals": 2,
            "rejections": 0,
            "issues": [],
            "feedback": "dry-run consensus approved",
        }
    if kind == "build":
        return {
            "ok": True,
            "command": "cargo build -p bun_bin",
            "summary": "dry-run build green",
        }
    if kind == "probe":
        return {
            "probeId": probe_id,
            "command": command,
            "passed": True,
            "panicLocation": None,
            "assertion": None,
            "signal": None,
            "durationMs": 1,
            "output": "dry-run probe passed",
        }
    if kind == "failure-fix":
        return {
            "failureKey": failure_key,
            "status": "fixed",
            "filesChanged": [],
            "summary": "dry-run failure fix",
        }
    if kind == "test-area":
        return {
            "areaId": area_id,
            "pass": 1,
            "fail": 0,
            "total": 1,
            "allPass": True,
            "bughuntBugs": 0,
            "commits": [],
            "branch": branch,
            "notes": "dry-run area green",
        }
    if kind == "merge":
        return {
            "id": subject,
            "picked": 0,
            "conflicts": 0,
            "notes": "dry-run merge",
        }
    if kind == "sweep":
        return {
            "sweepId": sweep_id,
            "kind": kind_field,
            "candidates": 1,
            "fixed": 1,
            "skipped": 0,
            "summary": "dry-run sweep",
        }
    return {
        "subject": subject,
        "reviewer": "dry",
        "approved": True,
        "ok": True,
        "issues": [],
        "feedback": "dry-run approved",
    }


def _make_dry_agent(kind: str) -> _LocalAgent:
    def generate(*, prompt: str = "", **_: Any) -> Dict[str, Any]:
        output = _dry_output(kind, prompt or "")
        return {"text": json.dumps(output), "output": output}

    return _LocalAgent(id=f"bun-port-py-dry:{kind}", generate=generate)


def _real_writer_agent(repo: str, kind: str) -> Any:
    """Placeholder for a real-mode writer agent.

    When the smithers_py engine learns to route TaskNode.agent through to a
    ClaudeCodeAgent equivalent, this function returns that agent. For now,
    real-mode falls back to dry-mode with a stderr warning so the workflow
    still runs.
    """

    import sys

    print(
        f"[warn] real-mode writer agent for {kind} not yet wired; falling back to dry",
        file=sys.stderr,
    )
    return _make_dry_agent(kind)


def _real_reviewer_agent(repo: str, kind: str) -> Any:
    """Placeholder for a real-mode reviewer agent."""

    import sys

    print(
        f"[warn] real-mode reviewer agent for {kind} not yet wired; falling back to dry",
        file=sys.stderr,
    )
    return _make_dry_agent(kind)


def agents_for_repo(repo: str) -> Dict[str, _LocalAgent]:
    """Return the bundle of agents the bun-port workflow consumes.

    Names match the TS reference 1:1 so existing prompts/templates port
    without renaming.
    """

    writer = (
        (lambda kind: _real_writer_agent(repo, kind)) if use_real_agents else _make_dry_agent
    )
    reviewer = (
        (lambda kind: _real_reviewer_agent(repo, kind))
        if use_real_agents
        else _make_dry_agent
    )

    return {
        "lifetimeClassifier": writer("lifetime-classify"),
        "lifetimeVerifier": reviewer("lifetime-verify"),
        "phaseAImplementer": writer("phase-a-implement"),
        "phaseAVerifier": reviewer("phase-a-verify"),
        "phaseAFixer": writer("phase-a-fix"),
        "crateChecker": writer("crate-check"),
        "properPorter": writer("proper-port"),
        "specReviewer": reviewer("spec-review"),
        "specDecider": reviewer("spec-decision"),
        "builder": writer("build"),
        "prober": writer("probe"),
        "failureFixer": writer("failure-fix"),
        "testAreaWorker": writer("test-area"),
        "mergeAgent": writer("merge"),
        "sweepAgent": writer("sweep"),
        "judge": reviewer("judge"),
    }
