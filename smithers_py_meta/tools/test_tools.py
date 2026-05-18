"""Comprehensive tests for tools subsystem.

Covers sandbox security, built-in tools, define_tool factory, and ToolCallLog.
"""

from __future__ import annotations

import os
import tempfile
import warnings
from pathlib import Path

import pytest

from smithers_py_meta.tools import (
    ToolCallLog,
    ToolContext,
    ToolError,
    ToolSecurityError,
    bash,
    check_network_policy,
    define_tool,
    edit,
    grep,
    invoke_tool,
    read,
    resolve_sandboxed_path,
    tools,
    write,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def sandbox_dir():
    """Temporary directory for sandbox operations."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def tool_ctx(sandbox_dir):
    """Default ToolContext rooted at sandbox_dir."""
    return ToolContext(
        root_dir=sandbox_dir,
        allow_network=False,
        run_id="test-run",
        node_id="test-node",
    )


@pytest.fixture
def tool_log(sandbox_dir):
    """ToolCallLog backed by temporary database."""
    db_path = os.path.join(sandbox_dir, "test.db")
    return ToolCallLog(db_path)


# ============================================================================
# Sandbox security tests
# ============================================================================


def test_resolve_sandboxed_path_relative(sandbox_dir):
    """Relative paths resolve within sandbox."""
    result = resolve_sandboxed_path(sandbox_dir, "foo/bar.txt")
    expected = str(Path(sandbox_dir).resolve() / "foo/bar.txt")
    assert result == expected


def test_resolve_sandboxed_path_absolute_inside(sandbox_dir):
    """Absolute paths inside sandbox are accepted."""
    inside = os.path.join(sandbox_dir, "nested", "file.txt")
    result = resolve_sandboxed_path(sandbox_dir, inside)
    expected = str(Path(inside).resolve())
    assert result == expected


def test_resolve_sandboxed_path_absolute_outside(sandbox_dir):
    """Absolute paths outside sandbox are rejected."""
    with pytest.raises(ToolSecurityError, match="escapes sandbox root"):
        resolve_sandboxed_path(sandbox_dir, "/etc/passwd")


def test_resolve_sandboxed_path_dot_dot_escape(sandbox_dir):
    """Dot-dot navigation escaping sandbox is rejected."""
    with pytest.raises(ToolSecurityError, match="escapes sandbox root"):
        resolve_sandboxed_path(sandbox_dir, "../outside.txt")


def test_resolve_sandboxed_path_empty(sandbox_dir):
    """Empty paths are rejected."""
    with pytest.raises(ToolSecurityError, match="Empty path"):
        resolve_sandboxed_path(sandbox_dir, "")

    with pytest.raises(ToolSecurityError, match="Empty path"):
        resolve_sandboxed_path(sandbox_dir, "   ")


def test_resolve_sandboxed_path_symlink_inside(sandbox_dir):
    """Symlinks whose target stays inside are allowed."""
    # Create a file and symlink to it
    target = Path(sandbox_dir) / "target.txt"
    target.write_text("content")

    link = Path(sandbox_dir) / "link.txt"
    link.symlink_to(target)

    result = resolve_sandboxed_path(sandbox_dir, "link.txt")
    assert result == str(target.resolve())


def test_resolve_sandboxed_path_symlink_escape(sandbox_dir):
    """Symlinks whose target escapes sandbox are rejected."""
    # Create symlink pointing outside
    link = Path(sandbox_dir) / "escape.txt"
    link.symlink_to("/etc/passwd")

    with pytest.raises(ToolSecurityError, match="escapes sandbox root"):
        resolve_sandboxed_path(sandbox_dir, "escape.txt")


def test_check_network_policy_blocks_curl(sandbox_dir):
    """Network policy blocks curl."""
    with pytest.raises(ToolSecurityError, match="curl"):
        check_network_policy("curl https://example.com", False)


def test_check_network_policy_blocks_wget(sandbox_dir):
    """Network policy blocks wget."""
    with pytest.raises(ToolSecurityError, match="wget"):
        check_network_policy("wget http://example.com/file", False)


def test_check_network_policy_blocks_https_url(sandbox_dir):
    """Network policy blocks https:// URLs."""
    with pytest.raises(ToolSecurityError, match="https://"):
        check_network_policy("some-tool --url=https://api.example.com", False)


def test_check_network_policy_blocks_git_push(sandbox_dir):
    """Network policy blocks git push."""
    with pytest.raises(ToolSecurityError, match="git push"):
        check_network_policy("git push origin main", False)


def test_check_network_policy_allows_local_git(sandbox_dir):
    """Local git operations are allowed."""
    check_network_policy("git status", False)
    check_network_policy("git commit -m 'msg'", False)
    check_network_policy("git log", False)


def test_check_network_policy_allows_with_flag(sandbox_dir):
    """allow_network=True bypasses all checks."""
    check_network_policy("curl https://example.com", True)
    check_network_policy("wget http://example.com", True)
    check_network_policy("git push origin main", True)


# ============================================================================
# Built-in tool tests
# ============================================================================


@pytest.mark.asyncio
async def test_read_happy_path(tool_ctx):
    """Read returns file content."""
    file_path = os.path.join(tool_ctx.root_dir, "test.txt")
    with open(file_path, "w") as f:
        f.write("Hello, world!")

    result = await read.execute({"path": "test.txt"}, tool_ctx)
    assert result == "Hello, world!"


@pytest.mark.asyncio
async def test_read_missing_file(tool_ctx):
    """Read raises ToolError for missing files."""
    with pytest.raises(ToolError, match="File not found"):
        await read.execute({"path": "missing.txt"}, tool_ctx)


@pytest.mark.asyncio
async def test_read_truncation(tool_ctx):
    """Read truncates large files and appends [truncated]."""
    file_path = os.path.join(tool_ctx.root_dir, "large.txt")
    large_content = "x" * (tool_ctx.max_output_bytes + 1000)
    with open(file_path, "w") as f:
        f.write(large_content)

    result = await read.execute({"path": "large.txt"}, tool_ctx)
    assert len(result) == tool_ctx.max_output_bytes + len("\n[truncated]")
    assert result.endswith("[truncated]")


@pytest.mark.asyncio
async def test_write_happy_path(tool_ctx):
    """Write creates file with content."""
    await write.execute({"path": "new.txt", "content": "test content"}, tool_ctx)

    file_path = os.path.join(tool_ctx.root_dir, "new.txt")
    with open(file_path, "r") as f:
        assert f.read() == "test content"


@pytest.mark.asyncio
async def test_write_creates_parent_dirs(tool_ctx):
    """Write creates parent directories."""
    await write.execute(
        {"path": "nested/deep/file.txt", "content": "nested"}, tool_ctx
    )

    file_path = os.path.join(tool_ctx.root_dir, "nested/deep/file.txt")
    assert os.path.exists(file_path)
    with open(file_path, "r") as f:
        assert f.read() == "nested"


@pytest.mark.asyncio
async def test_write_size_limit(tool_ctx):
    """Write rejects content above size limit."""
    from smithers_py_meta.tools import DEFAULT_FILE_SIZE_LIMIT_BYTES

    too_large = "x" * (DEFAULT_FILE_SIZE_LIMIT_BYTES + 1)
    with pytest.raises(ToolError, match="Content too large"):
        await write.execute({"path": "huge.txt", "content": too_large}, tool_ctx)


@pytest.mark.asyncio
async def test_edit_happy_path(tool_ctx):
    """Edit applies unified diff patch."""
    file_path = os.path.join(tool_ctx.root_dir, "code.py")
    with open(file_path, "w") as f:
        f.write("def foo():\n    return 1\n")

    patch = """@@ -1,2 +1,2 @@
 def foo():
-    return 1
+    return 2
"""

    await edit.execute({"path": "code.py", "patch": patch}, tool_ctx)

    with open(file_path, "r") as f:
        assert f.read() == "def foo():\n    return 2\n"


@pytest.mark.asyncio
async def test_edit_context_mismatch(tool_ctx):
    """Edit raises ToolError when patch doesn't match."""
    file_path = os.path.join(tool_ctx.root_dir, "code.py")
    with open(file_path, "w") as f:
        f.write("def foo():\n    return 1\n")

    # Patch expects different content
    patch = """@@ -1,2 +1,2 @@
 def bar():
-    return 1
+    return 2
"""

    with pytest.raises(ToolError, match="context mismatch"):
        await edit.execute({"path": "code.py", "patch": patch}, tool_ctx)


@pytest.mark.asyncio
async def test_grep_happy_path(tool_ctx):
    """Grep returns matches."""
    file_path = os.path.join(tool_ctx.root_dir, "data.txt")
    with open(file_path, "w") as f:
        f.write("line 1: hello\nline 2: world\nline 3: hello again\n")

    result = await grep.execute({"pattern": "hello", "path": "."}, tool_ctx)
    assert "hello" in result
    assert "data.txt" in result


@pytest.mark.asyncio
async def test_grep_no_matches(tool_ctx):
    """Grep returns empty string when no matches."""
    file_path = os.path.join(tool_ctx.root_dir, "data.txt")
    with open(file_path, "w") as f:
        f.write("line 1\nline 2\n")

    result = await grep.execute({"pattern": "nomatch", "path": "."}, tool_ctx)
    assert result == ""


@pytest.mark.asyncio
async def test_bash_happy_path(tool_ctx):
    """Bash executes command and returns output."""
    result = await bash.execute({"cmd": "echo", "args": ["hello"]}, tool_ctx)
    assert "hello" in result


@pytest.mark.asyncio
async def test_bash_non_zero_exit(tool_ctx):
    """Bash raises ToolError on non-zero exit."""
    with pytest.raises(ToolError, match="Command failed"):
        await bash.execute({"cmd": "false"}, tool_ctx)


@pytest.mark.asyncio
async def test_bash_network_blocked(tool_ctx):
    """Bash blocks network commands when allow_network=False."""
    with pytest.raises(ToolSecurityError, match="Network operation blocked"):
        await bash.execute({"cmd": "curl", "args": ["https://example.com"]}, tool_ctx)


@pytest.mark.asyncio
async def test_bash_network_allowed(tool_ctx):
    """Bash allows network when allow_network=True."""
    ctx = ToolContext(root_dir=tool_ctx.root_dir, allow_network=True)
    # This will fail because curl hits the network, but shouldn't raise SecurityError
    try:
        await bash.execute({"cmd": "curl", "args": ["--version"]}, ctx)
    except ToolError:
        # Expected - command might fail, but not due to security policy
        pass


@pytest.mark.asyncio
async def test_bash_timeout(tool_ctx):
    """Bash times out long-running commands."""
    ctx = ToolContext(
        root_dir=tool_ctx.root_dir, allow_network=False, tool_timeout_ms=500
    )
    with pytest.raises(ToolError, match="timed out"):
        await bash.execute({"cmd": "sleep", "args": ["10"]}, ctx)


# ============================================================================
# define_tool tests
# ============================================================================


@pytest.mark.asyncio
async def test_define_tool_basic(tool_ctx):
    """define_tool creates working tools."""

    async def my_tool_impl(args):
        return f"processed: {args.get('input')}"

    tool = define_tool(
        name="my_tool",
        description="Test tool",
        execute=my_tool_impl,
        side_effect=False,
        idempotent=True,
    )

    result = await tool.execute({"input": "hello"}, tool_ctx)
    assert result == "processed: hello"


@pytest.mark.asyncio
async def test_define_tool_with_ctx(tool_ctx):
    """define_tool detects ctx parameter."""

    async def ctx_tool_impl(args, ctx):
        return f"root: {ctx.root_dir}"

    tool = define_tool(
        name="ctx_tool",
        description="Tool using ctx",
        execute=ctx_tool_impl,
        side_effect=False,
        idempotent=True,
    )

    result = await tool.execute({}, tool_ctx)
    assert tool_ctx.root_dir in result


def test_define_tool_warning_missing_ctx():
    """define_tool warns when non-idempotent side-effect tool lacks ctx."""

    async def bad_impl(args):
        return "side-effect"

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        define_tool(
            name="bad_tool",
            description="Non-idempotent without ctx",
            execute=bad_impl,
            side_effect=True,
            idempotent=False,
        )
        assert len(w) == 1
        assert "doesn't accept ctx parameter" in str(w[0].message)


# ============================================================================
# ToolCallLog tests
# ============================================================================


@pytest.mark.asyncio
async def test_tool_call_log_success(tool_ctx, tool_log):
    """ToolCallLog records successful tool calls."""
    # Create a file to read
    file_path = os.path.join(tool_ctx.root_dir, "test.txt")
    with open(file_path, "w") as f:
        f.write("content")

    result = await invoke_tool(
        read,
        {"path": "test.txt"},
        tool_ctx,
        log=tool_log,
        seq=0,
    )

    records = tool_log.list_for_run("test-run")
    success_records = [r for r in records if r.status == "success"]
    assert len(success_records) >= 1
    assert success_records[-1].tool_name == "read"
    assert success_records[-1].status == "success"


@pytest.mark.asyncio
async def test_tool_call_log_error(tool_ctx, tool_log):
    """ToolCallLog records failed tool calls."""
    try:
        await invoke_tool(
            read,
            {"path": "missing.txt"},
            tool_ctx,
            log=tool_log,
            seq=0,
        )
    except ToolError:
        pass

    records = tool_log.list_for_run("test-run")
    assert len(records) == 1
    assert records[0].status == "error"
    assert records[0].tool_name == "read"
    assert records[0].error_json is not None


@pytest.mark.asyncio
async def test_tool_call_log_filter_by_tool(tool_ctx, tool_log):
    """ToolCallLog filters by tool_name."""
    file_path = os.path.join(tool_ctx.root_dir, "test.txt")
    with open(file_path, "w") as f:
        f.write("content")

    await invoke_tool(read, {"path": "test.txt"}, tool_ctx, log=tool_log, seq=0)
    await invoke_tool(
        write, {"path": "new.txt", "content": "data"}, tool_ctx, log=tool_log, seq=1
    )

    read_records = tool_log.list_for_run("test-run", tool_name="read")
    assert len(read_records) == 1
    assert read_records[0].tool_name == "read"


# ============================================================================
# Bundle tests
# ============================================================================


def test_tools_bundle():
    """tools dict contains all built-ins."""
    assert "read" in tools
    assert "write" in tools
    assert "edit" in tools
    assert "grep" in tools
    assert "bash" in tools
    assert len(tools) == 5
