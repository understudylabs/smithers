"""TS-compatibility node types.

These node classes mirror the public component surface of TypeScript Smithers
on `main` (`Workflow`, `Sequence`, `Parallel`, `Task`, `Subflow`,
`ApprovalGate`, `HumanTask`) so workflows authored against the TS API can
run on the existing `smithers_py` engine without redesigning the tick loop.

Mapping to the engine:
    Workflow      → root container; children execute under the workflow name.
    Sequence      → children executed in order; engine waits for each child
                    to settle before moving on.
    Parallel      → children scheduled concurrently up to ``max_concurrency``.
    Task          → unit of work. ``agent`` is invoked or, for static tasks,
                    ``render`` returns the payload directly. Output validated
                    against ``output_schema`` and persisted with
                    ``output_target`` (Pydantic model or ``OutputRef``).
    Subflow       → child workflow invocation. Same SQLite DB; child run is
                    recorded under the parent's ``run_id``.
    ApprovalGate  → if ``when`` is True, the gate suspends the workflow until
                    ``smithers approve`` (or ``smithers deny``) resolves it.
    HumanTask     → always suspends; resolves via ``smithers human``.

The discriminated union in ``smithers_py.nodes`` is updated to include each of
these types. Engine support for the new ``type`` literals lives alongside the
existing handlers — the tick loop already dispatches on ``node.type``.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Literal, Optional, Type, Union
from pydantic import BaseModel, Field, model_validator

from .base import NodeBase


# ----- Output targets ---------------------------------------------------------


class OutputRef(BaseModel):
    """Typed handle returned by ``createSmithers`` for binding ``Task.output``.

    Mirrors the TS shape ``outputs.someKey`` where the key was declared in
    ``createSmithers({key: someSchema, ...})``. Carries the schema so the
    engine can validate Task return values against the declared contract.

    ``schema_`` (the Pydantic class itself) is excluded from JSON
    serialization because class objects are not JSON-representable; the
    persisted ``schema_version`` literal on the validated payload is what
    travels with the row.
    """

    name: str = Field(..., description="Output key as registered in createSmithers")
    schema_: Type[BaseModel] = Field(
        ...,
        description="Pydantic model the output payload must satisfy",
        alias="schema",
        exclude=True,
    )

    model_config = {
        "arbitrary_types_allowed": True,
        "extra": "forbid",
        "populate_by_name": True,
    }


# ----- Structural -------------------------------------------------------------


class WorkflowNode(NodeBase):
    """Root container for a TS-shaped workflow.

    Equivalent to ``<Workflow name="...">`` in TS. The ``name`` is recorded
    alongside the run for ``smithers ps``/``smithers inspect`` discovery.
    """

    type: Literal["workflow"] = "workflow"
    name: str = Field(..., description="Workflow name (visible to the CLI)")
    cache: bool = Field(default=False, description="Enable per-run caching")

    model_config = {
        "extra": "allow",
    }


class SequenceNode(NodeBase):
    """Ordered execution of children.

    The engine waits for each child to settle (finished or paused) before
    starting the next. Mirrors TS ``<Sequence>``.
    """

    type: Literal["sequence"] = "sequence"

    model_config = {
        "extra": "allow",
    }


class ParallelNode(NodeBase):
    """Concurrent execution of children.

    Children are scheduled together up to ``max_concurrency``. Mirrors TS
    ``<Parallel maxConcurrency={N}>``.
    """

    type: Literal["parallel"] = "parallel"
    max_concurrency: int = Field(
        default=8,
        ge=1,
        description="Maximum number of children running at once",
        alias="maxConcurrency",
    )

    model_config = {
        "extra": "allow",
        "populate_by_name": True,
    }


# ----- Task -------------------------------------------------------------------


class TaskNode(NodeBase):
    """Unit-of-work node with a typed output contract.

    Mirrors TS ``<Task id="..." output={outputs.X} agent={...}>...</Task>``.

    - If ``agent`` is set the engine routes through the agent runtime (Claude,
      OpenAI, etc.), passing ``prompt`` and validating the return against
      ``output_schema``.
    - If ``render`` is set (no agent) the engine invokes the callable
      directly. This is the "deterministic Task" pattern used in the
      bun-port-smithers example for compute-only steps.

    ``output_target`` carries the registered schema so the persisted row is
    addressable as ``ctx.output(name)`` from downstream nodes.
    """

    type: Literal["task"] = "task"
    id: str = Field(..., description="Stable node identifier within the workflow")
    output_target: Optional[OutputRef] = Field(
        default=None,
        description="Output binding registered via createSmithers",
        alias="output",
    )
    output_schema: Optional[Type[BaseModel]] = Field(
        default=None,
        exclude=True,
        description="Inline output schema when no OutputRef is supplied",
    )
    agent: Optional[Any] = Field(
        default=None,
        exclude=True,
        description="Agent instance (ClaudeNode-compatible) or None for static tasks",
    )
    prompt: Optional[str] = Field(
        default=None,
        description="Prompt text passed to the agent (when ``agent`` is set)",
    )
    render: Optional[Callable[[], Any]] = Field(
        default=None,
        exclude=True,
        description="Static compute function for agent-less tasks",
    )
    timeout_ms: Optional[int] = Field(
        default=None,
        description="Per-attempt timeout in milliseconds",
        alias="timeoutMs",
    )
    max_attempts: int = Field(
        default=1,
        ge=1,
        description="Max attempts before giving up",
        alias="maxAttempts",
    )
    cache: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Cache policy: { by: callable, version: str }",
    )
    scorers: Optional[List[Any]] = Field(
        default=None,
        exclude=True,
        description="Smithers scorers attached to this task",
    )
    depends_on: Optional[List[str]] = Field(
        default=None,
        description="Explicit dependencies on other node ids",
        alias="dependsOn",
    )

    model_config = {
        "extra": "allow",
        "arbitrary_types_allowed": True,
        "populate_by_name": True,
    }

    @model_validator(mode="after")
    def _check_payload_source(self) -> "TaskNode":
        if self.agent is None and self.render is None and not self.children:
            raise ValueError(
                "TaskNode requires one of: agent (+prompt), render callable, "
                "or static children payload"
            )
        if self.output_target is None and self.output_schema is None:
            raise ValueError(
                "TaskNode requires output (OutputRef) or output_schema"
            )
        return self


# ----- Subflow ----------------------------------------------------------------


class SubflowNode(NodeBase):
    """Invoke a child workflow as a subtree of this run.

    Mirrors TS ``<Subflow id="..." workflow={...} output={outputs.Y} input={...}>``.
    The child workflow runs under its own ``run_id`` prefixed by the parent's,
    sharing the SQLite DB. Its terminal output becomes addressable as
    ``ctx.output(<subflow_id>)`` to downstream nodes.
    """

    type: Literal["subflow"] = "subflow"
    id: str = Field(..., description="Stable subflow node identifier")
    workflow: Any = Field(
        ...,
        exclude=True,
        description="Child workflow callable (a function returning a node tree)",
    )
    input: Dict[str, Any] = Field(
        default_factory=dict,
        description="Input payload passed to the child workflow",
    )
    output_target: Optional[OutputRef] = Field(
        default=None,
        description="OutputRef bound to the child's terminal output",
        alias="output",
    )
    max_attempts: int = Field(
        default=1,
        ge=1,
        alias="maxAttempts",
    )
    timeout_ms: Optional[int] = Field(default=None, alias="timeoutMs")

    model_config = {
        "extra": "allow",
        "arbitrary_types_allowed": True,
        "populate_by_name": True,
    }


# ----- Human-in-the-loop ------------------------------------------------------


class ApprovalRequest(BaseModel):
    """Structured request payload presented to the operator when a gate fires."""

    title: str
    summary: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class ApprovalGateNode(NodeBase):
    """Conditional pause-for-approval node.

    Mirrors TS ``<ApprovalGate when={...} request={...} onDeny="fail|continue">``.
    When ``when`` is True at render time, the engine writes a pending row
    to ``approvals`` and suspends the run. ``smithers approve`` or
    ``smithers deny`` resolves the gate; ``on_deny`` dictates whether
    denial fails the run or merely continues without promotion.
    """

    type: Literal["approval_gate"] = "approval_gate"
    id: str = Field(..., description="Stable gate node identifier")
    when: bool = Field(default=True, description="Fire only when this is True")
    request: ApprovalRequest = Field(
        ...,
        description="Operator-facing request body",
    )
    on_deny: Literal["fail", "continue"] = Field(
        default="fail",
        description="Behavior when the gate is denied",
        alias="onDeny",
    )
    output_target: Optional[OutputRef] = Field(
        default=None,
        description="OutputRef for the resolved approval row",
        alias="output",
    )

    model_config = {
        "extra": "allow",
        "populate_by_name": True,
    }


class HumanTaskNode(NodeBase):
    """Always-blocking human-input node.

    Mirrors TS ``<HumanTask outputSchema={...} prompt={...} maxAttempts={...}>``.
    Unlike ``ApprovalGate``, this always suspends — used for collecting
    structured operator input (e.g., a plan, a label, a rubric) rather than
    a yes/no decision.
    """

    type: Literal["human_task"] = "human_task"
    id: str = Field(..., description="Stable human-task node identifier")
    prompt: Any = Field(
        ...,
        description="Prompt body (string or MDX-rendered component output)",
    )
    output_target: Optional[OutputRef] = Field(
        default=None,
        description="OutputRef bound to the human's submitted payload",
        alias="output",
    )
    output_schema: Optional[Type[BaseModel]] = Field(
        default=None,
        exclude=True,
        description="Inline Pydantic schema for the response when no OutputRef set",
        alias="outputSchema",
    )
    max_attempts: int = Field(
        default=3,
        ge=1,
        alias="maxAttempts",
    )
    timeout_ms: int = Field(
        default=24 * 60 * 60 * 1000,
        description="How long to wait before timing out (default 24h)",
        alias="timeoutMs",
    )

    model_config = {
        "extra": "allow",
        "arbitrary_types_allowed": True,
        "populate_by_name": True,
    }


# ----- Workspace isolation ----------------------------------------------------


class WorktreeNode(NodeBase):
    """Isolated working tree for subagent edits.

    Mirrors TS ``<Worktree path={...} branch={...} baseBranch={...}>``. The
    engine materializes a git worktree (or jj workspace) at ``path`` rooted
    at ``base_branch`` and runs child tasks inside it. Used by bun-port to
    isolate per-subsystem fixes before serializing them through a merge
    queue.
    """

    type: Literal["worktree"] = "worktree"
    id: Optional[str] = Field(default=None, description="Stable worktree node id")
    path: str = Field(..., description="Filesystem path for the worktree")
    branch: Optional[str] = Field(default=None, description="Working branch name")
    base_branch: str = Field(
        default="main",
        description="Branch to root the worktree at",
        alias="baseBranch",
    )
    skip_if: bool = Field(
        default=False,
        description="When True, do not create the worktree and pass children through",
        alias="skipIf",
    )

    model_config = {
        "extra": "allow",
        "populate_by_name": True,
    }


class MergeQueueNode(NodeBase):
    """Serialized merge queue.

    Mirrors TS ``<MergeQueue maxConcurrency={1} requireGreen={true}>``. Wraps
    a set of worktree-emitted branches and merges them back to the base
    branch one at a time, with optional gate (e.g., require tests green
    before merge).
    """

    type: Literal["merge_queue"] = "merge_queue"
    id: Optional[str] = Field(default=None, description="Stable queue node id")
    base_branch: str = Field(
        default="main",
        description="Branch to merge into",
        alias="baseBranch",
    )
    max_concurrency: int = Field(
        default=1,
        ge=1,
        description="How many merges to serialize at once (default 1 = strict serial)",
        alias="maxConcurrency",
    )
    require_green: bool = Field(
        default=True,
        description="Require child branches to report green before merging",
        alias="requireGreen",
    )

    model_config = {
        "extra": "allow",
        "populate_by_name": True,
    }


__all__ = [
    "OutputRef",
    "ApprovalRequest",
    "WorkflowNode",
    "SequenceNode",
    "ParallelNode",
    "TaskNode",
    "SubflowNode",
    "ApprovalGateNode",
    "HumanTaskNode",
    "WorktreeNode",
    "MergeQueueNode",
]
