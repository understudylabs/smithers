"""Path resolution + network-block helpers for the tools sandbox.

Two distinct safety layers:

1. **Path containment** — every filesystem operation must stay inside
   ``ToolContext.root_dir``. Paths are resolved (following symlinks)
   and rejected if the resolved target is outside the root. This is
   the standard "no path traversal" defense, plus a symlink check that
   catches the trick where a symlink at ``inside_root/safe`` points to
   ``/etc/passwd``.

2. **Network-command blocking** — ``bash`` substrings that imply
   network access (``curl``, ``wget``, ``http://``, ``https://``,
   ``npm``, ``bun``, ``pip``, ``git push|pull|fetch|clone|remote``)
   are rejected before execution when ``allow_network=False``. Mirrors
   the upstream block list.

These helpers raise ``ToolSecurityError`` on policy violation. Callers
should catch and propagate as a clear error so the agent's tool loop
sees the problem and either retries with a different argument or gives
up cleanly.
"""

from __future__ import annotations

import os
from pathlib import Path


class ToolSecurityError(RuntimeError):
    """Raised when a tool call would violate a sandbox policy."""


# Network-implying substrings. Checked case-sensitively against the
# joined command + args, matching upstream behavior.
_BLOCKED_NETWORK_FRAGMENTS: tuple[str, ...] = (
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
)


def resolve_sandboxed_path(root_dir: str, path: str) -> str:
    """Resolve ``path`` relative to ``root_dir`` and verify containment.

    - Relative paths join under ``root_dir``.
    - Absolute paths are accepted iff they're inside ``root_dir``.
    - Symlinks are resolved before the containment check, so a symlink
      that escapes the sandbox is rejected.

    Returns the resolved absolute path on success; raises
    ``ToolSecurityError`` on policy violation.
    """
    if not path:
        raise ToolSecurityError("path cannot be empty")

    root = Path(root_dir).resolve()
    candidate = Path(path) if os.path.isabs(path) else root / path

    # Resolve symlinks. ``strict=False`` so we can still check non-existent
    # paths (write to a new file should be allowed under the root).
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ToolSecurityError(f"cannot resolve path {path!r}: {exc}") from exc

    # Containment check.
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ToolSecurityError(
            f"path {path!r} escapes sandbox root {root!s}"
        ) from exc

    # Even if the path itself looks contained, a symlink ancestor that
    # points outside is unsafe. Walk back up; any link target outside
    # root is a violation.
    for ancestor in [resolved, *resolved.parents]:
        if ancestor == root:
            break
        if ancestor.is_symlink():
            try:
                link_resolved = ancestor.resolve(strict=False)
                link_resolved.relative_to(root)
            except ValueError as exc:
                raise ToolSecurityError(
                    f"symlink {ancestor!s} escapes sandbox root {root!s}"
                ) from exc

    return str(resolved)


def check_network_policy(command_text: str, allow_network: bool) -> None:
    """Reject ``command_text`` if it implies network access and the
    sandbox doesn't allow it.

    ``command_text`` should be the entire command + args joined by
    spaces — the way it would appear on a real shell line. Matching is
    substring-based against the upstream block list.
    """
    if allow_network:
        return
    lowered = command_text.lower()
    for fragment in _BLOCKED_NETWORK_FRAGMENTS:
        if fragment in lowered:
            raise ToolSecurityError(
                f"network access blocked: command contains {fragment!r}; "
                f"set ToolContext.allow_network=True to permit"
            )
