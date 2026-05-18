/** @jsxImportSource smithers-orchestrator */
import {
  ApprovalGate,
  Branch,
  Loop,
  Subflow,
} from "@smithers-orchestrator/components";
import { createSmithers } from "smithers-orchestrator";

import childWorkflow from "./child-workflow.tsx";
import {
  approvalSchema,
  childOutSchema,
  finalOutSchema,
  stepOutSchema,
  wireInputSchema,
} from "./schemas.ts";

const { Workflow, Task, Sequence, Parallel, smithers, outputs } = createSmithers(
  {
    input: wireInputSchema,
    seq1: stepOutSchema,
    seq2: stepOutSchema,
    par1: stepOutSchema,
    par2: stepOutSchema,
    par3: stepOutSchema,
    branch_step: stepOutSchema,
    loop_step: stepOutSchema,
    child_out: childOutSchema,
    approval: approvalSchema,
    output: finalOutSchema,
  },
  { dbPath: process.env.WIRE_COMPAT_DB ?? "wire_compat.db" },
);

export default smithers((ctx) => (
  <Workflow name="wire-compat">
    <Sequence>
      <Task id="seq-1" output={outputs.seq1}>
        {{
          schema_version: "wire-compat-step-v0" as const,
          step: "seq-1",
          value: 100,
        }}
      </Task>

      <Task id="seq-2" output={outputs.seq2}>
        {{
          schema_version: "wire-compat-step-v0" as const,
          step: "seq-2",
          value: 200,
        }}
      </Task>

      <Parallel maxConcurrency={4}>
        <Task id="par-1" output={outputs.par1}>
          {{
            schema_version: "wire-compat-step-v0" as const,
            step: "par-1",
            value: 10,
          }}
        </Task>
        <Task id="par-2" output={outputs.par2}>
          {{
            schema_version: "wire-compat-step-v0" as const,
            step: "par-2",
            value: 20,
          }}
        </Task>
        <Task id="par-3" output={outputs.par3}>
          {{
            schema_version: "wire-compat-step-v0" as const,
            step: "par-3",
            value: 30,
          }}
        </Task>
      </Parallel>

      <Branch
        if={ctx.input.branch}
        then={
          <Task id="branch-then" output={outputs.branch_step}>
            {{
              schema_version: "wire-compat-step-v0" as const,
              step: "branch-then",
              value: 1,
            }}
          </Task>
        }
        else={
          <Task id="branch-else" output={outputs.branch_step}>
            {{
              schema_version: "wire-compat-step-v0" as const,
              step: "branch-else",
              value: 0,
            }}
          </Task>
        }
      />

      <Loop
        id="loop"
        maxIterations={ctx.input.iterations}
        until={false}
        onMaxReached="return-last"
      >
        <Task id="loop-step" output={outputs.loop_step}>
          {{
            schema_version: "wire-compat-step-v0" as const,
            step: "loop-tick",
            value: 1,
          }}
        </Task>
      </Loop>

      <ApprovalGate
        id="gate"
        output={outputs.approval}
        when={false}
        request={{
          title: "auto-pass",
          summary: "Snapshot stays deterministic by never firing the gate.",
        }}
        onDeny="continue"
      />

      <Subflow
        id="sub"
        output={outputs.child_out}
        workflow={childWorkflow as any}
        input={{
          workload: ctx.input.workload,
          branch: true,
          iterations: 1,
        }}
      />

      <Task id="final" output={outputs.output}>
        {{
          schema_version: "wire-compat-final-v0" as const,
          workload: ctx.input.workload,
          sequence_total: 300,
          parallel_total: 60,
          branch_step: ctx.input.branch ? "branch-then" : "branch-else",
          loop_iterations: ctx.input.iterations,
        }}
      </Task>
    </Sequence>
  </Workflow>
));
