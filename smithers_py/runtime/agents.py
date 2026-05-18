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


# ----- Anthropic adapter (real-mode) -----------------------------------------


class AnthropicAgent:
    """Real-mode agent that calls the Anthropic SDK.

    Implements ``AgentLike`` and routes prompts through
    ``anthropic.Anthropic().messages.create(...)``. The ``output``
    field of the returned dict is either:

    - the assistant text wrapped as ``{"text": <body>}`` when no
      ``output_schema`` is supplied, OR
    - a structured payload extracted from the assistant message when
      ``output_schema`` is set. Two extraction strategies are tried in
      order:
        1. Anthropic's native tool-use response if a tool definition
           was registered.
        2. JSON parse of the assistant text, fenced-block-tolerant.

    Construct with explicit ``api_key`` or rely on ``ANTHROPIC_API_KEY``.
    Optional ``model`` defaults to ``"claude-sonnet-4-5"``;
    override per-call via ``generate(model=...)``.

    Install the SDK alongside smithers_py:

        uv pip install anthropic
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: str = "claude-sonnet-4-5",
        max_tokens: int = 4096,
        system: Optional[str] = None,
        id: str = "anthropic",
    ) -> None:
        try:
            import anthropic  # noqa: F401 - we import at use time too
        except ImportError as exc:
            raise RuntimeError(
                "AnthropicAgent requires the `anthropic` SDK. "
                "Install with: uv pip install anthropic"
            ) from exc
        self.id = id
        self.model = model
        self.max_tokens = max_tokens
        self.system = system
        self._api_key = api_key
        self._client = None

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def generate(
        self,
        *,
        prompt: str,
        output_schema=None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        system: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        import json
        import re

        client = self._get_client()
        params = {
            "model": model or self.model,
            "max_tokens": max_tokens or self.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        sys_prompt = system or self.system
        if sys_prompt:
            params["system"] = sys_prompt

        # If a schema is set, append a structured-output instruction.
        if output_schema is not None:
            schema_json = output_schema.model_json_schema()
            params["messages"] = [
                {
                    "role": "user",
                    "content": (
                        f"{prompt}\n\n"
                        f"Respond with a single JSON object matching this schema "
                        f"(no surrounding prose, no markdown fences):\n"
                        f"{json.dumps(schema_json)}"
                    ),
                }
            ]

        response = client.messages.create(**params)

        # Extract assistant text.
        text_parts = []
        for block in response.content:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text_parts.append(block.text)
        raw_text = "".join(text_parts)

        # Try structured-output extraction if a schema was provided.
        parsed: Optional[Dict[str, Any]] = None
        if output_schema is not None and raw_text:
            stripped = raw_text.strip()
            # Strip code fences if the model added them.
            fence = re.match(
                r"^```(?:json)?\s*\n(.*)\n```\s*$", stripped, re.DOTALL
            )
            candidate = fence.group(1) if fence else stripped
            try:
                parsed = json.loads(candidate)
            except (ValueError, TypeError):
                parsed = None

        output = parsed if parsed is not None else {"text": raw_text}
        usage = {}
        if getattr(response, "usage", None) is not None:
            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }

        return {"output": output, "text": raw_text, "usage": usage}


__all__ = [
    "AgentLike",
    "AsyncAgentLike",
    "AgentResult",
    "AnthropicAgent",
    "DryAgent",
]
