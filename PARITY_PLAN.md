# Python-port parity plan

Path from current state (~30-35% of the upstream component surface, 80% of
the orchestration core) to feature parity with `smithersai/smithers` main.

Companion to [PARITY.md](./PARITY.md), which tracks parity at the *PR
level*. This doc is the *component / subsystem* gap.

Last updated: 2026-05-18 after the multi-model meta-workflow run.

## Current honest state

The Python port is 33,569 LoC across 9 subsystems
(`nodes/`, `runtime/`, `engine/`, `state/`, `executors/`, `vcs/`, `db/`,
`mcp/`, plus the facade + JSX-shim). It contains substantially more than
a hopeful audit by file presence would suggest — `engine/` alone has 18
modules covering frame storm protection, render purity, task leases,
phases, and the tick loop.

### What's solid (would not rewrite)

- **Core orchestration**: Workflow, Task, Sequence, Parallel, Branch
  (If), Loop (Ralph), Subflow, Approval, ApprovalGate, HumanTask, Signal,
  WaitForEvent, MergeQueue, Each (Kanban-lite).
- **Runtime**: render → execute → persist loop on bare node IDs,
  ParallelNode threading with `maxConcurrency`, Task timeout enforcement
  (`ThreadPoolExecutor` + `Future.result(timeout=)`), retry policy with
  `NonRetryableError` short-circuit, durable signal/event delivery.
- **Resume**: supervisor auto-resumes stale runs, lease-based
  coordination (`engine/task_lease.py`), heartbeat staleness threshold.
- **JJ workspace support** (`vcs/workspace.py`).
- **Agents**: SubprocessAgent base + Claude Code / Codex / OpenCode / Pi
  adapters, AnthropicAgent via the official Python SDK.
- **MCP control plane**: 20-method JSON-RPC surface
  (`StartExecution`, `Tick`, `RunUntilIdle`, `Pause`, `Resume`,
  `SetState`, `RestartFromFrame`, `Approve`, `Deny`, `ForkFromFrame`,
  `CancelNode`, `RetryNode`, …) over stdio + HTTP transports.
- **Cross-runtime parity**: `examples/wire_compat/` is green (5/5 row
  diffs match between TS and Python).

### What's genuinely missing

