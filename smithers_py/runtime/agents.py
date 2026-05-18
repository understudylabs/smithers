"""Agent protocol surface for the TS-shape runtime.

Defines ``AgentLike`` — the duck-typed contract ``TaskNode.agent`` must
satisfy. Modeled after the upstream TS ``AgentLike`` interface and the
PydanticAI executor already living in ``smithers_py/executors/``. Lets
provider adapters (Claude Code, Anthropic SDK, Codex CLI, OpenCode,
Pi) plug in without engine changes.

The minimum contract is a single method:

    def generate(self, *, prompt: str, **kwargs) -> dict | AgentResult: ...

The return value may be either:

  - A plain ``dict`` matching the Task's output schema.
  - A dict with an ``"output"`` key whose value matches the schema (and
    optional ``"text"`` / ``"usage"`` / ``"tool_calls"`` siblings). This
    is the shape PydanticAI's structured-output runs emit; the runner
    auto-unwraps ``"output"`` if present.

Async agents are supported via ``AsyncAgentLike`` — the runner will be
extended to await ``.generate(...)`` automatically once asyncio
integration lands in v0.2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Dict, Optional, Protocol, Type, Union, runtime_checkable

from pydantic import BaseModel


@dataclass
class AgentResult:
    """Structured agent return shape.

    Mirrors TS ``AgentResult``. ``output`` is the schema-validated
    payload; ``text`` is the raw assistant message; ``usage`` is the
    token-count metadata.
    """

    output: Dict[str, Any]
    text: str = ""
    usage: Dict[str, Any] = field(default_factory=dict)
    tool_calls: list = field(default_factory=list)


@runtime_checkable
class AgentLike(Protocol):
    """Synchronous agent surface ``TaskNode.agent`` may bind against.

    Implementers may also expose:

    - ``id: str`` — stable identifier surfaced in observability logs.
    - ``model: str`` — model name (e.g., ``"claude-sonnet-4"``).
    """

    def generate(
        self,
        *,
        prompt: str,
        output_schema: Optional[Type[BaseModel]] = None,
        **kwargs: Any,
    ) -> Union[Dict[str, Any], AgentResult]:  # pragma: no cover - protocol
        ...


@runtime_checkable
class AsyncAgentLike(Protocol):
    """Async agent surface. The runner will dispatch through ``asyncio``
    once v0.2 lands; for now, prefer the sync ``AgentLike``."""

    async def generate(
        self,
        *,
        prompt: str,
        output_schema: Optional[Type[BaseModel]] = None,
        **kwargs: Any,
    ) -> Union[Dict[str, Any], AgentResult]:  # pragma: no cover - protocol
        ...


# ----- Dry agent (deterministic, no LLM) -------------------------------------


class DryAgent:
    """Trivial deterministic agent.

    Returns a fixed payload regardless of prompt. Useful for end-to-end
    tests, smoke runs, and bun-port-py's dry mode. The ``id`` attribute
    is set so observability logs distinguish dry from real runs.
    """

    def __init__(
        self,
        *,
        id: str = "dry-agent",
        output: Optional[Dict[str, Any]] = None,
        output_fn: Optional[Any] = None,
    ) -> None:
        if output is None and output_fn is None:
            raise ValueError("DryAgent requires either output=... or output_fn=...")
        if output is not None and output_fn is not None:
            raise ValueError("DryAgent: pass either output=... or output_fn=..., not both")
        self.id = id
        self._output = output
        self._output_fn = output_fn

    def generate(
        self,
        *,
        prompt: str,
        output_schema: Optional[Type[BaseModel]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        if self._output_fn is not None:
            return {"output": self._output_fn(prompt=prompt, **kwargs)}
        return {"output": dict(self._output or {})}


__all__ = [
    "AgentLike",
    "AsyncAgentLike",
    "AgentResult",
    "DryAgent",
]
