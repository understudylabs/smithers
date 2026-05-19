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
the workflow. Four runs cleared all four. Verification script:
[`scripts/verify-subsystem.sh`](./examples/smithers-port-py/scripts/verify-subsystem.sh).

## The shape

One Smithers workflow with exactly one Task. The Task is bound to
`ClaudeCodeAgent`, which wraps a local `claude` subprocess with the
standard file tools (`Read`, `Write`, `Edit`, `Bash`, `Grep`,
`Glob`). The agent is handed a markdown spec describing the
subsystem, a target directory, and a list of files-with-hints. Its
job is to produce the files, verify them, and return a JSON
manifest. The workflow does almost nothing besides invoking the
agent and recording the result. No fan-out, no per-file
parallelism, no orchestration ceremony.

## The loop, in order

The agent makes the target directory. It writes the first file
from the spec. Before writing a file that references symbols from
another file in the subsystem, it `Read`s that file to confirm the
exact export names. When all files exist, it runs `python -c "from
<namespace>.<subsystem> import *"` to confirm the public surface
imports cleanly. If that fails it `Edit`s the offending file. Then
it runs `pytest` against the generated test file. If tests fail it
edits source or tests and re-runs. It iterates up to five times.
Only when both checks pass does it return the manifest. By the
time the workflow records "done", the code has already passed its
own tests.

## Why parallel fan-out didn't work

An earlier attempt — `port-subsystem.tsx`, API mode — ran one
`AnthropicAgent` call per file in parallel. Each call returned a
JSON string with file contents; the workflow wrote them to disk in
a fan-in task. The PR #88 demo run produced the predictable
failure: `app.py` invented function names like
`create_auth_dependency()`, but `auth.py` — written by a different
agent in a different process — invented `auth_dependency`.
Independent agents with no shared state can't agree on shared
symbols. They each guess; the guesses don't match; the imports
break.

The CLI-mode pattern collapses N parallel agents into one
sequential agent with persistent state. The state is the
filesystem. The agent reads its own previous output before
producing the next file, so cross-file naming agrees by
construction, not by convention.

## Why in-loop testing is the load-bearing piece

These ports aren't first-run-mergable because Sonnet writes
flawless code — it doesn't. They're first-run-mergable because the
agent is its own first reviewer. When `pytest` shows a failure the
agent reads the failing test, locates the bug, edits the file, and
re-runs. The draft → review → fix cycle that would normally need a
human collapses inside a single agent session. The workflow
surface only sees the post-iteration result.

## Two failure modes recorded

**Memory v1 and v2 (shortcut).** First two memory runs made zero
`Write` tool calls. The agent saw the existing hand-coded
`smithers_py/memory/`, ran its tests, declared the port complete,
returned the manifest. Technically faithful to "test_memory.py
passes"; not a port. v3 fixed this by adding two things to the
prompt: a prohibition on reading `smithers_py/<subsystem>/`, plus
an acceptance contract requiring three specific Bash invocations
(`ls`, import smoke, `pytest`) before the final JSON is allowed.

**Scorers (target override).** Agent followed the new
read-prohibition but wrote its output to `smithers_py/scorers/`
anyway, clobbering the hand-coded version. `appliedPath` confirmed
the override; the working tree confirmed the writes. The agent had
indexed the spec body's example imports — code blocks like `from
smithers_py.scorers import X` — and used those paths as the
target, not the workflow's `pythonTargetDir` input. The cache run
(next subsystem) sidestepped this by rewriting the spec to use
`smithers_py_meta.cache` in every example. That landed clean.

The order of precedence the agent actually honors, lowest to
highest:

  1. `pythonTargetDir` in the workflow input
  2. Prompt-level prohibitions ("do not read X")
  3. Example imports in the spec body
  4. The acceptance contract's Bash commands

Anything in (3) overrides anything in (1) or (2). The cache run
confirmed it; the scorers run discovered it.

## What got measured vs guessed

**Measured:** LoC, test pass counts, run IDs, commit shas, the
precedence order the agent honors.

**Guessed:** Per-run cost. The Claude Code session emits a
`total_cost_usd` field on its final `result` event, but Bun's
stdout pipe truncated the stream before the workflow could persist
it. From the work envelope (single tool loop, ≤1.5k LoC output, no
retries), each run is in the $0.30–$0.80 band on Sonnet 4.5 list
pricing — inside the $1 acceptance bound but not directly
observed. Capturing the cost field reliably is a known follow-up.

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

## What this is a pattern for

Not "give the LLM a vague goal". It's "give one agent the full
file-tools surface plus a precise spec plus a programmatic
acceptance gate." Three ingredients have to be present together:

A single agent with persistent filesystem state, so cross-file
consistency is mechanical not negotiated. A programmatic test gate
the agent itself can run, so quality is verified inside the loop
not on the way out. A spec whose example code matches the actual
target paths, so the agent's prior doesn't override the workflow
input.

Drop any one and the failure mode is concrete and reproducible:
parallel-name-drift, code-that-looks-right-but-doesn't-test, or
agent-targets-the-wrong-directory. With all three, a whole
subsystem ports in one shot at under a dollar.

## Open

- Re-fire scorers with the meta-namespaced spec. Known fix.
- Capture the Claude Code session cost field reliably so the cost
  numbers stop being inferred.
- Diff hand-coded vs meta for memory / tools / cache — find what
  the agent omitted.
- Wire `cache.by` into `runtime/runner.py` and `memory={...}` into
  `TaskNode`. The modules exist but aren't called from the task
  execution path yet.
