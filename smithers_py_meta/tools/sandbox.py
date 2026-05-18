"""Sandbox security primitives for path containment and network policy.

All file operations must pass through resolve_sandboxed_path. Bash commands
must pass network policy checks when allow_network=False.
"""

from __future__ import annotations

import os
from pathlib import Path

_BLOCKED_NETWORK_FRAGMENTS = [
    "curl",
    "wget",
    "http://",
    "https://",
    "npm",
    "bun",
    "pip",
    "git push",
    "git pull",
    "git fetch",
    "git clone",
    "git remote",
]


class ToolSecurityError(Exception):
    """Raised when a tool operation violates sandbox security policy."""

    pass


def resolve_sandboxed_path(root_dir: str, path: str) -> str:
    """Resolve and validate path is contained within root_dir.

    Args:
        root_dir: Sandbox root (must be absolute)
        path: Relative or absolute path to validate

    Returns:
        Absolute path within root_dir

    Raises:
        ToolSecurityError: If path is empty, escapes root, or symlink target
            escapes root (including parent symlinks)
    """
    if not path or not path.strip():
        raise ToolSecurityError("Empty path not allowed")

    root = Path(root_dir).resolve()

    # Convert relative paths to absolute within root
    if not os.path.isabs(path):
        candidate = root / path
    else:
        candidate = Path(path)

    # Resolve symlinks and normalize
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError) as e:
        raise ToolSecurityError(f"Cannot resolve path: {e}")

    # Check containment
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ToolSecurityError(
            f"Path '{path}' escapes sandbox root '{root_dir}'"
        )

    return str(resolved)


def check_network_policy(command: str, allow_network: bool) -> None:
    """Verify bash command complies with network policy.

    Args:
        command: Full command string (cmd + args joined)
        allow_network: If True, skip all checks

    Raises:
        ToolSecurityError: If command contains blocked network fragments
    """
    if allow_network:
        return

    cmd_lower = command.lower()
    for fragment in _BLOCKED_NETWORK_FRAGMENTS:
        if fragment in cmd_lower:
            raise ToolSecurityError(
                f"Network operation blocked: '{fragment}' in command"
            )
