"""Tests for the smithers_py tools sandbox.

Coverage:
- Sandbox path containment (relative, absolute, symlink escape)
- Network policy (block list, allow override)
- read / write / edit / grep / bash happy paths + edge cases
- define_tool factory (side-effect warning, ctx parameter detection)
- ToolCallLog persistence (success + error rows)
- invoke_tool wraps the call with logging
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import warnings
from pathlib import Path

import pytest

from smithers_py.tools import (
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


# ----- fixtures -------------------------------------------------------------


@pytest.fixture
def sandbox_root():
    """A temp dir to serve as the sandbox root."""
    root = tempfile.mkdtemp(prefix="smithers-tools-")
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def ctx(sandbox_root):
    """A ToolContext rooted at the temp sandbox."""
    return ToolContext(
        root_dir=sandbox_root,
        allow_network=False,
        run_id="test-run",
        node_id="test-node",
    )


@pytest.fixture
def log_path():
    """A temp SQLite path for ToolCallLog."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        yield path
    finally:
        for suffix in ("", "-wal", "-shm"):
            cand = path + suffix
            if os.path.exists(cand):
                try:
                    os.unlink(cand)
                except OSError:
                    pass


# ----- path containment -----------------------------------------------------


def test_resolve_relative_path(sandbox_root):
    result = resolve_sandboxed_path(sandbox_root, "subdir/file.txt")
    assert result.startswith(os.path.realpath(sandbox_root))
    assert result.endswith("subdir/file.txt")


def test_resolve_absolute_inside_root(sandbox_root):
    abs_inside = os.path.join(sandbox_root, "file.txt")
    result = resolve_sandboxed_path(sandbox_root, abs_inside)
    assert os.path.realpath(result) == os.path.realpath(abs_inside)


def test_reject_absolute_outside_root(sandbox_root):
    with pytest.raises(ToolSecurityError, match="escapes sandbox"):
        resolve_sandboxed_path(sandbox_root, "/etc/passwd")


def test_reject_dot_dot_escape(sandbox_root):
    with pytest.raises(ToolSecurityError, match="escapes sandbox"):
        resolve_sandboxed_path(sandbox_root, "../../../etc/passwd")


def test_reject_symlink_escape(sandbox_root):
    # Create a symlink inside the sandbox pointing outside.
    outside = tempfile.mkdtemp(prefix="smithers-outside-")
    try:
        link = os.path.join(sandbox_root, "escape-link")
        os.symlink(outside, link)
        with pytest.raises(ToolSecurityError):
            resolve_sandboxed_path(sandbox_root, "escape-link/secret.txt")
    finally:
        shutil.rmtree(outside, ignore_errors=True)


def test_reject_empty_path(sandbox_root):
    with pytest.raises(ToolSecurityError, match="empty"):
        resolve_sandboxed_path(sandbox_root, "")


# ----- network policy -------------------------------------------------------


def test_block_curl():
    with pytest.raises(ToolSecurityError, match="network"):
        check_network_policy("curl https://example.com", allow_network=False)


def test_block_https_url():
    with pytest.raises(ToolSecurityError):
        check_network_policy("xargs https://example.com", allow_network=False)


def test_block_git_push():
    with pytest.raises(ToolSecurityError):
        check_network_policy("git push origin main", allow_network=False)


def test_allow_local_git():
    # Local git commands shouldn't trigger the block list.
    check_network_policy("git status", allow_network=False)
    check_network_policy("git diff HEAD~1", allow_network=False)
    check_network_policy("git log --oneline", allow_network=False)


def test_allow_network_disables_check():
    # allow_network=True bypasses the block list entirely.
    check_network_policy("curl https://example.com", allow_network=True)


# ----- read ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_returns_file_contents(ctx, sandbox_root):
    p = Path(sandbox_root) / "hello.txt"
    p.write_text("hello world\n")
    result = await read.execute({"path": "hello.txt"}, ctx)
    assert result == "hello world\n"


@pytest.mark.asyncio
async def test_read_missing_file_raises(ctx):
    with pytest.raises(ToolError, match="file not found"):
        await read.execute({"path": "no-such-file.txt"}, ctx)


