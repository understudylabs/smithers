# wire_compat — Python ↔ TS cross-runtime parity contract

The artifacts in this directory define **what it means for the Python
port to be wire-compatible with TS Smithers**. The contract is a
normalized JSON snapshot of output rows produced by running a canonical
workflow.

## The contract

```text
examples/wire_compat/
├── workflow.py            # Python workflow exercising every primitive
├── snapshot.json          # Committed normalized row set
├── snapshot_helpers.py    # normalize_rows() + diff_rows()
├── generate_snapshot.py   # Regenerate snapshot.json
└── test_wire_compat.py    # Regression test
```

The workflow exercises:

- `WorkflowNode`, `SequenceNode`, `ParallelNode`, `BranchNode`,
  `LoopNode`, `TaskNode` (both render and DryAgent paths),
  `SubflowNode` (with a child run), `ApprovalGateNode` (when=False
  auto-pass branch).

It does *not* exercise:

- `HumanTaskNode` — always pauses; not snapshot-friendly.
- `WorktreeNode` / `MergeQueueNode` — structural pass-throughs with
  no row contribution.

`snapshot.json` is the committed contract: 12 rows, each a
`(node_id, schema_version, output_name, iteration, payload)` tuple,
sorted by `(node_id, iteration)`. Run-specific identifiers (`run_id`,
timestamps) are stripped during normalization.

## How to use it

### Regression: confirm Python still matches the contract

```bash
cd /Users/luis/smithers/smithers_py
uv run python -m pytest /Users/luis/smithers/examples/wire_compat/test_wire_compat.py -q
```

### Regenerate after an intentional change

```bash
cd /Users/luis/smithers/smithers_py
uv run python /Users/luis/smithers/examples/wire_compat/generate_snapshot.py
git diff examples/wire_compat/snapshot.json
```

### Cross-runtime parity (when a TS twin lands)

The future `workflow.tsx` twin would:

1. Use upstream `smithers-orchestrator` to author the same graph shape
   against the same Pydantic-equivalent Zod schemas.
2. Run via `smithers up workflow.tsx` against a fresh `smithers.db`.
3. Dump the per-schema output tables, normalize to the same `(node_id,
   schema_version, output_name, iteration, payload)` shape.
4. Apply the same `diff_rows` against `snapshot.json`.

The two runtimes are wire-compatible iff that diff is empty.

## What divergences are expected

A few row-level differences are *acceptable* and don't break wire-compat:

- **Subflow row count.** Upstream's `<Subflow>` writes child rows under
  the child's own run id; our Python `SubflowNode` does the same. At
  the *parent* run's row table, only the terminal subflow output is
  visible — and that's what the snapshot captures. The child's
  intermediate rows are isolated by run id either way.

- **`smithers-py-approval-v0` schema version.** This is a synthetic
  schema we use for the row written when an ApprovalGate resolves
  (whether auto-pass, approved, or denied). Upstream uses its own
  approval row format, but the *workflow-level* observable
  (`gate.payload.approved`) matches. The snapshot pins the Python
  side; the cross-runtime diff treats this row as an expected
  divergence — to be unified before publish.

## What divergences are bugs

Everything else. Specifically:

- Different `node_id` shape (path delimiter, suffix conventions).
- Different `output_name` (the registered key from `createSmithers`).
- Different `payload` content for a task that should be deterministic.
- Different iteration count for a `LoopNode`.
- A row missing on one side but present on the other.

The `diff_rows` helper emits these as one-line diagnostics so the
acceptance check tells you which row diverged and how.
