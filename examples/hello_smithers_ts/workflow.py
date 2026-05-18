"""Minimal end-to-end demo for the TS-shape smithers_py runtime.

Run:

    smithers-ts up examples/hello_smithers_ts/workflow.py \\
        --input '{"name":"world"}' --db /tmp/demo.db
    smithers-ts approve <runId> --note "lgtm" --by "you" --db /tmp/demo.db
    smithers-ts up examples/hello_smithers_ts/workflow.py \\
        --run-id <runId> --resume --db /tmp/demo.db
    smithers-ts inspect <runId> --db /tmp/demo.db

You should see two output rows persist (one Task pre-gate, one Task
post-gate), the ApprovalGate pause in between, then a clean completion
after approval.
"""

from __future__ import annotations

from pydantic import BaseModel

from smithers_py import (
    ApprovalGateNode,
    ApprovalRequest,
    SequenceNode,
    TaskNode,
    WorkflowNode,
    create_smithers,
)


class HelloInput(BaseModel):
    name: str = "world"


class GreetOut(BaseModel):
    schema_version: str = "hello-greet-v0"
    greeting: str


class FinalOut(BaseModel):
    schema_version: str = "hello-final-v0"
    greeting: str
    approved_by: str
    note: str


CONFIG = create_smithers(
    schemas={
        "input": HelloInput,
        "greeting": GreetOut,
        "output": FinalOut,
    }
)
outputs = CONFIG.outputs


@CONFIG.workflow
def hello_workflow(ctx) -> WorkflowNode:
    return WorkflowNode(
        name="hello-smithers-ts",
        children=[
            SequenceNode(
                children=[
                    TaskNode(
                        id="greet",
                        output=outputs.greeting,
                        render=lambda: {"greeting": f"hello, {ctx.input.name}"},
                    ),
                    ApprovalGateNode(
                        id="approve",
                        when=True,
                        request=ApprovalRequest(
                            title=f"Approve greeting for {ctx.input.name}?",
                            summary="A trivial approval gate so you can see pause/resume work.",
                        ),
                        on_deny="fail",
                    ),
                    TaskNode(
                        id="final",
                        output=outputs.output,
                        render=lambda: {
                            "greeting": ctx.output("greet")["greeting"],
                            "approved_by": ctx.output("approve")["decided_by"],
                            "note": ctx.output("approve")["note"],
                        },
                    ),
                ]
            )
        ],
    )