@pytest.mark.asyncio
async def test_read_truncates_at_max_output(ctx, sandbox_root):
    p = Path(sandbox_root) / "big.txt"
    p.write_text("a" * 500_000)
    ctx.max_output_bytes = 100
    result = await read.execute({"path": "big.txt"}, ctx)
    assert result.endswith("[truncated]")
    assert len(result) <= 200  # 100 bytes + truncation marker


@pytest.mark.asyncio
async def test_read_rejects_escape(ctx):
    with pytest.raises(ToolSecurityError):
        await read.execute({"path": "../../../etc/passwd"}, ctx)


# ----- write ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_creates_file(ctx, sandbox_root):
    result = await write.execute(
        {"path": "out.txt", "content": "wrote it"}, ctx
    )
    assert result == "ok"
    assert (Path(sandbox_root) / "out.txt").read_text() == "wrote it"


@pytest.mark.asyncio
async def test_write_creates_parent_dirs(ctx, sandbox_root):
    await write.execute(
        {"path": "sub/dir/file.txt", "content": "nested"}, ctx
    )
    assert (Path(sandbox_root) / "sub" / "dir" / "file.txt").read_text() == "nested"


@pytest.mark.asyncio
async def test_write_rejects_escape(ctx):
    with pytest.raises(ToolSecurityError):
        await write.execute(
            {"path": "/tmp/escaped.txt", "content": "no"}, ctx
        )


# ----- edit ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_applies_unified_diff(ctx, sandbox_root):
    p = Path(sandbox_root) / "file.py"
    p.write_text("line1\nline2\nline3\n")
    patch = (
        "--- a/file.py\n"
        "+++ b/file.py\n"
        "@@ -1,3 +1,3 @@\n"
        " line1\n"
        "-line2\n"
        "+LINE2\n"
        " line3\n"
    )
    result = await edit.execute({"path": "file.py", "patch": patch}, ctx)
    assert result == "ok"
    assert p.read_text() == "line1\nLINE2\nline3\n"


@pytest.mark.asyncio
async def test_edit_rejects_missing_file(ctx):
    with pytest.raises(ToolError, match="not found"):
        await edit.execute(
            {"path": "missing.txt", "patch": "@@ -1 +1 @@\n-a\n+b\n"}, ctx
        )


@pytest.mark.asyncio
async def test_edit_rejects_bad_context(ctx, sandbox_root):
    p = Path(sandbox_root) / "file.py"
    p.write_text("actual\n")
    patch = (
        "@@ -1 +1 @@\n"
        "-wrong-context\n"  # doesn't match the actual content
        "+replacement\n"
    )
    with pytest.raises(ToolError, match="hunks did not match"):
        await edit.execute({"path": "file.py", "patch": patch}, ctx)


# ----- grep ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_grep_finds_matches(ctx, sandbox_root):
    if shutil.which("rg") is None:
        pytest.skip("ripgrep not installed")
    p = Path(sandbox_root) / "code.py"
    p.write_text("def hello():\n    return 'world'\n\ndef other():\n    pass\n")
    result = await grep.execute({"pattern": "def ", "path": "code.py"}, ctx)
    assert "hello" in result
    assert "other" in result


@pytest.mark.asyncio
async def test_grep_no_match_returns_empty(ctx, sandbox_root):
    if shutil.which("rg") is None:
        pytest.skip("ripgrep not installed")
    p = Path(sandbox_root) / "code.py"
    p.write_text("hello\n")
    result = await grep.execute({"pattern": "xyzzy", "path": "code.py"}, ctx)
    assert result == ""


# ----- bash ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_bash_runs_command(ctx):
    result = await bash.execute({"cmd": "echo", "args": ["hello"]}, ctx)
    assert result.strip() == "hello"


@pytest.mark.asyncio
async def test_bash_blocks_network_by_default(ctx):
    with pytest.raises(ToolSecurityError, match="network"):
        await bash.execute({"cmd": "curl", "args": ["example.com"]}, ctx)


@pytest.mark.asyncio
async def test_bash_allows_network_when_enabled(ctx):
    ctx.allow_network = True
    # Use a fake `curl` argument; we don't actually need it to succeed,
    # we just need the policy check to pass. /bin/true is portable.
    result = await bash.execute({"cmd": "true", "args": ["curl-marker"]}, ctx)
    assert result == ""


