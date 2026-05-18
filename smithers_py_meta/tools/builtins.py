"""Built-in sandboxed tools: read, write, edit, grep, bash.

All operations run within ToolContext.root_dir with configurable resource
limits and network policy enforcement.
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
from pathlib import Path
from typing import Any

from .sandbox import ToolSecurityError, check_network_policy, resolve_sandboxed_path
from .types import DEFAULT_FILE_SIZE_LIMIT_BYTES, Tool, ToolContext


class ToolError(Exception):
    """Non-security operational error during tool execution."""

    pass


# ============================================================================
# Pure-Python unified diff applier
# ============================================================================


def _apply_unified_diff(original: str, patch: str) -> str:
    """Apply unified diff patch to original content.

    Args:
        original: Original file content
        patch: Unified diff format patch

    Returns:
        Patched content

    Raises:
        ToolError: If hunks don't match or patch is malformed
    """
    lines = original.splitlines(keepends=True)
    result = []
    line_idx = 0

    # Parse hunks
    hunk_pattern = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

    patch_lines = patch.splitlines(keepends=True)
    i = 0
    while i < len(patch_lines):
        line = patch_lines[i]

        # Skip non-hunk headers
        if not line.startswith("@@"):
            i += 1
            continue

        match = hunk_pattern.match(line)
        if not match:
            raise ToolError(f"Malformed hunk header: {line.rstrip()}")

        old_start = int(match.group(1))
        old_count = int(match.group(2)) if match.group(2) else 1
        new_start = int(match.group(3))
        new_count = int(match.group(4)) if match.group(4) else 1

        # Copy lines before hunk
        while line_idx < old_start - 1:
            if line_idx < len(lines):
                result.append(lines[line_idx])
            line_idx += 1

        # Process hunk
        i += 1
        hunk_old_idx = 0
        while i < len(patch_lines) and hunk_old_idx < old_count:
            hunk_line = patch_lines[i]

            if hunk_line.startswith(" "):
                # Context line - must match
                expected_idx = line_idx
                if expected_idx >= len(lines):
                    raise ToolError(
                        f"Hunk context mismatch at line {expected_idx + 1}: "
                        f"expected '{hunk_line[1:].rstrip()}' but file ended"
                    )
                if lines[expected_idx] != hunk_line[1:]:
                    raise ToolError(
                        f"Hunk context mismatch at line {expected_idx + 1}: "
                        f"expected '{hunk_line[1:].rstrip()}' but got '{lines[expected_idx].rstrip()}'"
                    )
                result.append(lines[expected_idx])
                line_idx += 1
                hunk_old_idx += 1
                i += 1

            elif hunk_line.startswith("-"):
                # Deletion - must match
                expected_idx = line_idx
                if expected_idx >= len(lines):
                    raise ToolError(
                        f"Hunk deletion mismatch at line {expected_idx + 1}: "
                        f"expected '{hunk_line[1:].rstrip()}' but file ended"
                    )
                if lines[expected_idx] != hunk_line[1:]:
                    raise ToolError(
                        f"Hunk deletion mismatch at line {expected_idx + 1}: "
                        f"expected '{hunk_line[1:].rstrip()}' but got '{lines[expected_idx].rstrip()}'"
                    )
                line_idx += 1
                hunk_old_idx += 1
                i += 1

            elif hunk_line.startswith("+"):
                # Addition
                result.append(hunk_line[1:])
                i += 1

            else:
                # End of hunk or unknown marker
                break

        # Handle remaining additions (when old_count < new_count)
        while i < len(patch_lines) and patch_lines[i].startswith("+"):
            result.append(patch_lines[i][1:])
            i += 1

    # Copy remaining lines after last hunk
    while line_idx < len(lines):
        result.append(lines[line_idx])
        line_idx += 1

    return "".join(result)


# ============================================================================
# Built-in tools
# ============================================================================


async def _read_impl(args: dict[str, Any], ctx: ToolContext) -> str:
    """Read UTF-8 file, truncating to max_output_bytes."""
    path_arg = args.get("path")
    if not path_arg:
        raise ToolError("Missing required argument: path")

    abs_path = resolve_sandboxed_path(ctx.root_dir, path_arg)

    if not os.path.exists(abs_path):
        raise ToolError(f"File not found: {path_arg}")

    if not os.path.isfile(abs_path):
        raise ToolError(f"Not a file: {path_arg}")

    file_size = os.path.getsize(abs_path)
    if file_size > DEFAULT_FILE_SIZE_LIMIT_BYTES:
        raise ToolError(
            f"File too large: {file_size} bytes "
            f"(limit: {DEFAULT_FILE_SIZE_LIMIT_BYTES})"
        )

    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            content = f.read(ctx.max_output_bytes)
            if file_size > ctx.max_output_bytes:
                content += "\n[truncated]"
            return content
    except UnicodeDecodeError as e:
        raise ToolError(f"UTF-8 decode error: {e}")


async def _write_impl(args: dict[str, Any], ctx: ToolContext) -> str:
    """Write content to file, creating parent directories."""
    path_arg = args.get("path")
    content = args.get("content", "")

    if not path_arg:
        raise ToolError("Missing required argument: path")

    if len(content) > DEFAULT_FILE_SIZE_LIMIT_BYTES:
        raise ToolError(
            f"Content too large: {len(content)} bytes "
            f"(limit: {DEFAULT_FILE_SIZE_LIMIT_BYTES})"
        )

    abs_path = resolve_sandboxed_path(ctx.root_dir, path_arg)

    # Create parent directories
    parent = Path(abs_path).parent
    parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
        return "ok"
    except Exception as e:
        raise ToolError(f"Write failed: {e}")


async def _edit_impl(args: dict[str, Any], ctx: ToolContext) -> str:
    """Apply unified diff patch to existing file."""
    path_arg = args.get("path")
    patch = args.get("patch")

    if not path_arg:
        raise ToolError("Missing required argument: path")
    if not patch:
        raise ToolError("Missing required argument: patch")

    abs_path = resolve_sandboxed_path(ctx.root_dir, path_arg)

    if not os.path.exists(abs_path):
        raise ToolError(f"File not found: {path_arg}")

    file_size = os.path.getsize(abs_path)
    if file_size > DEFAULT_FILE_SIZE_LIMIT_BYTES:
        raise ToolError(
            f"File too large: {file_size} bytes "
            f"(limit: {DEFAULT_FILE_SIZE_LIMIT_BYTES})"
        )

    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            original = f.read()
    except UnicodeDecodeError as e:
        raise ToolError(f"UTF-8 decode error: {e}")

    try:
        patched = _apply_unified_diff(original, patch)
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(f"Patch application failed: {e}")

    if len(patched) > DEFAULT_FILE_SIZE_LIMIT_BYTES:
        raise ToolError(
            f"Patched content too large: {len(patched)} bytes "
            f"(limit: {DEFAULT_FILE_SIZE_LIMIT_BYTES})"
        )

    try:
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(patched)
        return "ok"
    except Exception as e:
        raise ToolError(f"Write failed: {e}")


async def _grep_impl(args: dict[str, Any], ctx: ToolContext) -> str:
    """Search with ripgrep, returning matches or empty string."""
    pattern = args.get("pattern")
    path_arg = args.get("path", ".")

    if not pattern:
        raise ToolError("Missing required argument: pattern")

    abs_path = resolve_sandboxed_path(ctx.root_dir, path_arg)

    # Check if rg is available
    try:
        proc = await asyncio.create_subprocess_exec(
            "which",
            "rg",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if proc.returncode != 0:
            raise ToolError("ripgrep (rg) not found on PATH")
    except Exception as e:
        raise ToolError(f"ripgrep check failed: {e}")

    # Run ripgrep
    try:
        proc = await asyncio.create_subprocess_exec(
            "rg",
            "--",
            pattern,
            abs_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout_bytes, stderr_bytes = await proc.communicate()
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        # rg exits 0 on match, 1 on no match, >1 on error
        if proc.returncode == 0:
            if len(stdout) > ctx.max_output_bytes:
                return stdout[: ctx.max_output_bytes] + "\n[truncated]"
            return stdout
        elif proc.returncode == 1:
            # No matches
            return ""
        else:
            raise ToolError(f"ripgrep error (exit {proc.returncode}): {stderr}")

    except Exception as e:
        if isinstance(e, ToolError):
            raise
        raise ToolError(f"ripgrep execution failed: {e}")


async def _bash_impl(args: dict[str, Any], ctx: ToolContext) -> str:
    """Execute bash command with timeout and process group cleanup."""
    cmd = args.get("cmd")
    cmd_args = args.get("args", [])
    opts = args.get("opts", {})

    if not cmd:
        raise ToolError("Missing required argument: cmd")

    # Build full command string for network policy check
    if isinstance(cmd_args, list):
        full_cmd = " ".join([cmd] + cmd_args)
    else:
        full_cmd = f"{cmd} {cmd_args}" if cmd_args else cmd

    check_network_policy(full_cmd, ctx.allow_network)

    # Parse command into argv
    if isinstance(cmd_args, list):
        argv = [cmd] + cmd_args
    else:
        argv = full_cmd.split()

    timeout_sec = ctx.tool_timeout_ms / 1000.0

    try:
        # Start in new process group for timeout cleanup
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=ctx.root_dir,
            start_new_session=True,
        )

        try:
            stdout_bytes, _ = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_sec
            )
            stdout = stdout_bytes.decode("utf-8", errors="replace")
        except asyncio.TimeoutError:
            # Kill entire process group
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.wait()
            raise ToolError(
                f"Command timed out after {ctx.tool_timeout_ms}ms: {full_cmd}"
            )

        if len(stdout) > ctx.max_output_bytes:
            stdout = stdout[: ctx.max_output_bytes] + "\n[truncated]"

        if proc.returncode != 0:
            raise ToolError(
                f"Command failed (exit {proc.returncode}): {full_cmd}\n{stdout}"
            )

        return stdout

    except ToolError:
        raise
    except Exception as e:
        raise ToolError(f"Command execution failed: {e}")


# ============================================================================
# Tool registration via define_tool (imported from define.py at runtime)
# ============================================================================

# Import define_tool here to avoid circular dependency
# We'll populate tools dict after define_tool is available


def _create_builtins() -> dict[str, Tool]:
    """Construct the 5 built-in tools using define_tool.

    Called from __init__.py after define_tool is imported.
    """
    from .define import define_tool

    read_tool = define_tool(
        name="read",
        description="Read UTF-8 file content",
        execute=_read_impl,
        side_effect=False,
        idempotent=True,
    )

    write_tool = define_tool(
        name="write",
        description="Write content to file",
        execute=_write_impl,
        side_effect=False,
        idempotent=True,
    )

    edit_tool = define_tool(
        name="edit",
        description="Apply unified diff patch",
        execute=_edit_impl,
        side_effect=False,
        idempotent=True,
    )

    grep_tool = define_tool(
        name="grep",
        description="Search with ripgrep",
        execute=_grep_impl,
        side_effect=False,
        idempotent=True,
    )

    bash_tool = define_tool(
        name="bash",
        description="Execute bash command",
        execute=_bash_impl,
        side_effect=False,
        idempotent=True,
    )

    return {
        "read": read_tool,
        "write": write_tool,
        "edit": edit_tool,
        "grep": grep_tool,
        "bash": bash_tool,
    }


# Module-level tools dict populated by __init__.py
tools: dict[str, Tool] = {}
