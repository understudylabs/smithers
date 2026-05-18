# Python port — resume notice

This fork of [`smithersai/smithers`](https://github.com/smithersai/smithers)
is the working area for **resuming the Python port** of Smithers, which
lives on upstream's `python` branch at `v1.0.0` and has been quiet since
**2026-01-23**.

The resume effort is a community contribution from
[Understudy Labs](https://understudylabs.com). The aim is to bring
`smithers_py/` forward to parity with the current TS `main` branch (~v0.20+
as of writing) and then PR the result back to `smithersai/smithers:python`.

## Scope

In scope for the first pass:

- Catch the `smithers_py/` runtime up to current TS `main`'s public API.
- Maintain the existing v1.0.0 design choices: 7-phase tick loop,
  decorator + JSX-like `jsx(...)` node trees, SQLite durable state with
  transitions audit log, PydanticAI-backed agent executors.
- Add wire-compatibility tests: SQLite rows produced by the Python runtime
  match those produced by the TS runtime, column-for-column, on a shared
  set of canonical workflows. Cross-runtime resume (start a run in TS,
  approve in Python; or the reverse) is the acceptance criterion.

Explicitly *not* in scope for the first pass:

- Reinventing the paradigm. The decorator + `jsx` choice on the `python`
  branch is preserved.
- Porting upstream packages outside the existing `smithers_py/` surface
  (gateway, server, sandbox, openapi, devtools, observability,
  react-reconciler). These remain TS-only for now.
- Publishing to PyPI under a new name. Any PyPI publishing happens with
  upstream coordination; until then this is a fork.

## Working branches

| Branch | Purpose |
| --- | --- |
| `main` | tracks `smithersai/smithers:main` (current TS Smithers). |
| `python` | tracks `smithersai/smithers:python` (untouched). |
| `port/resume` | active work — resumed Python port. PR target: `smithersai/smithers:python`. |

## Method

The catch-up runs through a Smithers meta-workflow modeled on
[`examples/bun-port-smithers/`](examples/bun-port-smithers/) — same phase
shape (api-classify, paradigm-design, phase-a-port,
module-import-bringup, wire-compatibility-test, smoke-port, audit-sweeps),
re-pointed at TS→Python delta classification rather than full file
translation, since the existing `smithers_py/` already supplies most of the
target shape.

The meta-workflow itself is open work and will land alongside the port
output in this fork.

## Coordination with upstream

Outreach to upstream maintainers has been sent. Until they respond, the
working assumption is "friendly fork": all changes attribute clearly,
this `PORT_RESUME.md` and the banner in
[`smithers_py/README.md`](smithers_py/README.md) link prominently back to
[`smithersai/smithers`](https://github.com/smithersai/smithers), and we
avoid taking any action that would be hard to unwind if upstream prefers
a different shape.

## Status

This is **alpha-quality work in progress** and not ready for production
use. The original `v1.0.0` self-described as "Alpha - not ready for
production use," and that remains accurate for the resumed port too.

### Baseline health check (2026-05-18)

The upstream `python` branch is **not bit-rotted**. As of the resume
notice landing:

- `uv sync` from `smithers_py/pyproject.toml` resolves cleanly on Python
  3.12. No vendoring tricks, no overrides.
- `import smithers_py` succeeds at module import. 30+ public symbols are
  visible from the package root.
- `pytest --ignore=e2e` runs **645 tests passed, 1 skipped, 0 failures**
  in ~9s on a 2025-vintage Mac. No environment-specific fixtures are
  required to reach green.

So the catch-up effort is **API delta against current TS `main`**, not
"un-rot a stale port." This is the cheapest version of the work. The
gating questions are:

1. Which public TS API surfaces shipped after 2026-01-23 (the `python`
   branch's last touch) and which of them have user-visible Python
   analogues that need to land?
2. Does the SQLite row shape still match current TS Smithers? (Wire-
   compatibility is the bar for cross-runtime resume.)
3. Are there design deltas (not just additions) on `main` that the
   `python` branch should follow, or did `smithers_py` v1.0.0 lock in a
   shape that should stay frozen?

We won't try to answer (1)–(3) without upstream's input first. Outreach
is in flight.

### API surface delta (the actually-important finding)

Spot-check against the public `bun-port-smithers/` example on current TS
`main`: that workflow is built from `Workflow`, `Sequence`, `Parallel`,
`Task` (with typed output schemas), `Subflow`, `ApprovalGate`, `HumanTask`,
`Worktree`, `MergeQueue`.

`smithers_py` v1.0.0 exposes a different taxonomy: `IfNode`, `PhaseNode`,
`StepNode`, `RalphNode`, `WhileNode`, `FragmentNode`, `EachNode`,
`ClaudeNode`, `EffectNode`. None of the TS components above have direct
Python analogues today.

So the catch-up is **not** "translate a few new files" — it's a design
question. The two honest possibilities:

- **TS shape is canonical going forward.** `smithers_py` adds `Sequence`,
  `Parallel`, `Task`, `Subflow`, `ApprovalGate`, `HumanTask`, etc., maps
  them onto the existing tick-loop engine, deprecates `Phase`/`Step`/`Ralph`
  (or aliases them). This is the most surface-area to add.
- **`smithers_py` shape is intentional and stays.** The TS-side
  `Sequence`/`Parallel`/`Task` are syntactic sugar that compile down to
  the same Phase/Step/Ralph primitives at the engine level. Catch-up means
  building TS→Python workflow *translation* (and a thin `bun-port-py`
  example that uses the Python primitives) rather than adding new
  components.

Without upstream's input we don't know which. The DM should probably
include this exact question:

> "Looking at the gap between `smithers_py`'s `Phase/Step/Ralph` model
> and main's `Workflow/Sequence/Task/Subflow` model — was the v1.0.0
> design intentional, or did `main` evolve past it? Is the right resume
> path to add the TS shape into `smithers_py`, or to keep `smithers_py`'s
> primitives and translate workflows?"

Demo target once that's resolved: a Python port of
[`examples/bun-port-smithers/`](examples/bun-port-smithers/) living at
`examples/bun_port_smithers_py/`. Same phases (lifetimes, phase-A, compile,
ungate, probes, tests, sweeps), same gates, same scorers — but using
`smithers_py`. If that workflow runs end-to-end on a Bun checkout and
produces SQLite rows the TS Smithers CLI can also `approve` and
`inspect`, the resume is real.

### Update: API surface added (2026-05-18)

The TS shape is now wired into `smithers_py` ahead of upstream's input —
the user authorized "match current main." The catch-up went the
"add TS shape into smithers_py" route, not the "translation layer" route.

Landed on `port/resume`:

- **9 new node types** in `smithers_py.nodes.ts_compat`:
  `WorkflowNode`, `SequenceNode`, `ParallelNode`, `TaskNode`, `SubflowNode`,
  `ApprovalGateNode`, `HumanTaskNode`, `WorktreeNode`, `MergeQueueNode`.
  Each is a Pydantic model on the existing `NodeBase`, registered in the
  discriminated union, accepts both snake_case and camelCase keyword
  arguments (so TS-style call sites port verbatim).
- **`create_smithers` facade** (`smithers_py.facade`) that mirrors the TS
  `createSmithers({input, output, ...schemas}, {dbPath})` ergonomics.
  Returns a `SmithersConfig` with a `.outputs` namespace of typed
  `OutputRef`s and a `@config.workflow` decorator. `createSmithers` is
  exported as a camelCase alias.
- **34 new tests** in `nodes/test_ts_compat.py` and `test_facade.py`.
  Total suite now **679 passed, 1 skipped, 0 failures** in ~10s (up from
  the 645 baseline; zero regressions on existing engine tests).
- **`examples/bun_port_smithers_py/`** — Python port of the canonical
  bun-port workflow:
    - `components/schemas.py` — Pydantic mirrors of every Zod schema, with
      fractional metrics nested under `metrics` for cross-runtime row-shape
      parity.
    - `components/agents.py` — dry-mode + real-mode-stub agent bundle, 16
      named agents matching the TS reference 1:1.
    - `components/porting_rules.py` — stable node ids, field keys, cache
      keys, sampling, TSV synthesis. Deterministic; no LLM.
    - `workflows/lifetime_classify.py` — Phase 1 (the lifetime classifier
      that Cory describes as "the most important part") fully ported as
      a typed graph.
    - `workflow.py` — top-level workflow scaffolding all 7 phases as
      Subflows with the post-lifetimes ApprovalGate wired.

The graph constructs cleanly. Execution requires engine dispatch on the
new node types (`task`, `subflow`, `approval_gate`, `human_task`,
`worktree`, `merge_queue`) — that's the next chunk of work.

### MVP shipped (2026-05-18)

A working runtime — `smithers_py.runtime` — that walks TS-shape graphs
and executes them. Independent from the v1.0.0 tick loop; both engines
coexist.

```bash
# 30-second demo
smithers-ts up examples/hello_smithers_ts/workflow.py \
    --input '{"name":"luis"}' --db /tmp/demo.db
# → pauses at the ApprovalGate

smithers-ts approve <runId> --note "lgtm" --by "you" --db /tmp/demo.db

smithers-ts up examples/hello_smithers_ts/workflow.py \
    --run-id <runId> --resume --db /tmp/demo.db
# → completes with hello-final-v0 row

smithers-ts inspect <runId> --db /tmp/demo.db
# → full run state, output rows, terminal payload
```

The same flow works against the bun-port example:

```bash
smithers-ts up examples/bun_port_smithers_py/workflow.py \
    --workflow bun_port_workflow \
    --input '{"repo":"/tmp/bun","files":[{"zig":"src/http.zig","crate":"http","loc":1200}],"phases":["lifetimes"]}' \
    --db /tmp/bun.db
# → pauses at the post-lifetimes ApprovalGate

smithers-ts approve <runId> --db /tmp/bun.db
smithers-ts up examples/bun_port_smithers_py/workflow.py \
    --workflow bun_port_workflow --run-id <runId> --resume --db /tmp/bun.db
# → completes, terminal smithers-bun-port-py-final-v0 row emitted
```

#### What MVP covers

- `WorkflowNode`, `SequenceNode`, `ParallelNode`, `TaskNode` (render +
  duck-typed agent), `SubflowNode` (with child run isolation),
  `ApprovalGateNode` (pause/resume + on_deny fail/continue),
  `HumanTaskNode` (always pause), `WorktreeNode`, `MergeQueueNode`
  (structural pass-through).
- Pydantic schema validation on every output row.
- SQLite-durable rows in three new tables (`ts_runs`, `ts_output_rows`,
  `ts_approvals`). Resume reads previous output rows and skips already-
  done tasks (idempotent).
- CLI: `smithers-ts up | approve | deny | inspect | ps`.
- 12 runtime e2e tests + 34 unit tests for nodes/facade. Total suite:
  **691 passed, 1 skipped, 0 failures**.

#### What MVP doesn't cover (the v0.2+ backlog)

1. **Concurrency.** `ParallelNode` runs children sequentially today.
   Real concurrent execution within a frame is the next iteration.
2. **Real-mode agents.** TaskNode dispatches to any object with a
   `.generate(prompt=...)` method, which is enough for dry-mode and a
   thin Anthropic SDK wrapper, but we don't ship Claude Code / Codex
   adapters yet. Drop-in shape will come from the v1.0.0 `executors/`
   package once we decide on unification.
3. **Worktree / MergeQueue semantics.** Honored structurally
   (children run under them) but no real git worktree creation or merge
   serialization. This is the bigger lift for the bun-port end-to-end.
4. **Cross-runtime row diff.** The acceptance criterion. Once a real TS
   run dumps its `output_rows` we diff against a Python run on the same
   fixture. Some column names differ (we use `ts_output_rows` while TS
   uses per-schema tables). The shape map is documented but the test
   harness isn't written yet.
5. **Full bun-port port.** Only lifetime-classify is fleshed out; the
   other six phases are placeholder Subflows pointing at lifetime-classify
   so the parent graph constructs.

### Next concrete steps

1. **Cross-runtime row diff harness.** Write a TS workflow + Python
   workflow that produce identical conceptual rows; compare. This is the
   single most important test to write next.
2. **Concurrency for `ParallelNode`.** Thread pool, asyncio, or
   `concurrent.futures` — pick one, wire it.
3. **Real Anthropic agent adapter.** Port `executors/claude.py` (already
   working in v1.0.0) to the duck-typed `.generate()` interface the
   runtime expects.
4. **Fill in phases 2–7** of the bun-port example as each engine
   capability is unblocked.

## License & attribution

Smithers is MIT-licensed. All resume work in this fork is MIT-licensed and
attributes upstream as the source. The original Python port at
`v1.0.0` was authored upstream; this fork preserves the LICENSE file
verbatim and credits the upstream maintainers as the originators of the
design.

## Contact

For questions about the resume effort specifically: open an issue on
[`understudylabs/smithers`](https://github.com/understudylabs/smithers/issues).
For upstream Smithers itself: please direct to
[`smithersai/smithers`](https://github.com/smithersai/smithers).
