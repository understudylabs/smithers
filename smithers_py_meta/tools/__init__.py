"""Smithers sandboxed tools (read/write/edit/grep/bash + define_tool).

Five built-in tools plus a define_tool factory. All run inside a sandbox
rooted at ToolContext.root_dir with optional network access and configurable
timeout / output caps.

```python
from smithers_py_meta.tools import (
    ToolContext,
    ToolError,
    ToolSecurityError,
    bash, edit, grep, read, write,
    tools,
    define_tool,
    invoke_tool,
    ToolCallLog,
)

ctx = ToolContext(root_dir="/tmp/sandbox", allow_network=False)
result = await invoke_tool(read, {"path": "README.md"}, ctx)
```
"""

from __future__ import annotations

# Core types and exceptions
from .types import (
    DEFAULT_FILE_SIZE_LIMIT_BYTES,
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_TOOL_TIMEOUT_MS,
    Tool,
    ToolCallRecord,
    ToolContext,
    ToolExecuteFn,
)

# Security primitives
from .sandbox import (
    ToolSecurityError,
    check_network_policy,
    resolve_sandboxed_path,
)

# Tool operations and logging
from .builtins import ToolError, _create_builtins
from .define import ToolCallLog, define_tool, invoke_tool

# Initialize built-in tools bundle
_builtins_dict = _create_builtins()
tools = _builtins_dict

# Individual tool exports
read = _builtins_dict["read"]
write = _builtins_dict["write"]
edit = _builtins_dict["edit"]
grep = _builtins_dict["grep"]
bash = _builtins_dict["bash"]

# Also update builtins module's tools dict for consistency
from . import builtins as _builtins_module

_builtins_module.tools = _builtins_dict

__all__ = [
    # Types
    "Tool",
    "ToolContext",
    "ToolCallRecord",
    "ToolExecuteFn",
    # Exceptions
    "ToolError",
    "ToolSecurityError",
    # Built-in tools
    "read",
    "write",
    "edit",
    "grep",
    "bash",
    "tools",
    # Factory and runtime
    "define_tool",
    "invoke_tool",
    "ToolCallLog",
    # Security primitives
    "resolve_sandboxed_path",
    "check_network_policy",
    # Constants
    "DEFAULT_MAX_OUTPUT_BYTES",
    "DEFAULT_TOOL_TIMEOUT_MS",
    "DEFAULT_FILE_SIZE_LIMIT_BYTES",
]
