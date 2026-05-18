"""Tests for the TS-compatibility node types.

Covers construction, default values, alias handling (camelCase ↔ snake_case
input), validation errors, and JSON round-tripping. These are the
contract tests for the public TS shape on the Python side.
"""

import json

import pytest
from pydantic import BaseModel, ValidationError

from smithers_py.nodes.ts_compat import (
    ApprovalGateNode,
    ApprovalRequest,
    HumanTaskNode,
    OutputRef,
    ParallelNode,
    SequenceNode,
    SubflowNode,
    TaskNode,
    WorkflowNode,
)


class _OutSchema(BaseModel):
    schema_version: str = "test-output-v0"
    value: str


# ----- OutputRef --------------------------------------------------------------


class TestOutputRef:
    def test_construct_with_alias(self) -> None:
        ref = OutputRef(name="thing", schema=_OutSchema)
        assert ref.name == "thing"
        assert ref.schema_ is _OutSchema

    def test_construct_with_field_name(self) -> None:
        ref = OutputRef(name="thing", schema_=_OutSchema)
        assert ref.schema_ is _OutSchema

    def test_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            OutputRef(name="thing", schema=_OutSchema, bogus="x")


# ----- WorkflowNode -----------------------------------------------------------


class TestWorkflowNode:
    def test_construct_minimal(self) -> None:
        wf = WorkflowNode(name="my-wf")
        assert wf.type == "workflow"
        assert wf.name == "my-wf"
        assert wf.cache is False
        assert wf.children == []

    def test_round_trip_json(self) -> None:
        wf = WorkflowNode(name="rt", cache=True)
        payload = wf.model_dump(mode="json")
        assert payload["type"] == "workflow"
        assert payload["name"] == "rt"
        revived = WorkflowNode.model_validate(payload)
        assert revived.name == "rt"
        assert revived.cache is True


# ----- SequenceNode / ParallelNode -------------------------------------------


class TestStructural:
    def test_sequence_default(self) -> None:
        s = SequenceNode()
        assert s.type == "sequence"
        assert s.children == []

    def test_parallel_default_concurrency(self) -> None:
        p = ParallelNode()
        assert p.type == "parallel"
        assert p.max_concurrency == 8

    def test_parallel_accepts_camel_alias(self) -> None:
        p = ParallelNode(maxConcurrency=16)
        assert p.max_concurrency == 16

    def test_parallel_rejects_zero(self) -> None:
        with pytest.raises(ValidationError):
            ParallelNode(max_concurrency=0)


# ----- TaskNode ---------------------------------------------------------------


class TestTaskNode:
    def _ref(self) -> OutputRef:
        return OutputRef(name="t", schema=_OutSchema)

    def test_static_task_with_render(self) -> None:
        ref = self._ref()
        t = TaskNode(id="t1", output=ref, render=lambda: {"value": "hi"})
        assert t.id == "t1"
        assert t.output_target is ref
        assert t.agent is None
        assert callable(t.render)

    def test_agent_task_with_prompt(self) -> None:
        # Agent can be any object; engine duck-types it.
        ref = self._ref()
        t = TaskNode(id="t2", output=ref, agent=object(), prompt="hi")
        assert t.agent is not None
        assert t.prompt == "hi"

    def test_requires_payload_source(self) -> None:
        ref = self._ref()
        with pytest.raises(ValidationError):
            # No agent, no render, no children → invalid.
            TaskNode(id="bad", output=ref)

    def test_requires_output_binding(self) -> None:
        with pytest.raises(ValidationError):
            TaskNode(id="bad", render=lambda: {"value": "x"})

    def test_accepts_inline_output_schema(self) -> None:
        t = TaskNode(
            id="t3",
            output_schema=_OutSchema,
            render=lambda: {"value": "x"},
        )
        assert t.output_target is None
        assert t.output_schema is _OutSchema

    def test_camel_aliases(self) -> None:
        ref = self._ref()
        t = TaskNode(
            id="t4",
            output=ref,
            render=lambda: {"value": "x"},
            timeoutMs=5000,
            maxAttempts=3,
            dependsOn=["other"],
        )
        assert t.timeout_ms == 5000
        assert t.max_attempts == 3
        assert t.depends_on == ["other"]


