/** @jsxImportSource smithers-orchestrator */
// Cory-pattern subsystem port: ONE big Task per subsystem, agent owns
// reads/writes via tools. Cross-file awareness comes from the agent
// reading what it just wrote, not from upfront spec coordination.
//
// Matches bun-port-smithers' pattern: ClaudeCodeAgent with Read/Write/
// Edit/Bash tools rooted at the fork repo. Agent iterates until
// `python -c "from smithers_py.<subsystem> import *"` and pytest both
// pass, then returns a JSON manifest.
//
// Trade-off vs `port-subsystem.tsx`:
//   - More expensive (CLI agent tool loop, $0.50-1.50/subsystem vs
//     $0.20 single-shot)
//   - Slower (sequential tool calls)
//   - But: no cross-file naming drift, output is verifiable in place,
//     agent self-corrects on test failure
import { createSmithers } from "smithers-orchestrator";

import { agentsFor } from "../components/agents.ts";
import { estimateCostMicrocents, readActualTokenUsage } from "../components/sync-rules.ts";
import {
  subsystemPortFinalSchema,
  subsystemPortInputSchema,
} from "../components/schemas.ts";
import PortSubsystemCliPrompt from "../prompts/port-subsystem-cli.mdx";


const { Workflow, Task, Sequence, smithers, outputs } = createSmithers(
  {
    input: subsystemPortInputSchema,
    output: subsystemPortFinalSchema,
  },
  { dbPath: process.env.SMITHERS_PORT_SYNC_DB ?? "smithers.db" },
);


export default smithers((ctx) => {
  const agents = agentsFor({ forkRepoPath: ctx.input.forkRepoPath });

  return (
    <Workflow name="port-subsystem-cli">
      <Sequence>
        <Task
          id="port:cli"
          output={outputs.output}
          agent={agents.translator}
          timeoutMs={20 * 60_000}
          retries={1}
        >
          <PortSubsystemCliPrompt
            subsystemName={ctx.input.subsystemName}
            pythonTargetDir={ctx.input.pythonTargetDir}
            spec={ctx.input.spec}
            files={ctx.input.files}
            forkRepoPath={ctx.input.forkRepoPath}
            schema={subsystemPortFinalSchema}
          />
        </Task>
      </Sequence>
    </Workflow>
  );
});
