# `smithers_py.tools` — sandboxed tools (read/write/edit/grep/bash + define_tool)

Five built-in tools plus a `define_tool` factory. All run inside a
sandbox rooted at `ToolContext.root_dir` with optional network access
and configurable timeout / output caps. Mirrors upstream Smithers'
tool surface (/llms-integrations.txt#built-in-tools).

## Public surface

```python
from smithers_py.tools import (
    ToolContext,
    ToolError,
    ToolSecurityError,
    bash, edit, grep, read, write,
    tools,                    # bundle dict {name: tool}
    define_tool,              # factory for customs
    invoke_tool,              # runtime entry with logging
    ToolCallLog,              # persisted log
    resolve_sandboxed_path,
    check_network_policy,
)

ctx = ToolContext(root_dir="/tmp/sandbox", allow_network=False)
result = await invoke_tool(read, {"path": "README.md"}, ctx)
```

## Sandboxing rules (non-negotiable)

- Every filesystem op resolves via `resolve_sandboxed_path`. Rejects:
  - Empty paths
  - Absolute paths outside root
  - Relative paths that escape via `../`
  - Symlinks whose target (or any ancestor) escapes the root
- `check_network_policy` rejects bash commands containing any of:
  `curl`, `wget`, `http://`, `https://`, `npm`, `bun`, `pip`,
  `git push`, `git pull`, `git fetch`, `git clone`, `git remote`.
  Bypassed when `allow_network=True`.
- Per-tool output cap via `ctx.max_output_bytes` (default 200,000).
  Hard file size limit `DEFAULT_FILE_SIZE_LIMIT_BYTES = 10_000_000`.
- Per-tool timeout via `ctx.tool_timeout_ms` (default 60,000).

## Built-in tools

### `read({path})`
UTF-8 file read. Truncates to `max_output_bytes` with `[truncated]`
marker. Rejects files larger than `DEFAULT_FILE_SIZE_LIMIT_BYTES`.

### `write({path, content})`
Writes content, creates parent dirs. Refuses content above the size
limit. Returns `"ok"`.

### `edit({path, patch})`
Applies a unified diff via pure-Python parser (no external `patch`
binary). Parses `@@ -L,N +L,N @@` hunks, applies in order, rejects on
context mismatch with `ToolError("hunks did not match")`.

### `grep({pattern, path?})`
Shells out to `rg` (ripgrep) for performance — fails with `ToolError`
if `rg` is not on PATH. Returns `<path>:<line>:<content>` lines.
Empty string when no matches (rg exit 1). Truncates at output cap.

### `bash({cmd, args?, opts?})`
Subprocess execution with `start_new_session=True`, kills entire
process group on timeout (`os.killpg(pid, 9)`). Network policy
checked against the joined `cmd + args` string. Returns combined
stdout + stderr. Raises `ToolError` on non-zero exit (with exit code
+ truncated output in the message).

## `define_tool` factory

```python
def define_tool(
    *,
    name: str,
    description: str,
    execute: ToolExecuteFn,    # Callable[[dict, ToolContext], Awaitable[Any]]
    side_effect: bool = False,
    idempotent: bool = True,
) -> Tool: ...
```

Returns a `_DefinedTool` satisfying the `Tool` Protocol. Detects
whether `execute` takes 1 or 2 args via `inspect.signature`.

Warning behavior: if `side_effect=True, idempotent=False` and the
provided `execute` function doesn't accept a `ctx` parameter, emit
`UserWarning` at construction time — the runtime needs
`ctx.idempotency_key` to dedupe retries safely; building without it
is almost always a bug.

## `ToolContext`

```python
@dataclass
class ToolContext:
    root_dir: str
    allow_network: bool = False
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    tool_timeout_ms: int = DEFAULT_TOOL_TIMEOUT_MS
    idempotency_key: Optional[str] = None
    run_id: Optional[str] = None
    node_id: Optional[str] = None
    iteration: int = 0
    attempt: int = 0
```

## `ToolCallLog` — persisted log

Writes to `ts_tool_calls` table:

```sql
CREATE TABLE ts_tool_calls (
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    iteration INTEGER NOT NULL DEFAULT 0,
    attempt INTEGER NOT NULL DEFAULT 0,
    seq INTEGER NOT NULL,
    tool_name TEXT NOT NULL,
    input_json TEXT NOT NULL,
    output_json TEXT,
    started_at_ms INTEGER NOT NULL,
    finished_at_ms INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'success',
    error_json TEXT,
    PRIMARY KEY (run_id, node_id, iteration, attempt, seq)
);
```

Methods:

- `record(row: ToolCallRecord)` — writes one row.
- `list_for_run(run_id, *, node_id=None, tool_name=None)` — query
  helper.

## `invoke_tool` — runtime entry point

```python
async def invoke_tool(
    tool: Tool,
    args: dict[str, Any],
    ctx: ToolContext,
    *,
    log: Optional[ToolCallLog] = None,
    seq: int = 0,
) -> Any: ...
```

Wraps the call with logging. Records both success and error rows.
Re-raises on error so the caller (agent's tool loop) sees the
exception.

## Side-effect rules (documentation contract)

A side effect is any mutation of state **outside the sandbox**:
external API call, database write, message send, webhook. File-system
changes inside the sandbox are NOT side effects (they're reversible
with `git`). The built-in `write`, `edit`, `bash` tools therefore
have `side_effect=False`.

## Files to produce

- `__init__.py` — public exports
- `types.py` — `Tool` Protocol, `ToolContext` dataclass,
  `ToolCallRecord` dataclass, `ToolExecuteFn` alias, constants
  (`DEFAULT_MAX_OUTPUT_BYTES = 200_000`,
  `DEFAULT_TOOL_TIMEOUT_MS = 60_000`,
  `DEFAULT_FILE_SIZE_LIMIT_BYTES = 10_000_000`)
- `sandbox.py` — `ToolSecurityError`, `resolve_sandboxed_path`,
  `check_network_policy`, `_BLOCKED_NETWORK_FRAGMENTS` constant
- `builtins.py` — 5 built-ins via `define_tool`, plus the
  `tools = {"read": read, ...}` bundle. Pure-Python unified-diff
  applier in this file (`_apply_unified_diff`).
- `define.py` — `define_tool` factory, `_DefinedTool` class,
  `ToolCallLog`, `invoke_tool`, the `ts_tool_calls` schema
- `test_tools.py` — pytest-asyncio + tempfile fixtures. Cover:
  path containment (relative / absolute / dot-dot / symlink escape /
  empty), network policy (curl/wget/https/git push blocked; local
  git allowed; allow_network=True bypasses), each built-in's happy
  path + edge cases (truncation, missing files, bad patches,
  timeouts, non-zero exits, network blocks), define_tool factory
  (warning detection, ctx parameter handling), ToolCallLog
  persistence (success rows, error rows, filter by tool name),
  bundle membership.
