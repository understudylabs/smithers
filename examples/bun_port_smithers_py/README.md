# bun-port-smithers-py

Python port of [`examples/bun-port-smithers/`](../bun-port-smithers/). Uses
the TS-shape components added to `smithers_py` on the `port/resume` branch:
`Workflow`, `Sequence`, `Parallel`, `Task`, `Subflow`, `ApprovalGate`,
`HumanTask`, `Worktree`, `MergeQueue`, plus the `create_smithers` facade.

The TS reference (and Cory's tweet thread on the design) is the canonical
spec. The goal of this Python port is *wire compatibility*: every node, every
output row, every approval gate behaves identically across runtimes, with
identical SQLite row shape.

## Status

| Piece | Status |
| --- | --- |
| `workflow.py` (top-level) | Scaffolded with all 7 phases as Subflow placeholders |
| `workflows/lifetime_classify.py` | Ported (graph shape + deterministic helpers) |
| `workflows/phase_a_port.py` | Not yet ported |
| `workflows/crate_compile_bringup.py` | Not yet ported |
| `workflows/ungate_proper_port.py` | Not yet ported |
| `workflows/panic_probe_swarm.py` | Not yet ported |
| `workflows/test_swarm.py` | Not yet ported |
| `workflows/audit_sweeps.py` | Not yet ported |
| `components/schemas.py` | Lifetime + top-level schemas only |
| `components/agents.py` | Dry-mode for every agent name (16 total). Real-mode warns and falls back to dry until `smithers_py` engine learns `TaskNode.agent` dispatch. |
| `components/porting_rules.py` | Stable node ids, field keys, cache keys, sampling, TSV synth |

The graph constructs and validates cleanly today. End-to-end execution
requires engine work in `smithers_py.engine` to dispatch on the new node
types (`task`, `subflow`, `approval_gate`, `human_task`) — that's the
next chunk of the resume effort.

## Running (graph construction smoke)

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
