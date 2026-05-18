"""Shared types for the smithers_py tools subsystem.

Mirrors the upstream Smithers tool surface (read / write / edit / grep /
bash + ``defineTool``). Tools execute inside a sandbox rooted at a
specific filesystem path with optional network access and configurable
output / timeout caps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Protocol, runtime_checkable


# Default policy values mirroring upstream defaults.
DEFAULT_MAX_OUTPUT_BYTES = 200_000  # 200 KB
DEFAULT_TOOL_TIMEOUT_MS = 60_000  # 60 s
DEFAULT_FILE_SIZE_LIMIT_BYTES = 10_000_000  # 10 MB hard cap on read/write/edit


@dataclass
class ToolContext:
    """Per-call runtime context passed into every tool's ``execute``.

    The sandbox root, network policy, and resource caps are read from
    this context — never from globals — so a single Python process can
    safely host multiple workflow runs with different sandboxes.

    ``idempotency_key`` is stable across retries of the same task
    iteration, so side-effecting tools can safely pass it to external
    APIs that support idempotency (e.g., Stripe, AWS).
    """

    root_dir: str
    """Sandbox root. All filesystem operations resolve relative to this
    and are rejected if they escape it (including through symlinks)."""

    allow_network: bool = False
    """When False, bash blocks network commands (curl, wget, http URLs,
    package managers, git remote ops). Defaults to safe."""

    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    """Per-tool output cap. Truncated reads / capped command output."""

    tool_timeout_ms: int = DEFAULT_TOOL_TIMEOUT_MS
    """Wall-clock timeout for long-running tools (bash, grep)."""

    idempotency_key: Optional[str] = None
    """Stable across retries; ``None`` when the tool isn't running
    inside a task attempt."""

    run_id: Optional[str] = None
    node_id: Optional[str] = None
    iteration: int = 0
    attempt: int = 0
    """Run / node / iteration / attempt the tool call belongs to. Used
    for the persisted tool-call log."""


@runtime_checkable
class Tool(Protocol):
    """Interface every Smithers tool implements.

    Both built-in tools and ``define_tool``-built customs satisfy this
    protocol so they can be used interchangeably.
    """

    name: str
    description: str
    side_effect: bool
    idempotent: bool

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> Any:
        """Run the tool. ``args`` is the validated input; return value
        becomes the tool's output."""


ToolExecuteFn = Callable[[dict[str, Any], ToolContext], Awaitable[Any]]
"""Signature for the user-supplied function in ``define_tool``."""


@dataclass
class ToolCallRecord:
    """One row in the persisted tool-call log (``ts_tool_calls`` table).

    Mirrors the upstream ``_smithers_tool_calls`` columns. Stored on
    every tool invocation regardless of success or failure.
    """

    run_id: str
    node_id: str
    iteration: int
    attempt: int
    seq: int
    tool_name: str
    input_json: str
    output_json: Optional[str] = None
    started_at_ms: int = 0
    finished_at_ms: int = 0
    status: str = "success"  # "success" | "error"
    error_json: Optional[str] = None
