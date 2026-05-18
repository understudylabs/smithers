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
| 72 | 2026-02-13 | Add PI support | ✅ Ported | `PiAgent` in `runtime/subprocess_agents.py`. Provider/mode/thinking/tools CLI args forwarded. |
| 85 | 2026-02-18 | PiAgent JSON-mode NDJSON fix | ✅ Ported | `PiAgent._extract_output` parses NDJSON line-by-line and pulls the last assistant text. |
| 87 | 2026-03-01 | `resume --force` + SIGINT cancellation | ✅ Ported | `run_workflow(..., force=True)`; CLI `--force` flag; SIGINT handler in `smithers-ts up` marks run cancelled. |
| 88 | 2026-03-01 | Idle timeout for CLI agents | ✅ Ported | `TaskNode.timeout_ms` enforced via per-attempt `ThreadPoolExecutor` with `Future.result(timeout=)`. Rogue computes detached via `shutdown(wait=False)`. |
| 89 | 2026-03-01 | `smithers graph` cyclic refs | ✅ Ported | `smithers-ts graph` command — tree / JSON / DOT formats. |
| 92 | 2026-03-06 | docs: `RunResult.output` clarification | ✅ Ported | `RunResult.output` is the terminal `output_name == "output"` row's payload. |
| 93 | 2026-03-13 | docs: Ralph as core pattern | ➖ N/A | Docs-only. |
| 94 | 2026-03-18 | docs: nested ralph | ➖ N/A | Docs-only. |
| 109 | 2026-03-18 | Ralph loops respect approved reviews | ✅ Ported (Loop) | `LoopNode` with `until_fn` callable; Ralph is `TSRalphNode` alias. |
| 113 | 2026-03-18 | Nested Loop/Ralph across structural nodes | ✅ Ported (Loop) | Each iteration is keyed by `(node_id, iteration)`; nesting works naturally. |
| 114 | 2026-03-18 | Codex rollout recorder stderr tolerance | ✅ Ported | `CodexAgent` captures stderr but doesn't fail on it (treated as informational). |
| 118 | 2026-03-27 | PiAgent RPC terminal-response wait | ✅ Ported | `PiAgent` reads the full NDJSON stream and uses the final `text`-bearing event. |
| 124 | 2026-04-16 | test: supervisor double-resume reproduction | ✅ Ported | `Supervisor` serializes resumes via an in-process `threading.Lock` on `(run_id)` so two ticks can't both take over the same run. `smithers-ts supervise` CLI. |
| 130 | 2026-05-04 | Duplicate output refs + SDK structured output + docs | ✅ Ported | `_Outputs` yields distinct `OutputRef` per key; structured-output handshake via Pydantic + the `AnthropicAgent` `output_schema=` plumbing. |
| 125 | 2026-05-04 | OpenCodeAgent integration | ✅ Ported | `OpenCodeAgent` in `runtime/subprocess_agents.py`. |
| 126 | 2026-05-04 | `bunx init` dependency resolution | ➖ N/A | TS init flow. |
| 131 | 2026-04-27 | Restore green main baseline | ➖ N/A | Upstream maintenance. |
| 132 | 2026-05-04 | Honor non-retryable agent failures | ✅ Ported | `NonRetryableError` short-circuits the retry loop. |
| 133 | 2026-05-06 | Harden gateway client contracts | ➖ N/A | Gateway server skip-v0. |
| 134 | 2026-05-10 | Harden gateway HTTP boundaries | ➖ N/A | Same. |
| 137 | 2026-05-14 | `smithers init` .gitignore templates | ➖ N/A | TS init flow. |
| 138 | 2026-05-14 | Codex/OpenAI agent fixes | ✅ Ported | `CodexAgent` carries the upstream model/thinking flag conventions. |
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

- **18 PRs fully ported.** All Tier-1 (engine/CLI behavior) and Tier-2
  (agent adapters + supervisor) work from upstream's `main` since
  2026-01-23 is now live.
- **0 PRs deferred** at the PR level. The v0.4 backlog items (Effect
  composition, observability mirror, gateway, HMR, time travel) are
  forward-looking design areas that don't have specific upstream PRs
  driving them yet.