# ----- SubflowNode ------------------------------------------------------------


class TestSubflowNode:
    def test_construct(self) -> None:
        ref = OutputRef(name="child", schema=_OutSchema)

        def child_wf(ctx):  # pragma: no cover - exercised by engine
            return WorkflowNode(name="child")

        sf = SubflowNode(id="sf", workflow=child_wf, input={"a": 1}, output=ref)
        assert sf.type == "subflow"
        assert sf.id == "sf"
        assert sf.input == {"a": 1}
        assert sf.output_target is ref


# ----- ApprovalGateNode -------------------------------------------------------


class TestApprovalGateNode:
    def test_construct_with_dict_request(self) -> None:
        g = ApprovalGateNode(
            id="g",
            when=True,
            request={"title": "approve?", "summary": "yes/no"},
        )
        assert g.type == "approval_gate"
        assert g.request.title == "approve?"
        assert g.on_deny == "fail"

    def test_camel_on_deny(self) -> None:
        g = ApprovalGateNode(
            id="g",
            request=ApprovalRequest(title="x"),
            onDeny="continue",
        )
        assert g.on_deny == "continue"

    def test_invalid_on_deny(self) -> None:
        with pytest.raises(ValidationError):
            ApprovalGateNode(
                id="g",
                request=ApprovalRequest(title="x"),
                on_deny="bogus",
            )


# ----- HumanTaskNode ----------------------------------------------------------


class TestHumanTaskNode:
    def test_construct(self) -> None:
        h = HumanTaskNode(
            id="h",
            prompt="please respond",
            outputSchema=_OutSchema,
            maxAttempts=5,
            timeoutMs=60_000,
        )
        assert h.type == "human_task"
        assert h.output_schema is _OutSchema
        assert h.max_attempts == 5
        assert h.timeout_ms == 60_000

    def test_default_timeout(self) -> None:
        h = HumanTaskNode(id="h", prompt="hi", outputSchema=_OutSchema)
        # default = 24h in ms
        assert h.timeout_ms == 24 * 60 * 60 * 1000


# ----- Composition smoke test -------------------------------------------------


class TestComposition:
    def test_full_workflow_construction(self) -> None:
        ref = OutputRef(name="r", schema=_OutSchema)
        wf = WorkflowNode(
            name="composed",
            children=[
                SequenceNode(
                    children=[
                        TaskNode(id="t1", output=ref, render=lambda: {"value": "a"}),
                        ParallelNode(
                            max_concurrency=2,
                            children=[
                                TaskNode(id="t2", output=ref, render=lambda: {"value": "b"}),
                                TaskNode(id="t3", output=ref, render=lambda: {"value": "c"}),
                            ],
                        ),
                        ApprovalGateNode(
                            id="g1",
                            request=ApprovalRequest(title="ok?"),
                        ),
                    ]
                )
            ],
        )
        # JSON round-trip preserves type discriminators all the way down.
        payload = wf.model_dump(mode="json")
        assert payload["type"] == "workflow"
        types = [c["type"] for c in payload["children"][0]["children"]]
        assert types == ["task", "parallel", "approval_gate"]

    def test_serialization_excludes_schema_classes(self) -> None:
        # JSON dump succeeds because OutputRef.schema_ is excluded — class
        # references aren't JSON-representable. The schema_version literal
        # on the validated payload (not the schema *class*) is what
        # travels with the persisted row.
        ref = OutputRef(name="r", schema=_OutSchema)
        wf = WorkflowNode(
            name="rt",
            children=[
                SequenceNode(
                    children=[
                        TaskNode(id="t1", output=ref, render=lambda: {"value": "a"}),
                    ]
                )
            ],
        )
        text = wf.model_dump_json()
        payload = json.loads(text)
        out_target = payload["children"][0]["children"][0]["output_target"]
        assert out_target == {"name": "r"}
        task_payload = payload["children"][0]["children"][0]
        assert "render" not in task_payload
        assert "agent" not in task_payload