By upstream-docs category (the
[full Smithers docs](https://smithers.sh/llms.txt) the user pasted today):

| Category | Missing items |
| --- | --- |
| Composite components | `Saga`, `TryCatchFinally`, `ContinueAsNew`, `Aspects`, `Worktree`, `Sandbox`, `SuperSmithers`, `ReviewLoop`, `Optimizer`, `ContentPipeline`, `DriftDetector`, `ScanFixVerify`, `Poller`, `Runbook`, `Supervisor` (the component), `CheckSuite`, `ClassifyAndRoute`, `GatherAndSynthesize`, `Panel`, `Debate`, `Kanban`, `DecisionTable`, `EscalationChain` |
| Subsystems | Memory (working/messages/semantic-recall), Tool sandbox (read/write/edit/grep/bash with containment), Scorers (schemaAdherence/latency/relevancy/toxicity/faithfulness/llmJudge), OpenAPI → tools, Caching with `cache.by` + version + schema-signature invalidation, Time travel (fork/replay/diff/timeline/revert) |
| Surfaces | HTTP server (multi-workflow REST + SSE), Serve mode (Hono single-workflow), Gateway (WebSocket/RPC + JWT/trusted-proxy auth + scopes + DevTools streaming), TUI |
| Cross-cutting | Hot reload (fs_watcher.py exists but full integration TBD), Cron schedules, Effect API (Python equivalent unclear — possibly skip) |

## What we actually need for Understudy as a product

Cut the wishlist by what the autonomous-maintenance product actually
requires. Three categories:

**Load-bearing (must have for product MVP)**:

1. **Memory** — agents need cross-run context. Without it every PR sync
   is amnesia. (~1 week)
2. **HTTP server** — cron-able + Slack/Greptile/Codex webhooks land here.
   (~3 days)
3. **Scorers** — gate auto-PRs on quality signals before merging. (~3
   days)
4. **Tool sandbox** — give agents safe file/shell access without
   blast-radius risk. (~4 days)
5. **Caching** — re-running a workflow shouldn't re-spend on tasks whose
   inputs didn't change. (~2 days)

**Differentiating (turns a hobby project into something a buyer trusts)**:

6. **Gateway** (WebSocket/RPC) — long-lived clients (the bot, the
   dashboard, the cron daemon). (~1 week)
7. **Time travel** (fork/replay/diff/timeline/revert) — when a sync goes
   wrong, "rewind to before the bad commit" is the killer feature.
   (~4 days)
8. **OpenAPI → tools** — Linear/Notion/Slack via spec without writing
   each integration. (~2 days)

**Nice-to-have (we can ship without them)**:

9. **Composite components** (Saga, TryCatch, ReviewLoop, Optimizer,
   Panel, Debate, Kanban, …) — each is 100-300 LoC, mostly thin
   compositions over Sequence/Parallel/Branch/Loop. Pick the 4-5 we
   actually use. (~3-5 days for the curated set)
10. **Worktree + Sandbox** (Docker/bubblewrap/codeplane) — for the case
    where an auto-PR needs an isolated env. (~1 week, codeplane is the
    bulk)
11. **TUI** — nice for ops, not load-bearing for product. (~1 week)
12. **Hot reload** — `engine/fs_watcher.py` is half-built; finishing
    likely 2-3 days.

## Recommended phasing

**Phase 1: production essentials (1 week)**

Memory + HTTP server + Scorers + Tool sandbox + Caching. After this,
Understudy can run unattended against a real repo, gate auto-PRs on
quality, and remember context across syncs.

- [ ] `smithers_py.memory` (working / messages / semantic recall + 4
      namespaces + TTL/TokenLimiter/Summarizer processors)
- [ ] `smithers_py.serve` (REST + SSE, mirrors the upstream
      `startServer` surface; auth via bearer token)
- [ ] `smithers_py.scorers` (schemaAdherence, latency, relevancy,
      toxicity, faithfulness, llmJudge, createScorer + sampling)
- [ ] `smithers_py.tools` (read/write/edit/grep/bash with path
      containment, symlink rejection, output truncation, network block,
      side-effect tracking + idempotency keys)
- [ ] Task-level `cache.by` + version + schema-signature in
      `runtime/runner.py` and persisted to `_smithers_cache`

**Phase 2: differentiating capabilities (1.5 weeks)**

Gateway, time travel, OpenAPI tools.

- [ ] `smithers_py.gateway` (WebSocket + REST `/rpc` + JWT and
      trusted-proxy auth + scopes + cron + DevTools streaming)
- [ ] `runtime.time_travel` (`fork`, `replay`, `diff`, `timeline`,
      `revert_to_attempt`) + the CLI commands to drive them
- [ ] `smithers_py.openapi` (`createOpenApiTools`: parse OpenAPI 3.x,
      auth shapes, allowlist/blocklist)
- [ ] CLI fork: `smithers-ts fork`, `replay`, `diff`, `timeline`

**Phase 3: composite components — curated, not exhaustive (3-4 days)**

Ship only what the meta-workflow + Understudy product actually uses;
defer the rest until a real need shows up.

- [ ] `Saga` (compensation chain — directly useful for atomic auto-PR
      sequences)
- [ ] `TryCatchFinally` (error boundary — useful for cleanup tasks)
- [ ] `ReviewLoop` (produce → review until approved — production fit)
- [ ] `Poller` (poll until satisfied — for waiting on external systems)
- [ ] `CheckSuite` (parallel checks with verdict — fits the cloud
      reviewer pattern)
- [ ] `Aspects` (token/cost budget enforcement — needed for budget
      guards on cron'd runs)
- [ ] `ContinueAsNew` (long-lived runs hand off carried state — needed
      for the always-on sync mode)

**Phase 4: stretch (optional)**

- [ ] `Worktree` + `Sandbox` (Docker runtime first; bubblewrap/codeplane
      deferred until a buyer asks)
- [ ] Hot reload (finish `engine/fs_watcher.py`)
- [ ] Cron (the cron table + scheduler tick)
- [ ] Remaining composites (ContentPipeline, DriftDetector, ScanFixVerify,
      Runbook, ClassifyAndRoute, GatherAndSynthesize, Panel, Debate,
      Kanban, DecisionTable, EscalationChain, Optimizer)
- [ ] TUI

## Total scope

- **Phase 1**: ~1 week of focused work, ~3-4k new LoC. Unblocks
  unattended product runs.
- **Phase 2**: ~1.5 weeks, ~5-7k new LoC. Unblocks long-lived clients,
  time-travel recovery.
- **Phase 3**: ~3-4 days, ~1.5k new LoC. Unblocks the patterns the
  meta-workflow itself wants.
- **Phase 4**: highly variable. Worktree/Sandbox could be ~1 week alone.

Rough total to ship Phase 1+2+3 (full product-grade parity for what we
need): **~3 weeks of focused work**. Phase 4 stretch as time allows.

## Cross-cutting requirements

These touch every phase:

- **Effect-ts equivalence**: Python doesn't have Effect-ts. We've been
  using plain async/threading. Most upstream code is portable; the
  Effect API specifically (`Smithers.workflow(opts)`, `G.step`, etc.) is
  a different programming model — likely we skip the Effect surface and
  expose the same primitives via a Pythonic API. Open question.
- **Hot reload (`engine/fs_watcher.py`)**: half-built; finish it after
  Phase 1 lands or before Phase 4.
- **Wire-compat tests must stay green**: every new subsystem needs a
  parity snapshot in `examples/wire_compat/`. Currently 5/5 tests pass;
  budget +1 snapshot per new subsystem.

## Open questions

1. **Memory's embedding backend**: do we use OpenAI embeddings, a local
   model, or pluggable? Affects ~2 days of work.
2. **Sandbox runtimes**: bubblewrap is Linux-only, codeplane is a hosted
   product, Docker is universal. Ship Docker-only first?
3. **TUI**: worth the week? The CLI + a web dashboard via the gateway
   might be enough for product UX.
4. **Effect API**: skip entirely (we don't have Effect-ts) or build a
   Pythonic equivalent (~2 days for a value-graph builder)?
5. **Hijack handoff for SDK agents**: Python's AnthropicAgent doesn't
   currently support resuming a partial conversation via REPL. The
   subprocess agents (Claude Code, Codex, Pi) inherit native session
   resume via the CLI flags. Worth scoping ~2 days of work for the SDK
   case.

## What this is not

This plan ships *feature parity for what Understudy needs*. It is not
a 1-to-1 line port of upstream Smithers — some upstream features
(Effect API, codeplane sandbox, full TUI) may stay deferred indefinitely
if they don't move the product forward.

When this plan is complete, the Python port runs the same meta-workflow
the TS side runs today, with the same observability, the same safety
gates, and the same cost-per-PR. That's the threshold for "parity."