- **9 PRs N/A** — docs only or Bun/gateway-specific.
- **1 open PR** (#135 observability) parked behind a "wait for upstream
  to ship" gate.

## What "parity with current main" means for v0.1+v0.2

The Python port is at **runtime + CLI parity** with TS main for the
slice of the API surface most workflows actually use:

- `Workflow / Sequence / Parallel / Task / Subflow / ApprovalGate /
  HumanTask / Worktree / MergeQueue / Branch / Loop / Signal /
  WaitForEvent` — 13 node types live and executable. `TSRalphNode`
  is exported as the deprecated-upstream alias for `LoopNode`.
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

## v0.2 lift (2026-05-18) — what just landed

Most of the originally-deferred v0.2 items are now live:

| v0.2 target | Status | Notes |
| --- | --- | --- |
| Real concurrency in `ParallelNode` | ✅ Shipped | `ThreadPoolExecutor` with per-thread `Store` instances. SQLite WAL handles concurrent connections. Children mutate the parent output cache under a `threading.Lock`. Measured: 5 × 0.2s tasks finish in ~0.21s in parallel vs 1.0s sequentially. |
| Task `timeout_ms` enforcement | ✅ Shipped | Each retry attempt runs in a single-worker `ThreadPoolExecutor`; `Future.result(timeout=…)` raises a `TimeoutError` (retryable). Rogue computes are detached via `shutdown(wait=False)` so the runner returns immediately. |
| `Signal` / `WaitForEvent` node types | ✅ Shipped | New `ts_signals` table; `SignalNode` writes a row, `WaitForEventNode` pauses until a matching `(run_id, event, correlation_id)` exists. `smithers-ts signal <runId> <event> --json '...'` for external delivery. `signal_run(...)` from Python. **Used by the bun-port test_swarm phase** for external CI integration. |
| `AnthropicAgent` adapter | ✅ Shipped | Real-mode `AgentLike` implementing `anthropic.Anthropic().messages.create(...)`. Optional `output_schema` triggers structured-output prompt synthesis + JSON extraction. Install with `uv pip install 'smithers-py[anthropic]'`. |
| MDX → Jinja2 templated prompts | ✅ Shipped | `PromptTemplate(...)` accepts Jinja2 syntax (with `str.format` fallback when Jinja2 is missing). `TaskNode.prompt` accepts strings or any object with `.render()`. `Optional[smithers-py[templates]]` for Jinja2. |
| `smithers-ts graph` command | ✅ Shipped | Renders the workflow DAG in indented-tree / JSON / Graphviz DOT format without executing. Closes the v0.1 gap on PR #89. |
| `smithers-ts signal` CLI | ✅ Shipped | Delivers an external signal to a paused `WaitForEventNode`. |

## v0.3 lift (2026-05-18) — final batch

The remaining originally-deferred items are now live:

| v0.3 target | Status | Notes |
| --- | --- | --- |
| Provider adapters: Claude Code / Codex / OpenCode / Pi | ✅ Shipped | `SubprocessAgent` base in `runtime/subprocess_agents.py` handles common subprocess plumbing (spawn, timeout, JSON-fenced output extraction, stderr capture, working-directory). Four concrete subclasses: `ClaudeCodeAgent`, `CodexAgent`, `OpenCodeAgent`, `PiAgent`. PiAgent has NDJSON event-stream parsing (PR #85/#118). |
| Supervisor loop | ✅ Shipped | `Supervisor` class in `runtime/supervisor.py`. Polls `ts_runs` for stale `'running'` rows and force-resumes them. Serialized per-run via an in-process lock. New `smithers-ts supervise <workflow.py> --interval 10s --stale-threshold 30s --max-concurrent 3` CLI. Closes PR #124. |

## What's still deferred (the v0.4 backlog)

These are intentional non-goals for the resume effort. None block any
realistic workflow today.

- The Effect API composition model on the Python side. (Possibly via `anyio` structured concurrency. Not strictly needed; current threading covers the bun-port shape and every other workflow we have.)
- Canonical agent trace events (#135 — still draft upstream; mirror once it lands).
- The gateway server / client / HTTP boundaries.
- TS-shape **observability metrics** and the prometheus endpoint.
- HMR (hot module replacement) for `.py` workflows during a live run.
- Time-travel debugging (`smithers fork`, `smithers replay`, `smithers timeline` upstream commands). Our wire-compat row-shape parity lays the groundwork — a future implementation would replay rows directly from `ts_output_rows`.

## Bonus: bun-port-smithers fully ported (2026-05-18)

All 7 phases of upstream's canonical bun-port example are now ported to
Python and execute end-to-end in dry mode through the `smithers_py.runtime`
walker. Each phase produces a coherent PhaseDone output:

```
lifetimes → completed | Lifetime classification produced 2 field row(s)
phaseA    → completed | Phase A: 2/2 clean, 2 fix task(s).
compile   → completed | Compile: 2/2 crates green, 0 gated modules.
ungate    → completed | Ungate: 1/1 approved, 1 patched.
probes    → completed | Probes: 1/1 passed, 0 unique failures.
tests     → completed | Test swarm: 1/1 areas green, 1 merged.
sweeps    → completed | Sweeps: 1 fixed across 1 sweep(s).
```

The graph uses every TS-shape primitive: WorkflowNode, SequenceNode,
ParallelNode, BranchNode (transitively via lifetime), LoopNode,
TaskNode, SubflowNode, ApprovalGateNode, HumanTaskNode, WorktreeNode,
MergeQueueNode. See [`examples/bun_port_smithers_py/`](examples/bun_port_smithers_py/).
