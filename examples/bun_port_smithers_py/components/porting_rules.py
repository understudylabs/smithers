"""Deterministic helpers for the bun-port workflow.

Mirrors examples/bun-port-smithers/components/porting-rules.ts. These are
the parts of the workflow that are pure compute — no LLMs — and therefore
the part that *doesn't* commoditize as the agents underneath get better.
The methodology lives here.
"""

from __future__ import annotations

import hashlib
import re
from typing import Iterable, List, Sequence


_NODE_ID_SAFE = re.compile(r"[^a-zA-Z0-9_]")


def stable_node_id(text: str) -> str:
    """Filesystem-safe slug used as part of the workflow node id.

    Same shape as the TS reference: the last 48 chars after stripping non
    alphanumerics. Stable across runs given the same input.
    """
    return _NODE_ID_SAFE.sub("_", text)[-48:]


def field_key(field: dict) -> str:
    """``zigFile|StructName|fieldName`` — the immutable identity of a Zig field
    across runs, voters, and revisions. Matches TS ``fieldKey``."""
    return f"{field['file']}|{field['struct']}|{field['field']}"


def cache_key_for_file(
    *,
    repo: str,
    zig: str,
    crate: str,
    porting_revision: str = "",
    lifetime_revision: str = "",
) -> str:
    """Cache key for a single lifetime classification cell.

    Hashes the inputs that, when changed, must invalidate the prior LLM
    classification: the repo identity, the Zig file path, the crate it
    routes into, and any rubric revision pins.
    """
    h = hashlib.sha256()
    for part in (repo, zig, crate, porting_revision, lifetime_revision):
        h.update(part.encode("utf-8"))
        h.update(b"|")
    return h.hexdigest()[:16]


def select_lifetime_verification_rows(
    fields: Sequence[dict],
    sample_rate: float,
) -> List[dict]:
    """Sample fields for triple-verification.

    bun-port-smithers picks ~12% of classified fields for a 3-voter
    verification pass. We mirror that exactly: sort by ``field_key`` so the
    sample is deterministic across runs, then take every Nth row to hit
    the target rate.
    """
    if not fields or sample_rate <= 0:
        return []
    sample_rate = min(max(sample_rate, 0.0), 1.0)
    sorted_fields = sorted(fields, key=field_key)
    n = max(1, round(len(sorted_fields) * sample_rate))
    step = max(1, len(sorted_fields) // n)
    return sorted_fields[::step][:n]


def summarize_lifetime_rows(fields: Sequence[dict]) -> dict:
    """Aggregate per-class counts + the UNKNOWN rate gate input."""
    total = len(fields)
    by_class: dict = {}
    unknown = 0
    for f in fields:
        cls = f.get("class") or f.get("class_") or "UNKNOWN"
        by_class[cls] = by_class.get(cls, 0) + 1
        if cls == "UNKNOWN":
            unknown += 1
    return {
        "totalFields": total,
        "byClass": by_class,
        "unknownRate": (unknown / total) if total else 0.0,
    }


def lifetime_tsv(fields: Sequence[dict]) -> str:
    """Emit a TSV view of every classified field.

    Same column order as the TS reference so the operator approval gate
    presents a familiar table.
    """
    head = "\t".join(
        ["file", "crate", "struct", "field", "class", "rustType", "confidence"]
    )
    body = "\n".join(
        "\t".join(
            [
                str(f.get("file", "")),
                str(f.get("crate", "")),
                str(f.get("struct", "")),
                str(f.get("field", "")),
                str(f.get("class") or f.get("class_") or ""),
                str(f.get("rustType", "")),
                str(f.get("confidence", "")),
            ]
        )
        for f in fields
    )
    return head + "\n" + body if body else head


def tsv_preview(tsv: str, max_rows: int = 20) -> str:
    """First N rows of a TSV (header + max_rows), used in approval bodies."""
    return "\n".join(tsv.split("\n")[: max_rows + 1])


# ----- Helpers for the remaining phases --------------------------------------


def normalize_port_files(files):
    """Phase A plan: for each Zig file, compute its Rust target path."""
    out = []
    for f in files:
        zig = f.get("zig") if isinstance(f, dict) else f.zig
        crate = (f.get("crate") if isinstance(f, dict) else f.crate) or "bun"
        loc = (f.get("loc") if isinstance(f, dict) else f.loc) or 0
        rs = zig.replace(".zig", ".rs") if zig else ""
        out.append({"zig": zig, "rs": rs, "loc": loc, "crate": crate})
    return out


def plan_crates_by_tier(crates):
    """Group crates by tier so the compile phase bring-up can do tiers serially."""
    tiers_dict = {}
    for c in crates:
        is_dict = isinstance(c, dict)
        tier = c.get("tier", 0) if is_dict else c.tier
        tiers_dict.setdefault(tier, []).append(
            c if is_dict else c.model_dump()
        )
    tiers = [{"tier": t, "crates": cs} for t, cs in sorted(tiers_dict.items())]
    return {"tiers": tiers, "totalCrates": len(crates)}


def dedupe_failures(probe_results):
    """Roll probe failures up into a deduped FailureSet keyed by (probeId, panic)."""
    seen = {}
    for r in probe_results:
        if r is None:
            continue
        is_dict = isinstance(r, dict)
        passed = r.get("passed") if is_dict else r.passed
        if passed:
            continue
        pid = r.get("probeId") if is_dict else r.probeId
        cmd = r.get("command") if is_dict else r.command
        loc = r.get("panicLocation") if is_dict else r.panicLocation
        asrt = r.get("assertion") if is_dict else r.assertion
        key = f"{pid}|{loc or ''}|{asrt or ''}"
        seen.setdefault(
            key,
            {
                "failureKey": key,
                "probeId": pid,
                "command": cmd,
                "panicLocation": loc,
                "assertion": asrt,
            },
        )
    failures = list(seen.values())
    return {"totalFailures": len(failures), "failures": failures}


def survey_targets(targets):
    """Build the TargetSurvey for the ungate phase."""
    survey = []
    for t in targets:
        is_dict = isinstance(t, dict)
        survey.append(
            {
                "id": t.get("id") if is_dict else t.id,
                "crate": t.get("crate") if is_dict else t.crate,
                "file": t.get("file") if is_dict else t.file,
                "reason": (t.get("reason") if is_dict else t.reason) or "",
            }
        )
    return {"totalTargets": len(survey), "targets": survey}


def survey_sweeps(sweeps):
    """Build SweepSurvey for the audit-sweeps phase."""
    out = []
    for s in sweeps:
        out.append(s.model_dump() if hasattr(s, "model_dump") else dict(s))
    return {"sweeps": out, "total": len(out)}
