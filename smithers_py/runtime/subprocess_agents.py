"""Subprocess-based provider adapters.

Wraps CLI tools (``claude``, ``codex``, ``opencode``, Pi RPC) behind a
single ``SubprocessAgent`` base implementing the ``AgentLike`` protocol.
Each subclass tells the base:

  - which binary to invoke (``binary``)
  - which CLI flags to pass for non-interactive single-turn use
  - how to render the prompt body (stdin? ``--input``? a temp file?)
  - how to parse the response into ``{output, text, usage}``

By design the base handles the parts that don't differ between providers:
spawning, timeout, signal handling, JSON-fenced-output extraction,
stderr capture, working directory selection.

These adapters are speculative in the sense that we haven't unit-tested
each one against a live CLI. The shape is correct per upstream
``smithers-orchestrator`` and the public Claude Code / Codex / OpenCode
docs; tweaks may be needed once a specific CLI version pins. Treat each
adapter as a starting template — override ``_build_args`` or
``_extract_output`` if the upstream CLI evolves.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Type

from pydantic import BaseModel


_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(.*)\n```\s*$", re.DOTALL)


@dataclass
class SubprocessAgentResult:
    """Internal helper: raw subprocess outcome before final shaping."""

    stdout: str
    stderr: str
    returncode: int
    duration_ms: int


class SubprocessAgent:
    """Common base for CLI-driven agents.

    Subclasses override:

      - ``binary`` (class attr or constructor): name of the CLI to invoke.
      - ``_build_args(self, prompt, **kwargs)``: returns the full ``argv``
        (excluding the binary itself).
      - Optionally ``_prepare_stdin(self, prompt, **kwargs)``: returns the
        bytes to write to subprocess stdin. Default sends the prompt.

    The default ``_extract_output`` strips code fences and tries
    ``json.loads`` when an ``output_schema`` is supplied; otherwise
    returns ``{"text": stdout}``.
    """

    binary: str = ""  # subclass must override or pass in constructor

    def __init__(
        self,
        *,
        binary: Optional[str] = None,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        default_args: Optional[Sequence[str]] = None,
        id: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        bin_name = binary or self.binary
        if not bin_name:
            raise ValueError(
                f"{type(self).__name__} requires a `binary` "
                "(set as class attr or pass to constructor)"
            )
        self.binary = bin_name
        self.cwd = cwd
        self.env = env
        self.default_args = list(default_args or [])
        self.id = id or f"subprocess:{bin_name}"
        self.timeout_seconds = timeout_seconds

    # ----- Public API ----------------------------------------------------

    def generate(
        self,
        *,
        prompt: str,
        output_schema: Optional[Type[BaseModel]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        if not shutil.which(self.binary):
            raise RuntimeError(
                f"{type(self).__name__}: binary {self.binary!r} not found in PATH. "
                "Install the provider CLI or set `binary=...` to a path."
            )
        argv = [self.binary, *self.default_args, *self._build_args(prompt, **kwargs)]
        stdin = self._prepare_stdin(prompt, **kwargs)
        env = self._build_env()
        import time

        t0 = time.time()
        try:
            proc = subprocess.run(
                argv,
                input=stdin,
                cwd=self.cwd,
                env=env,
                capture_output=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(
                f"{type(self).__name__}: {self.binary} exceeded "
                f"timeout_seconds={self.timeout_seconds}"
            ) from exc
        elapsed_ms = int((time.time() - t0) * 1000)

        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
            raise RuntimeError(
                f"{type(self).__name__}: {self.binary} exited "
                f"code={proc.returncode}: {stderr.strip()[:400]}"
            )

        raw_stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
        raw_stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
        result = SubprocessAgentResult(
            stdout=raw_stdout,
            stderr=raw_stderr,
            returncode=proc.returncode,
            duration_ms=elapsed_ms,
        )
        return self._extract_output(result, output_schema=output_schema)

    # ----- Subclass hooks -----------------------------------------------

    def _build_args(self, prompt: str, **kwargs: Any) -> List[str]:
        return []

    def _prepare_stdin(self, prompt: str, **kwargs: Any) -> Optional[bytes]:
        return prompt.encode("utf-8")

    def _build_env(self) -> Dict[str, str]:
        env = dict(os.environ)
        if self.env:
            env.update(self.env)
        return env

    def _extract_output(
        self,
        result: SubprocessAgentResult,
        *,
        output_schema: Optional[Type[BaseModel]],
    ) -> Dict[str, Any]:
        text = result.stdout.strip()
        parsed: Optional[Dict[str, Any]] = None
        if output_schema is not None and text:
            fence = _CODE_FENCE_RE.match(text)
            candidate = fence.group(1) if fence else text
            try:
                parsed = json.loads(candidate)
            except (ValueError, TypeError):
                parsed = None
        return {
            "output": parsed if parsed is not None else {"text": text},
            "text": text,
            "stderr": result.stderr,
            "duration_ms": result.duration_ms,
        }


# ----- Concrete adapters -----------------------------------------------------


class ClaudeCodeAgent(SubprocessAgent):
    """Adapter for the ``claude`` CLI (Claude Code).

    Spawns ``claude --print`` (non-interactive single-turn) with the
    prompt on stdin. Tool use, permission modes, and allowed-tools lists
    are forwarded as CLI flags so existing harness conventions port.
    """

    binary = "claude"

    def __init__(
        self,
        *,
        cwd: Optional[str] = None,
        model: Optional[str] = None,
        permission_mode: Optional[str] = None,
        allowed_tools: Optional[Sequence[str]] = None,
        disallowed_tools: Optional[Sequence[str]] = None,
        timeout_seconds: Optional[float] = None,
        id: str = "claude-code",
    ) -> None:
        super().__init__(cwd=cwd, timeout_seconds=timeout_seconds, id=id)
        self.model = model
        self.permission_mode = permission_mode
        self.allowed_tools = list(allowed_tools or [])
        self.disallowed_tools = list(disallowed_tools or [])

    def _build_args(self, prompt: str, **kwargs: Any) -> List[str]:
        args: List[str] = ["--print"]
        model = kwargs.get("model") or self.model
        if model:
            args.extend(["--model", model])
        if self.permission_mode:
            args.extend(["--permission-mode", self.permission_mode])
        for t in self.allowed_tools:
            args.extend(["--allowed-tool", t])
        for t in self.disallowed_tools:
            args.extend(["--disallowed-tool", t])
        return args


class CodexAgent(SubprocessAgent):
    """Adapter for OpenAI's ``codex`` CLI.

    The Codex CLI accepts a prompt via stdin (``--stdin``-style) or
    inline. We default to stdin for cross-version stability. Upstream
    fix PR #114 (codex rollout recorder stderr) is encoded here by
    tolerating non-empty stderr on success — we capture it but don't
    fail on it.
    """

    binary = "codex"

    def __init__(
        self,
        *,
        cwd: Optional[str] = None,
        model: Optional[str] = None,
        thinking: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        id: str = "codex",
    ) -> None:
        super().__init__(cwd=cwd, timeout_seconds=timeout_seconds, id=id)
        self.model = model
        self.thinking = thinking

    def _build_args(self, prompt: str, **kwargs: Any) -> List[str]:
        args: List[str] = []
        model = kwargs.get("model") or self.model
        if model:
            args.extend(["--model", model])
        if self.thinking:
            args.extend(["--thinking", self.thinking])
        return args


class OpenCodeAgent(SubprocessAgent):
    """Adapter for the ``opencode`` CLI.

    Mirrors upstream PR #125 (OpenCode integration). Treats stdin as the
    prompt; reads stdout as the response.
    """

    binary = "opencode"

    def __init__(
        self,
        *,
        cwd: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        id: str = "opencode",
    ) -> None:
        super().__init__(cwd=cwd, timeout_seconds=timeout_seconds, id=id)
        self.model = model

    def _build_args(self, prompt: str, **kwargs: Any) -> List[str]:
        args: List[str] = []
        model = kwargs.get("model") or self.model
        if model:
            args.extend(["--model", model])
        return args


class PiAgent(SubprocessAgent):
    """Adapter for the Pi RPC CLI.

    Pi runs in RPC mode emitting NDJSON; the assistant's final message is
    typically the last ``turn_end`` event. Upstream fixes:

      - #85: JSON-mode NDJSON stream extraction — we parse line-by-line
        and pull the final ``turn_end`` payload.
      - #118: wait for terminal assistant response in RPC mode — we
        keep reading until the stream ends rather than returning early.
    """

    binary = "pi"

    def __init__(
        self,
        *,
        cwd: Optional[str] = None,
        provider: str = "openai-codex",
        model: Optional[str] = None,
        mode: str = "rpc",
        thinking: Optional[str] = None,
        tools: Optional[Sequence[str]] = None,
        timeout_seconds: Optional[float] = None,
        id: str = "pi",
    ) -> None:
        super().__init__(cwd=cwd, timeout_seconds=timeout_seconds, id=id)
        self.provider = provider
        self.model = model
        self.mode = mode
        self.thinking = thinking
        self.tools = list(tools or [])

    def _build_args(self, prompt: str, **kwargs: Any) -> List[str]:
        args: List[str] = [
            "--provider", self.provider,
            "--mode", self.mode,
        ]
        if self.model:
            args.extend(["--model", self.model])
        if self.thinking:
            args.extend(["--thinking", self.thinking])
        for t in self.tools:
            args.extend(["--tool", t])
        return args

    def _extract_output(
        self,
        result: SubprocessAgentResult,
        *,
        output_schema: Optional[Type[BaseModel]],
    ) -> Dict[str, Any]:
        """Parse the NDJSON stream and pull the terminal assistant text.

        The shape isn't pinned across Pi versions, so we look for the
        last event containing a ``text`` field (the final assistant
        turn) and use that. Fall back to plain stdout if the stream
        isn't NDJSON.
        """
        text = result.stdout.strip()
        final_text = ""
        events: List[Dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except (ValueError, TypeError):
                continue
            events.append(evt)
            if isinstance(evt, dict):
                # Common shapes: {"text": "..."} or
                # {"event": "turn_end", "text": "..."} or
                # {"role": "assistant", "content": "..."}.
                t = evt.get("text") or evt.get("content")
                if t:
                    final_text = t
        text_to_use = final_text or text

        parsed: Optional[Dict[str, Any]] = None
        if output_schema is not None and text_to_use:
            fence = _CODE_FENCE_RE.match(text_to_use)
            candidate = fence.group(1) if fence else text_to_use
            try:
                parsed = json.loads(candidate)
            except (ValueError, TypeError):
                parsed = None
        return {
            "output": parsed if parsed is not None else {"text": text_to_use},
            "text": text_to_use,
            "stderr": result.stderr,
            "duration_ms": result.duration_ms,
            "events_seen": len(events),
        }


__all__ = [
    "ClaudeCodeAgent",
    "CodexAgent",
    "OpenCodeAgent",
    "PiAgent",
    "SubprocessAgent",
    "SubprocessAgentResult",
]
