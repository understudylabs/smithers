# smithers-port-py-sync — the recursive maintenance workflow

A Smithers TS workflow that watches upstream
[`smithersai/smithers:main`](https://github.com/smithersai/smithers),
classifies new commits as `port` / `skip-v0` / `skip-forever` /
`already-ported` for the Python port at
[`port/resume`](https://github.com/understudylabs/smithers/tree/port/resume),
translates accepted deltas through real-mode agents, verifies via the
existing [`examples/wire_compat/`](../wire_compat/) cross-runtime
parity test, and opens PRs back against `port/resume`.

This is the **recursive** Smithers narrative: Cory's
[`bun-port-smithers`](../bun-port-smithers/) used Smithers to do a
one-shot Zig→Rust port. This workflow does Smithers→Smithers ongoing
sync. The orchestrator that we're porting drives the porting work.

## Shape (mirrors `bun-port-smithers/workflow.tsx`)

```
Workflow "smithers-port-py-sync"
  Sequence
    ├── (optional) HumanTask: operator-plan
    ├── Subflow: upstream-watch        (gh search merged PRs since lastIso)
    ├── Subflow: delta-classify        (Parallel per PR + static rules)
    ├── ApprovalGate: classify rubric  (fires when reject rate > 15%)
    ├── Subflow: delta-translate       (Parallel per port row, real LLM)
    ├── Subflow: cross-runtime-verify  (re-runs wire_compat live)
    ├── ApprovalGate: parity gate      (fires when parity FAILED)
    ├── Subflow: pr-emit               (gh pr create against port/resume)
    └── Task: final → port-sync-final-v0 row
```

## Status (2026-05-18)

- ✅ All 5 phases authored as real Smithers Subflows with typed Zod
  schemas + MDX prompts.
- ✅ Dry-mode end-to-end run completes: 6-PR fixture → 5 phases →
  PR drafted → final report.
- ✅ Live wire_compat parity check wired into the verify phase.
- ✅ Static-rule shortcut (`staticClassification`) handles obvious
  docs/gateway/types PRs without an LLM call.
- ⏳ Real-mode (`SMITHERS_PORT_PY_REAL_AGENTS=1`) is wired but
  untested against live LLM calls today. See [`COSTS.md`](COSTS.md)
  for projected spend.

## Quick start (dry mode)

```bash
cd /Users/luis/smithers/examples/smithers-port-py
bun install
rm -f smithers.db smithers.db-* 2>/dev/null
SMITHERS_PORT_SYNC_DB=smithers.db ./node_modules/.bin/smithers up workflow.tsx \
    --run-id port-sync-dry \
    --input "$(cat fixtures/input.smoke.json)" \
    --format json
```

The fixture pins 6 historical PRs (#87, #88, #109, #113, #130, #132) —
the same Tier-1 ports we shipped manually this morning. Dry mode
exercises every phase without LLM spend.

## Real mode

```bash
SMITHERS_PORT_PY_REAL_AGENTS=1 \
ANTHROPIC_API_KEY=... \
./node_modules/.bin/smithers up workflow.tsx \
    --run-id port-sync-real \
    --input fixtures/input.real.json
```

Real mode uses `ClaudeCodeAgent` (writer) and `PiAgent` (reviewer)
from `smithers-orchestrator`. Both need their underlying CLIs
installed and authenticated. See [`COSTS.md`](COSTS.md) for the per-PR
and per-week spend model.

## Inspect a run

```bash
./node_modules/.bin/smithers inspect port-sync-dry
./node_modules/.bin/smithers logs port-sync-dry
./node_modules/.bin/smithers chat port-sync-dry
```

## Files

```
smithers-port-py/
├── workflow.tsx                    # top-level — 5 phases + 2 gates + final
├── package.json                    # smithers-orchestrator + zod deps
├── tsconfig.json
├── README.md                       # this file
├── COSTS.md                        # per-PR + steady-state spend model
├── components/
│   ├── schemas.ts                  # Zod contracts for every persisted output
│   ├── agents.ts                   # dry-mode stubs + real-mode wiring
│   ├── sync-rules.ts               # deterministic helpers (cache keys,
│   │                                 static classification, cost estimate)
│   └── upstream-watch.ts           # gh search wrapper
├── prompts/
│   ├── operator-plan.mdx           # HumanTask body
│   ├── classify-delta.mdx          # per-PR classifier
│   ├── translate-delta.mdx         # per-PR translator (writer)
│   ├── verify-parity.mdx           # wire_compat verifier
│   └── emit-pr.mdx                 # gh pr create instructions
├── workflows/
│   ├── upstream-watch.tsx          # phase 1: gh search merged PRs
│   ├── delta-classify.tsx          # phase 2: Parallel per-PR classify
│   ├── delta-translate.tsx         # phase 3: Parallel per-row translate
│   ├── cross-runtime-verify.tsx    # phase 4: live wire_compat re-run
│   └── pr-emit.tsx                 # phase 5: aggregate + gh pr create
└── fixtures/
    └── input.smoke.json            # 6 historical PRs for dry-mode smoke
```

## Closing the loop

This workflow is itself a Python port artifact. If it runs successfully
on real upstream changes that include changes to *itself*, that's a
real reflexive proof: the orchestrator can keep its own Python twin
synchronized. That's the launch demo Cory would recognize from the
bun-port pattern.
