"""Built-in tools: read / write / edit / grep / bash.

Mirrors the upstream Smithers tool surface (/llms-integrations.txt
``Built-in Tools``). Each tool is a small ``define_tool`` invocation
plus a private ``_*_impl`` async function that does the work.

Sandboxing rules:

- Every filesystem op resolves through ``resolve_sandboxed_path`` and
  is rejected if it escapes the root.
- ``read`` truncates at ``ctx.max_output_bytes`` and refuses files
  larger than ``DEFAULT_FILE_SIZE_LIMIT_BYTES``.
- ``write`` refuses content over the size limit; creates parent
  directories.
- ``edit`` applies a unified diff to an existing file via stdlib
  ``difflib`` (no external patch dep). Refuses missing files.
- ``grep`` shells out to ``rg`` (ripgrep) for performance. Falls back
  to ``ToolError`` if rg isn't on PATH.
- ``bash`` runs the command with subprocess timeout. Blocks network
  commands per ``check_network_policy``.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
from pathlib import Path
from typing import Any

from .define import define_tool
from .sandbox import (
    ToolSecurityError,
    check_network_policy,
    resolve_sandboxed_path,
)
from .types import (
    DEFAULT_FILE_SIZE_LIMIT_BYTES,
    Tool,
    ToolContext,
)


class ToolError(RuntimeError):
    """A tool execution failed in an expected way (e.g., file not found,
    rg not installed, command exited non-zero). Distinct from
    ``ToolSecurityError`` which signals a policy violation."""


# ----- read ----------------------------------------------------------------


async def _read_impl(args: dict[str, Any], ctx: ToolContext) -> str:
    path = args["path"]
    resolved = resolve_sandboxed_path(ctx.root_dir, path)

    p = Path(resolved)
    if not p.exists():
        raise ToolError(f"file not found: {path}")
    if not p.is_file():
        raise ToolError(f"not a regular file: {path}")

    size = p.stat().st_size
    if size > DEFAULT_FILE_SIZE_LIMIT_BYTES:
        raise ToolError(
            f"file too large: {size} bytes (limit "
            f"{DEFAULT_FILE_SIZE_LIMIT_BYTES} bytes)"
        )

    contents = p.read_text(encoding="utf-8", errors="replace")
    if len(contents) > ctx.max_output_bytes:
        return contents[: ctx.max_output_bytes] + "\n... [truncated]"
    return contents


read: Tool = define_tool(
    name="read",
    description="Read a file from the sandbox. Returns UTF-8 contents (truncated to max_output_bytes).",
    execute=_read_impl,
    side_effect=False,
    idempotent=True,
)


# ----- write ---------------------------------------------------------------


async def _write_impl(args: dict[str, Any], ctx: ToolContext) -> str:
    path = args["path"]
    content = args["content"]
    if not isinstance(content, str):
        raise ToolError(f"content must be a string, got {type(content).__name__}")
    if len(content.encode("utf-8")) > DEFAULT_FILE_SIZE_LIMIT_BYTES:
        raise ToolError(
            f"content too large: {len(content)} chars (limit "
            f"{DEFAULT_FILE_SIZE_LIMIT_BYTES} bytes)"
        )

    resolved = resolve_sandboxed_path(ctx.root_dir, path)
    p = Path(resolved)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return "ok"


write: Tool = define_tool(
    name="write",
    description="Write content to a file in the sandbox. Creates parent directories.",
    execute=_write_impl,
    side_effect=False,  # sandboxed FS — git-revertable, not a real side effect
    idempotent=True,
)


# ----- edit ----------------------------------------------------------------


async def _edit_impl(args: dict[str, Any], ctx: ToolContext) -> str:
    path = args["path"]
    patch = args["patch"]
    if not isinstance(patch, str):
        raise ToolError(f"patch must be a string, got {type(patch).__name__}")

    resolved = resolve_sandboxed_path(ctx.root_dir, path)
    p = Path(resolved)
    if not p.exists():
        raise ToolError(f"file not found: {path}")
    if not p.is_file():
        raise ToolError(f"not a regular file: {path}")

    original = p.read_text(encoding="utf-8", errors="replace")
    patched = _apply_unified_diff(original, patch)
    if patched is None:
        raise ToolError(
            f"failed to apply patch to {path}: hunks did not match file content"
        )
    p.write_text(patched, encoding="utf-8")
    return "ok"


def _apply_unified_diff(original: str, patch: str) -> str | None:
    """Apply a unified diff to ``original``. Returns the patched text or
    ``None`` if any hunk fails to match.

    Pure-Python implementation — no external ``patch`` binary needed.
    Recognizes standard unified-diff format with ``---`` / ``+++`` /
    ``@@ -L,N +L,N @@`` hunk headers.
    """
    lines = original.splitlines(keepends=True)
    patch_lines = patch.splitlines()
    # Skip until first hunk header.
    i = 0
    while i < len(patch_lines) and not patch_lines[i].startswith("@@"):
        i += 1
    if i == len(patch_lines):
        # No hunks — patch is a no-op.
        return original

    result: list[str] = []
    cursor = 0  # current line in the original (0-indexed)

    while i < len(patch_lines):
        line = patch_lines[i]
        if not line.startswith("@@"):
            return None  # malformed
        # Parse header: @@ -orig_start,orig_count +new_start,new_count @@
        try:
            header_body = line.split("@@")[1].strip()
            parts = header_body.split()
            orig_part = parts[0]  # e.g. "-3,4"
            orig_start_str = orig_part.lstrip("-").split(",")[0]
            orig_start = int(orig_start_str)
        except (IndexError, ValueError):
            return None

        # Copy unchanged lines from cursor up to (orig_start - 1).
        target = max(0, orig_start - 1)
        if cursor > target:
            return None  # hunks out of order
        result.extend(lines[cursor:target])
        cursor = target

        # Apply hunk body until next "@@" or EOF.
        i += 1
        while i < len(patch_lines) and not patch_lines[i].startswith("@@"):
            body_line = patch_lines[i]
            if body_line.startswith("\\"):
                # "\ No newline at end of file" — ignore
                i += 1
                continue
            if body_line.startswith(" "):
                # Context line — must match.
                expected = body_line[1:]
                if cursor >= len(lines) or lines[cursor].rstrip("\n") != expected:
                    return None
                result.append(lines[cursor])
                cursor += 1
            elif body_line.startswith("-"):
                # Deletion — must match.
                expected = body_line[1:]
                if cursor >= len(lines) or lines[cursor].rstrip("\n") != expected:
                    return None
                cursor += 1
            elif body_line.startswith("+"):
                # Addition.
                added = body_line[1:]
                result.append(added + "\n")
            else:
                # Empty line in the patch body. Treat as context for
                # tolerance with patches that omit the leading space.
                if cursor < len(lines) and lines[cursor].rstrip("\n") == "":
                    result.append(lines[cursor])
                    cursor += 1
                else:
                    return None
            i += 1

    # Copy any trailing unchanged lines.
    result.extend(lines[cursor:])
    return "".join(result)


edit: Tool = define_tool(
    name="edit",
    description="Apply a unified-diff patch to a file in the sandbox.",
    execute=_edit_impl,
    side_effect=False,
    idempotent=True,
)


# ----- grep ----------------------------------------------------------------


async def _grep_impl(args: dict[str, Any], ctx: ToolContext) -> str:
    pattern = args["pattern"]
    path = args.get("path") or ctx.root_dir
    resolved = resolve_sandboxed_path(ctx.root_dir, path)

    if shutil.which("rg") is None:
        raise ToolError(
            "ripgrep (rg) not on PATH; required for the grep tool"
        )

    timeout_s = max(1.0, ctx.tool_timeout_ms / 1000.0)
    proc = await asyncio.create_subprocess_exec(
        "rg",
        "-n",
        "--no-heading",
        pattern,
        resolved,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_s
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise ToolError(f"grep timed out after {timeout_s:.0f}s")

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")

    # rg exits 0 with matches, 1 without matches, >=2 on error.
    if proc.returncode == 1:
        return ""
    if proc.returncode != 0:
        raise ToolError(f"rg failed (exit {proc.returncode}): {stderr.strip()}")

    if len(stdout) > ctx.max_output_bytes:
        return stdout[: ctx.max_output_bytes] + "\n... [truncated]"
    return stdout


grep: Tool = define_tool(
    name="grep",
    description="Search for a regex pattern via ripgrep. Returns matching lines (path:line:content).",
    execute=_grep_impl,
    side_effect=False,
    idempotent=True,
)


# ----- bash ----------------------------------------------------------------


async def _bash_impl(args: dict[str, Any], ctx: ToolContext) -> str:
    cmd = args["cmd"]
    extra_args = args.get("args") or []
    opts = args.get("opts") or {}
    cwd = opts.get("cwd")

    if not isinstance(extra_args, list):
        raise ToolError("args must be a list of strings")

    # Join the command + args for the network policy check (substring
    # match against the full intended invocation).
    full = " ".join([cmd, *extra_args])
    check_network_policy(full, allow_network=ctx.allow_network)

    # Resolve cwd inside the sandbox (default to root_dir).
    if cwd:
        cwd_resolved = resolve_sandboxed_path(ctx.root_dir, cwd)
    else:
        cwd_resolved = ctx.root_dir

    timeout_s = max(1.0, ctx.tool_timeout_ms / 1000.0)
    proc = await asyncio.create_subprocess_exec(
        cmd,
        *extra_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd_resolved,
        start_new_session=True,
    )

    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_s
        )
    except asyncio.TimeoutError:
        # Kill the entire process group (start_new_session=True makes
        # the child the leader of its own group). SIGKILL because
        # SIGTERM may be ignored.
        try:
            os.killpg(proc.pid, 9)
        except (OSError, ProcessLookupError):
            pass
        await proc.wait()
        raise ToolError(f"bash timed out after {timeout_s:.0f}s")

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    combined = stdout + (stderr if stderr else "")

    if proc.returncode != 0:
        raise ToolError(
            f"bash command failed (exit {proc.returncode}): "
            f"{combined.strip()[: ctx.max_output_bytes]}"
        )

    if len(combined) > ctx.max_output_bytes:
        return combined[: ctx.max_output_bytes] + "\n... [truncated]"
    return combined


bash: Tool = define_tool(
    name="bash",
    description="Execute a shell command in the sandbox. Network access blocked by default.",
    execute=_bash_impl,
    side_effect=False,  # local sandboxed exec — same rationale as write/edit
    idempotent=True,
)


# ----- bundle --------------------------------------------------------------


tools: dict[str, Tool] = {
    "read": read,
    "write": write,
    "edit": edit,
    "grep": grep,
    "bash": bash,
}
"""All five built-ins keyed by name. Useful when passing to an agent
that accepts the whole bundle (e.g., a least-privilege filter
``{name: tools[name] for name in agent_allowed_names}``)."""


__all__ = [
    "ToolError",
    "bash",
    "edit",
    "grep",
    "read",
    "tools",
    "write",
]
