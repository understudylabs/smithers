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
