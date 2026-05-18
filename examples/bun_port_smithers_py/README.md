# bun-port-smithers-py

Python port of [`examples/bun-port-smithers/`](../bun-port-smithers/). Uses
the TS-shape components added to `smithers_py` on the `port/resume` branch:
`Workflow`, `Sequence`, `Parallel`, `Task`, `Subflow`, `ApprovalGate`,
`HumanTask`, `Worktree`, `MergeQueue`, plus the `create_smithers` facade.

The TS reference (and Cory's tweet thread on the design) is the canonical
spec. The goal of this Python port is *wire compatibility*: every node, every
output row, every approval gate behaves identically across runtimes, with
identical SQLite row shape.

## Status — full port complete

| Piece | Status |
| --- | --- |
| `workflow.py` (top-level) | **Wired** — all 7 phase Subflows point at real workflows |
| `workflows/lifetime_classify.py` | ✅ Ported |
| `workflows/phase_a_port.py` | ✅ Ported (per-file implement/verify/fix Sequence in Parallel) |
| `workflows/crate_compile_bringup.py` | ✅ Ported (per-tier Sequence of per-crate compile Loops) |
| `workflows/ungate_proper_port.py` | ✅ Ported (per-target Loop with patch + 2-reviewer Parallel + decision) |
| `workflows/panic_probe_swarm.py` | ✅ Ported (build → Parallel probes → dedupe → report Loop) |
| `workflows/test_swarm.py` | ✅ Ported (per-area Loop + Worktree + MergeQueue) |
| `workflows/audit_sweeps.py` | ✅ Ported |
| `components/schemas.py` | ✅ Full — every Zod schema mirrored as Pydantic |
| `components/porting_rules.py` | ✅ Stable ids, cache keys, sampling, TSV synth, plus new helpers for normalize_port_files, plan_crates_by_tier, dedupe_failures, survey_targets, survey_sweeps |
| `components/agents.py` | Dry-mode for all 16 agents. Real-mode adapter pending v0.2 (AgentLike protocol is in place). |
| `components/scorers.py` | Stub (returns empty list). Real per-task scorer hooks land in v0.2. |
| Engine dispatch on every TS-shape primitive | ✅ Workflow / Sequence / Parallel / Branch / Loop / Task / Subflow / ApprovalGate / HumanTask / Worktree / MergeQueue all execute end-to-end |
| Cross-runtime row parity | ✅ Verified on the `examples/wire_compat/` canonical workflow — same SQLite row set as upstream TS |

Run the full 7-phase port in one command:

```bash
cd /Users/luis/smithers/smithers_py
uv run python -c "
import sys; sys.path.insert(0, '/Users/luis/smithers')
from examples.bun_port_smithers_py.workflow import bun_port_workflow
from smithers_py import run_workflow
result = run_workflow(bun_port_workflow, input={
    'repo': '/tmp/bun', 'requireOperatorPlan': False,
    'phases': ['lifetimes','phaseA','compile','ungate','probes','tests','sweeps'],
    'files': [{'zig':'src/http/http.zig','crate':'http','loc':1200}],
    'crates': [{'name':'http','tier':0}],
    'targets': [{'id':'http-server','crate':'http','file':'src/http/lib.rs'}],
    'probes': [{'id':'cli-help','cmd':'--help'}],
    'areas': [{'id':'bun-http','glob':'test/js/bun/http/','crate':'http'}],
    'sweeps': [{'id':'todo-sweep','kind':'todo','pattern':'TODO','scope':'src/'}],
    'useWorktrees': False,
}, db_path='/tmp/bun.db')
print(result.status, result.output['summary'])
"
```

## Running

```bash
# First call — runs the lifetimes phase, pauses at the ApprovalGate.
smithers-ts up examples/bun_port_smithers_py/workflow.py \
    --workflow bun_port_workflow \
    --input '{"repo":"/tmp/bun-rust-port","files":[{"zig":"src/http/http.zig","crate":"http","loc":1200}],"phases":["lifetimes"]}' \
    --db /tmp/bun.db

# Approve the gate.
smithers-ts approve <runId> --note "ok" --by "you" --db /tmp/bun.db

# Resume — completes the remaining phase placeholders and writes the
# terminal smithers-bun-port-py-final-v0 row.
smithers-ts up examples/bun_port_smithers_py/workflow.py \
    --workflow bun_port_workflow --run-id <runId> --resume --db /tmp/bun.db

# See the full run state.
smithers-ts inspect <runId> --db /tmp/bun.db
```

## Graph construction smoke (no runtime)

```bash
cd /Users/luis/smithers/smithers_py
uv run python -c "
from smithers_py.nodes.ts_compat import WorkflowNode
from examples.bun_port_smithers_py.workflow import bun_port_workflow, CONFIG
from examples.bun_port_smithers_py.components.schemas import BunPortInput, ZigFileInput

class Ctx:
    pass
ctx = Ctx()
ctx.input = BunPortInput(
    repo='/tmp/bun-rust-port',
    files=[ZigFileInput(zig='src/http/http.zig', crate='http', loc=1200)],
    phases=['lifetimes'],
    requireOperatorPlan=False,
)
graph = bun_port_workflow(ctx)
assert isinstance(graph, WorkflowNode)
print('Graph built:', graph.name)
print('Node types:', [c.type for c in graph.children[0].children])
"
```

## Conceptual map: TS → Python

| TS reference | Python equivalent |
| --- | --- |
| `<Workflow name="bun-port-smithers">` | `WorkflowNode(name="bun-port-py", children=[...])` |
| `<Sequence>...</Sequence>` | `SequenceNode(children=[...])` |
| `<Parallel maxConcurrency={N}>` | `ParallelNode(max_concurrency=N, children=[...])` |
| `<Task id="x" output={outputs.X} agent={a}>...</Task>` | `TaskNode(id="x", output=outputs.X, agent=a, prompt="...")` |
| `<Subflow id="x" output={outputs.Y} workflow={wf} input={...}/>` | `SubflowNode(id="x", output=outputs.Y, workflow=wf, input={...})` |
| `<ApprovalGate id="x" when={...} request={...} onDeny="fail"/>` | `ApprovalGateNode(id="x", when=True, request=ApprovalRequest(...), on_deny="fail")` |
| `<HumanTask id="x" output={outputs.X} prompt={...}/>` | `HumanTaskNode(id="x", output=outputs.X, prompt=...)` |
| `<Worktree path={...} branch={...}/>` | `WorktreeNode(path=..., branch=...)` |
| `<MergeQueue maxConcurrency={1} requireGreen={true}/>` | `MergeQueueNode(max_concurrency=1, require_green=True)` |
| `createSmithers({input, output, ...})` | `create_smithers(schemas={"input": ..., "output": ...})` |
| `outputs.foo` | `config.outputs.foo` |
| MDX prompts (`<LifetimePrompt repo={...}>`) | f-strings or Jinja2 templates returning the same body |

## Why this is "the demo that matters"

Cory's tweet thread:

> "The bun rewrite is some of the most impressive harness engineering I've
> seen. @jarredsumner basically first invented his own minimal version of
> Smithers and then thoughtfully created what is a high quality zig to
> rust compiler utilizing llms."

If `smithers-py` can host the same workflow with the same approval gates
and the same SQLite row shape, the Python port is a real peer to the TS
runtime — not just a parallel toy. That's the acceptance criterion.
