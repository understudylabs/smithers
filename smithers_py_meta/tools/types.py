"""Core types for sandboxed tool execution.

Defines Tool Protocol, ToolContext, constants, and type aliases shared
across the tools subsystem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Protocol

# Constants
DEFAULT_MAX_OUTPUT_BYTES = 200_000
DEFAULT_TOOL_TIMEOUT_MS = 60_000
DEFAULT_FILE_SIZE_LIMIT_BYTES = 10_000_000


@dataclass
class ToolContext:
    """Execution context for sandboxed tools.

    All file operations are scoped to root_dir. Network access and resource
    limits are configurable per-invocation.
    """

    root_dir: str
    allow_network: bool = False
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    tool_timeout_ms: int = DEFAULT_TOOL_TIMEOUT_MS
    idempotency_key: Optional[str] = None
    run_id: Optional[str] = None
    node_id: Optional[str] = None
    iteration: int = 0
    attempt: int = 0


class Tool(Protocol):
    """Protocol for executable tools.

    Minimal contract: name, description, and an execute callable.
    """

    name: str
    description: str
    side_effect: bool
    idempotent: bool

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> Any:
        """Execute the tool with provided arguments and context.

        May raise ToolError or ToolSecurityError on failure.
        """
        ...


ToolExecuteFn = Callable[[dict[str, Any], ToolContext], Awaitable[Any]] | Callable[[dict[str, Any]], Awaitable[Any]]
"""Execute function signature - may accept 1 (args) or 2 (args, ctx) parameters."""


@dataclass
class ToolCallRecord:
    """Persisted log entry for a single tool invocation."""

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
    status: str = "success"
    error_json: Optional[str] = None
