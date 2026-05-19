# Recursive port proof

Ported four `smithers_py` subsystems by running a Smithers workflow
that drives a `ClaudeCodeAgent` against a markdown spec. The agent
writes Python directly, runs the generated `pytest` suite, iterates
on failures, and exits with a manifest. Each port commits under a
`meta-workflow:` prefix naming the run that produced it.

| Subsystem | Run ID                | Output path                   | LoC   | Tests  | Commit     |
| ---       | ---                   | ---                           | ---   | ---    | ---        |
| serve     | `port-serve-cli-v2`   | `smithers_py/serve/`          | 756   | 12/12  | `d214662b` |
| memory    | `port-mem-cli-v3`     | `smithers_py_meta/memory/`    | 1,004 | 16/16  | `f4764a53` |
| tools     | `port-tools-cli`      | `smithers_py_meta/tools/`     | 450   | 35/35  | `63f9e82d` |
| cache     | `port-cache-cli`      | `smithers_py_meta/cache/`     | 669   | 21/21  | `be6554f4` |
| scorers   | `port-scorers-cli`    | discarded — see below         | —     | —      | —          |

Acceptance per goal (`/goal` set 2026-05-18): import-from-namespace
works, tests pass, ≤$1/run on Sonnet 4.5, commit message identifies
the workflow. Four runs cleared all four. Run script:
[`scripts/verify-subsystem.sh`](./examples/smithers-port-py/scripts/verify-subsystem.sh).

## Two failure modes worth recording

**Memory v1/v2 (shortcut).** First two memory ports made zero `Write`
tool calls. The agent saw the hand-coded `smithers_py/memory/`, ran
its tests, declared the port complete, returned the manifest.
Technically faithful to "test_memory.py passes"; not a port. Fixed in
v3 by adding to the prompt: a prohibition on reading
`smithers_py/<subsystem>/`, plus an acceptance contract requiring
three specific Bash invocations (`ls`, import smoke, `pytest`) before
the final JSON is allowed.

**Scorers (target override).** Agent followed the new
read-prohibition but wrote its output to `smithers_py/scorers/`
anyway, clobbering the hand-coded version. `appliedPath` confirmed
the override; the working tree confirmed the writes. The agent had
indexed the spec's example imports (`from smithers_py.scorers import
...`) and used those paths as the target, not the workflow's
`pythonTargetDir` input. Cache (next run) sidestepped this by
rewriting the spec to use `smithers_py_meta.cache` in every example.
That landed clean.

Order of precedence the agent actually honors, lowest to highest:

  1. `pythonTargetDir` in the workflow input
  2. Prompt-level prohibitions ("do not read X")
  3. Example imports in the spec body
  4. The acceptance contract's Bash commands

Anything in (3) overrides anything in (1) or (2). The cache run
confirmed it; the scorers run discovered it.

## What got measured vs guessed

**Measured:** LoC, test pass counts, run IDs, commit shas, the order
in which the agent honors instructions.

**Guessed:** Per-run cost. The Claude Code session emits a
`total_cost_usd` field on its final `result` event, but Bun's stdout
pipe truncated the stream before the workflow could persist it. From
the work envelope (single tool loop, ≤1.5k LoC output, no retries),
each run is in the $0.30–$0.80 band on Sonnet 4.5 list pricing —
inside the $1 acceptance bound but not directly observed. Capturing
the cost field reliably is a known follow-up.

## Hand-coded vs meta-generated, by the numbers

| Subsystem | Hand-coded LoC | Meta LoC | Hand-coded tests | Meta tests |
| ---       | ---            | ---      | ---              | ---        |
| memory    | 1,209          | 1,004    | 18               | 16         |
| tools     | 1,467          | 450      | 35               | 35         |
| cache     | 562            | 669      | 18               | 21         |

Meta total ~35% smaller in aggregate, driven mostly by tools (the
meta version omitted defensive helpers the hand-coded version
carries). Whether that's "leaner" or "missing edge cases" needs a
functional diff that hasn't been run yet.

## Open

- Re-fire scorers with the meta-namespaced spec. Known fix.
- Capture the Claude Code session cost field reliably so the cost
  numbers stop being inferred.
- Diff hand-coded vs meta for memory / tools / cache — find what the
  agent omitted.
- Wire `cache.by` into `runtime/runner.py` and `memory={...}` into
  `TaskNode`. The modules exist but aren't called from the task
  execution path yet.
