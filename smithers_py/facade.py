"""TS-compatible ``createSmithers`` facade.

Mirrors the public ergonomics of the TS function with the same name:

    const { Workflow, Task, Sequence, smithers, outputs } = createSmithers(
        { input: inputSchema, output: outputSchema, scored: scoreSchema },
        { dbPath: ".smithers/demo.db" },
    );

The Python equivalent:

    config = create_smithers(
        schemas={"input": InputSchema, "output": OutputSchema, "scored": ScoreSchema},
        db_path=".smithers/demo.db",
    )
    outputs = config.outputs

    @config.workflow
    def my_workflow(ctx):
        return WorkflowNode(name="demo", children=[
            SequenceNode(children=[
                TaskNode(id="t1", output=outputs.scored,
                         agent=my_agent, prompt="..."),
            ]),
        ])

The facade does three things:

1. **Registers schemas** so each named output has a typed ``OutputRef`` the
   engine can validate Task/Subflow/HumanTask returns against.
2. **Captures durable config** (``db_path``, default agents, etc.) without
   forcing the workflow author to thread it through every node.
3. **Returns a callable wrapper** so users can write ``@config.workflow``
   on a Python function the way TS users write ``smithers((ctx) => ...)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional, Type

from pydantic import BaseModel

from .nodes.ts_compat import OutputRef


# ----- Outputs namespace ------------------------------------------------------


class _Outputs:
    """Attribute-style access to registered output refs.

    ``outputs.foo`` is the OutputRef registered under the name ``"foo"``.
    Missing names raise ``AttributeError`` with a list of the registered
    keys, so workflow authors catch typos before runtime.
    """

    __slots__ = ("_refs",)

    def __init__(self, refs: Mapping[str, OutputRef]) -> None:
        self._refs: Dict[str, OutputRef] = dict(refs)

    def __getattr__(self, name: str) -> OutputRef:
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self._refs[name]
        except KeyError as exc:
            keys = ", ".join(sorted(self._refs))
            raise AttributeError(
                f"No output registered as {name!r}. "
                f"Registered: [{keys}]. "
                f"Did you forget to add it to create_smithers(schemas=...)?"
            ) from exc

    def __getitem__(self, name: str) -> OutputRef:
        return self._refs[name]

    def __iter__(self):
        return iter(self._refs)

    def __contains__(self, name: object) -> bool:
        return name in self._refs

    def __len__(self) -> int:
        return len(self._refs)

    def keys(self):
        return self._refs.keys()

    def items(self):
        return self._refs.items()

    def values(self):
        return self._refs.values()


# ----- Config -----------------------------------------------------------------


@dataclass
class SmithersConfig:
    """Bundled config returned by ``create_smithers``.

    Use ``config.outputs`` to bind Task/Subflow outputs and
    ``@config.workflow`` to register the workflow definition.
    """

    schemas: Dict[str, Type[BaseModel]]
    db_path: str
    outputs: _Outputs
    options: Dict[str, Any] = field(default_factory=dict)
    _registered: list = field(default_factory=list)

    def workflow(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Mark a function as a smithers workflow definition.

        The wrapped function receives a context object and returns a
        ``WorkflowNode`` (or a tree rooted at one). The decorator records
        the registration on the config so external tooling
        (``smithers-py up``, etc.) can discover the workflow without
        re-importing the module.
        """
        # In a real runtime we would attach scheduler / db handles to fn here.
        # For the v0 facade we just stamp metadata and return the function so
        # tests can inspect it without booting the tick loop.
        fn._smithers_workflow = True  # type: ignore[attr-defined]
        fn._smithers_config = self  # type: ignore[attr-defined]
        self._registered.append(fn)
        return fn

    @property
    def input_schema(self) -> Optional[Type[BaseModel]]:
        return self.schemas.get("input")

    @property
    def output_schema(self) -> Optional[Type[BaseModel]]:
        return self.schemas.get("output")


# ----- Factory ----------------------------------------------------------------


def create_smithers(
    schemas: Mapping[str, Type[BaseModel]],
    *,
    db_path: str = "smithers.db",
    **options: Any,
) -> SmithersConfig:
    """Build a ``SmithersConfig`` for a TS-shape workflow.

    ``schemas`` is a mapping from output name → Pydantic model. Each entry
    becomes accessible as ``config.outputs.<name>`` (an ``OutputRef`` carrying
    the schema for validation).

    ``input`` and ``output`` are conventional names: ``input`` is the
    workflow's top-level input shape; ``output`` is its terminal output.
    They're optional — the facade doesn't enforce them — but workflows that
    omit them lose the ability to validate ``ctx.input`` and the final
    return automatically.

    Extra ``**options`` are stashed on the config for engine consumers (e.g.,
    default agent, scorer policies, retention windows).
    """
    if not isinstance(schemas, Mapping) or not schemas:
        raise ValueError(
            "create_smithers(schemas=...) requires a non-empty mapping of "
            "name → Pydantic model"
        )

    refs: Dict[str, OutputRef] = {}
    for name, model in schemas.items():
        if not isinstance(model, type) or not issubclass(model, BaseModel):
            raise TypeError(
                f"schemas[{name!r}] must be a Pydantic BaseModel subclass; "
                f"got {model!r}"
            )
        refs[name] = OutputRef(name=name, schema=model)

    return SmithersConfig(
        schemas=dict(schemas),
        db_path=db_path,
        outputs=_Outputs(refs),
        options=dict(options),
    )


# Camel-cased alias for users coming from the TS API.
createSmithers = create_smithers


__all__ = [
    "SmithersConfig",
    "create_smithers",
    "createSmithers",
]
