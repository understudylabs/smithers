"""Prompt templating helpers.

Mirrors the role of MDX prompts in upstream TS Smithers without
requiring a JSX runtime. Two shapes are supported:

1. **Plain strings.** ``TaskNode.prompt="hello {name}"`` — rendered as
   the literal string at execution time.

2. **PromptTemplate** — a lazy Jinja2-backed renderer. Construct with
   a template string; the runner evaluates ``.render()`` just before
   passing to the agent so workflow authors can interpolate from a
   bound context.

Example:

    from smithers_py.runtime.prompts import PromptTemplate

    prompt = PromptTemplate(
        "Classify lifetimes for {{ file }} in crate {{ crate }}.",
        file="src/http/http.zig",
        crate="http",
    )
    TaskNode(id="classify", prompt=prompt, ...)

PromptTemplate is opt-in. Install Jinja2 with ``uv pip install
'smithers-py[templates]'`` to enable it; without Jinja2 installed,
PromptTemplate falls back to Python's ``str.format(**vars)``.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class PromptTemplate:
    """Lazy prompt template.

    Stores a template string + bound variables. ``render()`` returns
    the interpolated text. The runner calls ``render()`` automatically
    when a TaskNode.prompt is a ``PromptTemplate`` instance.

    Prefers Jinja2 if installed (richer features: loops, conditionals,
    filters); falls back to ``str.format(**vars)`` otherwise.
    """

    __slots__ = ("_template", "_vars", "_engine")

    def __init__(
        self,
        template: str,
        engine: Optional[str] = None,
        **vars: Any,
    ) -> None:
        self._template = template
        self._vars: Dict[str, Any] = vars
        self._engine = engine  # explicit override or auto-detect

    def with_vars(self, **vars: Any) -> "PromptTemplate":
        """Return a new template binding (current + new), without mutation."""
        merged = {**self._vars, **vars}
        return PromptTemplate(self._template, engine=self._engine, **merged)

    def render(self, **extra_vars: Any) -> str:
        """Interpolate and return the final prompt string."""
        scope = {**self._vars, **extra_vars}
        engine = self._engine or self._auto_engine()
        if engine == "jinja2":
            import jinja2
            return jinja2.Template(
                self._template, autoescape=False, undefined=jinja2.StrictUndefined
            ).render(**scope)
        if engine == "format":
            return self._template.format(**scope)
        raise ValueError(f"Unknown prompt template engine: {engine!r}")

    def _auto_engine(self) -> str:
        try:
            import jinja2  # noqa: F401
            return "jinja2"
        except ImportError:
            return "format"

    def __str__(self) -> str:  # pragma: no cover - debug helper
        return f"PromptTemplate({self._template!r}, **{self._vars!r})"


def render_prompt(value: Any) -> str:
    """Resolve a TaskNode.prompt value into a string.

    Accepts:
        - ``None`` → empty string
        - ``str`` → returned as-is
        - ``PromptTemplate`` → ``.render()``
        - any object with a ``render()`` method → returned as string
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, PromptTemplate):
        return value.render()
    render_fn = getattr(value, "render", None)
    if callable(render_fn):
        result = render_fn()
        return str(result) if result is not None else ""
    return str(value)


__all__ = ["PromptTemplate", "render_prompt"]
