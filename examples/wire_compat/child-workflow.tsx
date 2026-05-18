/** @jsxImportSource smithers-orchestrator */
import { createSmithers } from "smithers-orchestrator";
import { z } from "zod";

import {
  childOutSchema,
  wireInputSchema,
} from "./schemas.ts";

const { Workflow, Task, Sequence, smithers, outputs } = createSmithers(
  {
    input: wireInputSchema,
    output: childOutSchema,
  },
  { dbPath: process.env.WIRE_COMPAT_DB ?? "wire_compat.db" },
);

export default smithers((ctx) => (
  <Workflow name="wire-compat-child">
    <Sequence>
      <Task id="child-emit" output={outputs.output}>
        {{
          schema_version: "wire-compat-child-v0" as const,
          label: `child-of-${ctx.input.workload}`,
          payload: ["alpha", "beta", "gamma"],
        }}
      </Task>
    </Sequence>
  </Workflow>
));
