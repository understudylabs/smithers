# wire_compat — Python ↔ TS cross-runtime parity contract

The artifacts in this directory define **what it means for the Python
port to be wire-compatible with TS Smithers**. The contract is a pair
of normalized JSON snapshots produced by running the same workflow
through both runtimes; an empty diff between them is the parity
assertion.

**Status (2026-05-18):** ✅ **Parity achieved.** The Python `snapshot.json`
and TS `ts_snapshot.json` are row-for-row identical for the canonical
workflow. The `test_cross_runtime_row_set_diff` test asserts an empty
diff and currently passes.

## The contract

```text
examples/wire_compat/
├── workflow.py             # Python workflow exercising every primitive
├── workflow.tsx            # TS twin (runs in upstream smithers-orchestrator)
├── child-workflow.tsx      # TS Subflow child
├── schemas.ts              # Zod twins of the Pydantic schemas
├── package.json            # TS deps (bun install)
├── snapshot.json           # Committed Python normalized row set
├── ts_snapshot.json        # Committed TS normalized row set (manually regen)
├── snapshot_helpers.py     # normalize_rows() + diff_rows()
├── generate_snapshot.py    # Regenerate Python snapshot.json
├── extract_ts_snapshot.py  # Regenerate TS ts_snapshot.json from smithers.db
├── test_wire_compat.py     # Python single-runtime regression
└── test_cross_runtime.py   # Python ↔ TS cross-runtime parity
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

### Cross-runtime parity (live)

```bash
# 1. Run the TS twin via upstream smithers-orchestrator. Produces
#    wire_compat.db with the per-schema output tables.
cd /Users/luis/smithers/examples/wire_compat
bun install
rm -f wire_compat.db wire_compat.db-* 2>/dev/null
./node_modules/.bin/smithers up workflow.tsx --run-id wire-compat-ts \
  --input '{"workload":"snapshot","branch":true,"iterations":3}'

# 2. Extract the TS rows into ts_snapshot.json, filtered to the
#    parent run (subflow child rows live in their own run id).
python3 extract_ts_snapshot.py

# 3. Regenerate the Python snapshot.
cd /Users/luis/smithers/smithers_py
uv run python /Users/luis/smithers/examples/wire_compat/generate_snapshot.py

# 4. Cross-runtime diff.
uv run python -m pytest /Users/luis/smithers/examples/wire_compat/test_cross_runtime.py -v
```

The two runtimes are wire-compatible iff that diff is empty. Today
it is.

## How parity was achieved (the journey)

Initial Python and TS runs diverged in five ways. Each got fixed:

| Divergence | Fix |
| --- | --- |
| Python emitted `node_id="main/seq-1"` etc.; TS emitted bare `node_id="seq-1"` | Dropped the `main/` prefix and the path-stack walking from the Python runner; `node.id` is now used as-is, matching TS's flat-id-within-a-run scheme. |
| Python suffixed loop iterations into the `node_id` (`loop:loop/iter:0/loop-step`); TS reused the bare `node_id` with the `iteration` column incrementing | Threaded `iteration: int` through `_walk` so `LoopNode` writes N rows with the same `node_id` and `iteration=0..N-1`. |
| Python wrapped Branch children with `branch:br/then/…`; TS was transparent | Branch now walks the chosen child directly with no path wrapper. |
| Approval row had a synthetic `smithers-py-approval-v0` schema_version; TS used the bound output schema | Approval row now writes `{approved: bool, …}` with no synthetic schema_version, matching TS. |
| TS extract initially over-included rows from the subflow child run | `extract_ts_snapshot.py` now filters by parent `run_id`, matching what the Python snapshot generator does on its side. |

After these fixes the row sets are row-for-row identical.

## What divergences will be bugs (going forward)

Now that parity is the baseline, any future divergence is a bug. The
`diff_rows` helper emits one-line diagnostics per divergence so the
acceptance check tells you which row diverged and how.
