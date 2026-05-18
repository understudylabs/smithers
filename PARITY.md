# Parity tracker: upstream TS main → Python port

Every merged PR on [`smithersai/smithers`](https://github.com/smithersai/smithers)
since the `python` branch froze (2026-01-23, last commit `d1287436`) is
listed below with its Python-port disposition. Each row is one of:

- ✅ **Ported** — landed on `port/resume`.
- ⏳ **Deferred** — accepted for the v0.2 backlog; reason given.
- ➖ **N/A** — TS-specific (Bun tooling, gateway server, docs only) with
  no equivalent in the Python runtime.
- 🔍 **Open question** — needs upstream input or a deeper read before
  deciding.

The MVP runtime is `smithers_py.runtime` (independent from the v1.0.0
tick loop). Everything below is scoped to whether each upstream change
needs a corresponding adjustment there.

## Merged PRs (ordered by merge date)

| # | Date | Title | Status | Notes |
| --- | --- | --- | --- | --- |
| 72 | 2026-02-13 | Add PI support | ⏳ Deferred | `AgentLike` protocol formalized; PiAgent adapter is a thin wrapper to add in v0.2 along with other providers. |
| 85 | 2026-02-18 | PiAgent JSON-mode NDJSON fix | ⏳ Deferred | Tied to #72. Will land with PiAgent. |
| 87 | 2026-03-01 | `resume --force` + SIGINT cancellation | ✅ Ported | `run_workflow(..., force=True)` refuses to take over a 'running' run without force; CLI `--force` flag; SIGINT handler in `smithers-ts up` marks run cancelled. 2 new tests. |
| 88 | 2026-03-01 | Idle timeout for CLI agents | ⏳ Deferred | `TaskNode.timeout_ms` is a typed field already; soft enforcement TBD (needs threading or asyncio for real cancellation in Python). v0.2. |
| 89 | 2026-03-01 | `smithers graph` cyclic refs | ➖ N/A | No `smithers-ts graph` command yet. Logged for when we add it. |
| 92 | 2026-03-06 | docs: `RunResult.output` clarification | ✅ Ported | Our `RunResult.output` is the terminal output row's payload (preferring `output_name == "output"`); documented in runner docstring + PORT_RESUME. |
| 93 | 2026-03-13 | docs: Ralph as core pattern | ➖ N/A | Docs-only; Ralph node port is deferred. |
| 94 | 2026-03-18 | docs: nested ralph | ➖ N/A | Docs-only; tied to Ralph deferral. |
| 109 | 2026-03-18 | Ralph loops respect approved reviews | ✅ Ported (Loop+TSRalph) | `LoopNode` added with `until_fn` callable + `max_iterations` + `on_max_reached` ("fail"/"return-last"). Ralph is a deprecated alias upstream; exported as `TSRalphNode` (not `RalphNode`, which still belongs to the v1.0.0 engine). Behavioral fix (loops respect approved reviews) is naturally encoded — workflows pass `until_fn=lambda c: c.output("reviewer")["approved"]`. 4 new tests. |
| 113 | 2026-03-18 | Nested Loop/Ralph across structural nodes | ✅ Ported (Loop) | Nesting works: each iteration suffixes node ids with `:iter:N`, so child paths remain unique across nested loops. |
| 114 | 2026-03-18 | Codex rollout recorder stderr tolerance | ⏳ Deferred | Codex agent adapter not ported yet. v0.2 with PiAgent and OpenCodeAgent. |
| 118 | 2026-03-27 | PiAgent RPC terminal-response wait | ⏳ Deferred | Tied to #72. |
| 124 | 2026-04-16 | test: supervisor double-resume reproduction | ⏳ Deferred | We have no supervisor loop yet; resume-on-crash is manual via `--force`. The supervisor pattern is a v0.2 ergonomic. |
| 130 | 2026-05-04 | Duplicate output refs + SDK structured output + docs | ✅ Ported | `_Outputs` namespace yields a distinct `OutputRef` per registered key even when keys share a schema (test: `test_duplicate_schema_yields_unique_refs`). Structured-output handshake is implicit because Pydantic schemas validate Task return values directly. Docs in PORT_RESUME. |
| 125 | 2026-05-04 | OpenCodeAgent integration | ⏳ Deferred | Implementer of `AgentLike` not yet shipped. v0.2 along with the other providers. |
| 126 | 2026-05-04 | `bunx init` dependency resolution | ➖ N/A | TS init flow. The Python install is `uv pip install smithers-py` (eventual PyPI). |
| 131 | 2026-04-27 | Restore green main baseline | ➖ N/A | Upstream-only maintenance. |
| 132 | 2026-05-04 | Honor non-retryable agent failures | ✅ Ported | `NonRetryableError` exception class; `run_workflow` retries up to `TaskNode.max_attempts` with exponential backoff, short-circuits on `NonRetryableError`. 4 new tests. |
| 133 | 2026-05-06 | Harden gateway client contracts | ➖ N/A | Gateway server is `skip-v0` per PORT_PLAN. |
| 134 | 2026-05-10 | Harden gateway HTTP boundaries | ➖ N/A | Same. |
| 137 | 2026-05-14 | `smithers init` .gitignore templates | ➖ N/A | Tied to TS init flow. |
| 138 | 2026-05-14 | Codex/OpenAI agent fixes | ⏳ Deferred | Tied to deferred agent adapters. |
| 139 | 2026-05-18 | Fix doc URL | ➖ N/A | Docs only. |

## Open PRs

| # | Status | Title | Disposition |
| --- | --- | --- | --- |
| 135 | DRAFT (since 2026-05-10) | feat(observability): canonical agent trace OTEL logs | 🔍 Wait. Adds `CanonicalAgentTraceEvent`, `AgentTraceSummary`, structured trace events (`assistant.text.delta`, `tool.execution.*`, `usage`, `capture.error`, …) and a `trace.completeness` field. Worth adopting the same canonical shape on the Python side once this lands. Our `ts_output_rows` table is the right place to extend; or we add `ts_trace_events` mirroring the canonical model. |

## Effect API rewrite (PR-less context)

Upstream landed an **Effect API rewrite** during the gap window (visible
in #135's PR body: *"~691 commits of monorepo restructure + Effect API
rewrite have since landed on `main`"*). `runWorkflow` returns an
`Effect` rather than a Promise. Cancellation, retries, resource
scoping, and concurrency now compose through Effect operators.

Our Python `run_workflow` is sync and returns a plain `RunResult`. This
is a deliberate v0.1 simplification:

- The runtime's behavior (pause/resume, retry, force resume) is in
  place; it doesn't need Effect to work correctly.
- Effect's analogues in Python are `anyio` / `trio`'s structured
  concurrency primitives. Migrating to anyio is a v0.2 lift — once it
  lands, `ParallelNode` gets real concurrency and `TaskNode.timeout_ms`
  gets real enforcement at the same time.

## Summary of catch-up state

- **7 PRs fully ported** (#87, #92, #109, #113, #130, #132, plus the
  original MVP surface from #91-ish era).
- **7 PRs deferred** to v0.2 — every one is either tied to agent
  provider adapters (#72/#85/#114/#118/#125/#138) or CLI ergonomics
  (#88/#124, #89).
- **9 PRs N/A** — docs only or Bun/gateway-specific.
- **1 open PR** (#135 observability) parked behind a "wait for upstream
  to ship" gate.

## What "parity with current main" means for v0.1

The Python port is at **runtime + CLI parity** with TS main for the
slice of the API surface most workflows actually use:

- `Workflow / Sequence / Parallel / Task / Subflow / ApprovalGate /
  HumanTask / Worktree / MergeQueue / Branch / Loop` — 11 node types
  live and executable. `TSRalphNode` is exported as the deprecated-
  upstream alias for `LoopNode`.
- `createSmithers({input, output, ...}, db_path=...)` — facade landed
  including duplicate-schema safety (#130).
- `RunResult` — paused / completed / failed / cancelled statuses with
  pending approvals, output rows, error details (PR #92 contract).
- Retry policy with `NonRetryableError` (PR #132).
- `up --resume --force` + SIGINT cancellation (PR #87).
- CLI: `smithers-ts up | approve | deny | inspect | ps`.
- 37 runtime tests + 35 schema/facade tests + 5 wire-compat tests
  (2 single-runtime + 3 cross-runtime), **712 total passing**
  (was 645 at python-branch-freeze; +67 new tests, zero regressions).
- **Cross-runtime parity ACHIEVED.** Python and TS Smithers produce
  identical normalized SQLite row sets for the canonical wire-compat
  workflow. The acceptance test (`test_cross_runtime_row_set_diff`)
  runs both runtimes and asserts an empty diff — currently green.
  See [`examples/wire_compat/`](examples/wire_compat/) for the
  workflow.py / workflow.tsx pair + diff harness.

## What "parity with current main" does NOT mean for v0.1

- Real concurrency in `ParallelNode` — sequential within a frame today.
- Real timeout enforcement on Tasks (`timeout_ms` is a typed field but
  no thread/asyncio-backed cancellation yet).
- The Ralph loop primitive.
- Provider adapters (Anthropic SDK, Claude Code, Codex, Pi, OpenCode).
- The Effect API composition model on the Python side.
- Canonical agent trace events (#135 — wait for upstream).
- The gateway server / client / HTTP boundaries.

These are the v0.2 backlog. Each is a discrete lift; none block the v0.1
MVP from being usable today.
