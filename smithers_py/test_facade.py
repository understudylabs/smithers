"""Tests for the create_smithers facade."""

import pytest
from pydantic import BaseModel

from smithers_py import create_smithers, createSmithers
from smithers_py.nodes.ts_compat import OutputRef, TaskNode, WorkflowNode


class _Input(BaseModel):
    workload_id: str


class _Output(BaseModel):
    schema_version: str = "test-final-v0"
    summary: str


class _Score(BaseModel):
    schema_version: str = "test-score-v0"
    accuracy: float


def test_returns_config_with_outputs_namespace() -> None:
    cfg = create_smithers(
        schemas={"input": _Input, "output": _Output, "scored": _Score},
        db_path="/tmp/test.db",
    )
    assert cfg.db_path == "/tmp/test.db"
    assert isinstance(cfg.outputs.scored, OutputRef)
    assert cfg.outputs.scored.name == "scored"
    assert cfg.outputs.scored.schema_ is _Score


def test_outputs_accessor_raises_on_unknown_key() -> None:
    cfg = create_smithers(schemas={"output": _Output})
    with pytest.raises(AttributeError) as exc_info:
        _ = cfg.outputs.nonexistent
    assert "Registered: [output]" in str(exc_info.value)


def test_outputs_subscript_and_iteration() -> None:
    cfg = create_smithers(schemas={"a": _Score, "b": _Output})
    assert cfg.outputs["a"].name == "a"
    assert sorted(cfg.outputs) == ["a", "b"]
    assert "a" in cfg.outputs
    assert len(cfg.outputs) == 2


def test_requires_pydantic_subclass() -> None:
    with pytest.raises(TypeError):
        create_smithers(schemas={"bad": dict})  # type: ignore[arg-type]


def test_requires_non_empty_mapping() -> None:
    with pytest.raises(ValueError):
        create_smithers(schemas={})


def test_workflow_decorator_stamps_metadata() -> None:
    cfg = create_smithers(schemas={"output": _Output})

    @cfg.workflow
    def my_wf(ctx):
        return WorkflowNode(name="x")

    assert getattr(my_wf, "_smithers_workflow") is True
    assert my_wf._smithers_config is cfg
    assert my_wf in cfg._registered
    # Decorator is non-destructive: function still callable.
    node = my_wf(ctx=None)
    assert isinstance(node, WorkflowNode)


def test_camel_alias_is_same_function() -> None:
    assert createSmithers is create_smithers


def test_outputs_can_bind_task() -> None:
    cfg = create_smithers(schemas={"scored": _Score})
    task = TaskNode(
        id="t",
        output=cfg.outputs.scored,
        render=lambda: {"accuracy": 0.9},
    )
    assert task.output_target is cfg.outputs.scored
    assert task.output_target.schema_ is _Score


def test_input_output_convenience_props() -> None:
    cfg = create_smithers(schemas={"input": _Input, "output": _Output, "scored": _Score})
    assert cfg.input_schema is _Input
    assert cfg.output_schema is _Output


def test_input_output_props_none_when_missing() -> None:
    cfg = create_smithers(schemas={"scored": _Score})
    assert cfg.input_schema is None
    assert cfg.output_schema is None


def test_options_pass_through() -> None:
    cfg = create_smithers(
        schemas={"output": _Output},
        db_path="x.db",
        max_concurrency=16,
        default_agent="claude",
    )
    assert cfg.options == {"max_concurrency": 16, "default_agent": "claude"}