@pytest.mark.asyncio
async def test_bash_timeout_kills_command(ctx):
    ctx.tool_timeout_ms = 200
    with pytest.raises(ToolError, match="timed out"):
        await bash.execute({"cmd": "sleep", "args": ["5"]}, ctx)


@pytest.mark.asyncio
async def test_bash_nonzero_exit_raises(ctx):
    with pytest.raises(ToolError, match="exit"):
        await bash.execute({"cmd": "false"}, ctx)


# ----- define_tool ---------------------------------------------------------


@pytest.mark.asyncio
async def test_define_tool_basic(ctx):
    async def my_exec(args, _ctx):
        return args["x"] * 2

    t = define_tool(
        name="my-tool",
        description="doubles x",
        execute=my_exec,
    )
    assert t.name == "my-tool"
    assert t.side_effect is False
    assert t.idempotent is True
    assert await t.execute({"x": 5}, ctx) == 10


@pytest.mark.asyncio
async def test_define_tool_warns_on_side_effect_without_ctx():
    async def bad_exec(args):
        return None

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        define_tool(
            name="bad-tool",
            description="dangerous",
            execute=bad_exec,
            side_effect=True,
            idempotent=False,
        )
        assert any("ctx parameter" in str(w.message) for w in caught)


@pytest.mark.asyncio
async def test_define_tool_no_warning_when_idempotent():
    async def fine_exec(args):
        return None

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        define_tool(
            name="fine-tool",
            description="safe",
            execute=fine_exec,
            side_effect=True,
            idempotent=True,
        )
        assert not any("ctx parameter" in str(w.message) for w in caught)


# ----- ToolCallLog ---------------------------------------------------------


def test_tool_call_log_records_success_row(log_path, sandbox_root):
    ctx = ToolContext(
        root_dir=sandbox_root,
        run_id="r1",
        node_id="n1",
        iteration=0,
        attempt=0,
    )
    p = Path(sandbox_root) / "hi.txt"
    p.write_text("hi")
    log = ToolCallLog(log_path)
    result = asyncio.run(invoke_tool(read, {"path": "hi.txt"}, ctx, log=log, seq=0))
    assert result == "hi"
    rows = log.list_for_run("r1")
    assert len(rows) == 1
    assert rows[0].tool_name == "read"
    assert rows[0].status == "success"
    assert rows[0].output_json is not None


def test_tool_call_log_records_error_row(log_path, sandbox_root):
    ctx = ToolContext(
        root_dir=sandbox_root,
        run_id="r1",
        node_id="n1",
    )
    log = ToolCallLog(log_path)
    with pytest.raises(ToolError):
        asyncio.run(
            invoke_tool(read, {"path": "nope.txt"}, ctx, log=log, seq=0)
        )
    rows = log.list_for_run("r1")
    assert len(rows) == 1
    assert rows[0].status == "error"
    assert rows[0].error_json is not None
    assert "not found" in rows[0].error_json


def test_tool_call_log_filters_by_tool_name(log_path, sandbox_root):
    ctx = ToolContext(
        root_dir=sandbox_root,
        run_id="r1",
        node_id="n1",
    )
    Path(sandbox_root, "a.txt").write_text("a")
    log = ToolCallLog(log_path)
    asyncio.run(invoke_tool(read, {"path": "a.txt"}, ctx, log=log, seq=0))
    asyncio.run(
        invoke_tool(write, {"path": "b.txt", "content": "b"}, ctx, log=log, seq=1)
    )
    reads = log.list_for_run("r1", tool_name="read")
    writes = log.list_for_run("r1", tool_name="write")
    assert len(reads) == 1 and reads[0].tool_name == "read"
    assert len(writes) == 1 and writes[0].tool_name == "write"


# ----- bundle --------------------------------------------------------------


def test_tools_bundle_contains_all_builtins():
    assert set(tools.keys()) == {"read", "write", "edit", "grep", "bash"}
    assert tools["read"].name == "read"
    assert tools["bash"].name == "bash"
