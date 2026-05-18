"""Smithers tools sandbox.

Five built-in tools plus a ``define_tool`` factory for customs. All
tools run inside a sandbox rooted at ``ToolContext.root_dir`` with
optional network access and configurable timeout / output caps.

```python
from smithers_py.tools import (
    ToolContext,
    bash,
    define_tool,
    invoke_tool,
    read,
    tools,
    write,
)

ctx = ToolContext(root_dir="/tmp/sandbox")
result = await invoke_tool(read, {"path": "README.md"}, ctx)
```

For custom side-effecting tools, pass ``side_effect=True``:

```python
import os

async def send_email(args, ctx):
    return await mailer.send(
        to=args["to"],
        body=args["body"],
        idempotency_key=ctx.idempotency_key,
    )

email = define_tool(
    name="email.send",
    description="Send an email",
    execute=send_email,
    side_effect=True,
    idempotent=False,
)
```

Match upstream's tool-call logging contract via ``ToolCallLog`` if you
want each invocation persisted to ``ts_tool_calls``.
"""

from __future__ import annotations

from .builtins import (
    ToolError,
    bash,
    edit,
    grep,
    read,
    tools,
    write,
)
from .define import (
    ToolCallLog,
    define_tool,
    invoke_tool,
)
from .sandbox import (
    ToolSecurityError,
    check_network_policy,
    resolve_sandboxed_path,
)
from .types import (
    DEFAULT_FILE_SIZE_LIMIT_BYTES,
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_TOOL_TIMEOUT_MS,
    Tool,
    ToolCallRecord,
    ToolContext,
    ToolExecuteFn,
)

__all__ = [
    # built-ins
    "bash",
    "edit",
    "grep",
    "read",
    "tools",
    "write",
    # define_tool + logging
    "define_tool",
    "invoke_tool",
    "ToolCallLog",
    # sandbox helpers
    "check_network_policy",
    "resolve_sandboxed_path",
    "ToolSecurityError",
    # types + constants
    "DEFAULT_FILE_SIZE_LIMIT_BYTES",
    "DEFAULT_MAX_OUTPUT_BYTES",
    "DEFAULT_TOOL_TIMEOUT_MS",
    "Tool",
    "ToolCallRecord",
    "ToolContext",
    "ToolError",
    "ToolExecuteFn",
]
