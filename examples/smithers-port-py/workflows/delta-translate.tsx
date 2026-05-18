/** @jsxImportSource smithers-orchestrator */
import { createSmithers } from "smithers-orchestrator";
import { z } from "zod";

import { agentsFor } from "../components/agents.ts";
import { estimateCostMicrocents, stableNodeId } from "../components/sync-rules.ts";
import {
  classificationSummarySchema,
  translationRowSchema,
  translationSummarySchema,
} from "../components/schemas.ts";
import TranslateDeltaPrompt from "../prompts/translate-delta.mdx";

const inputSchema = z.object({
  forkRepoPath: z.string(),
  classifications: classificationSummarySchema,
  maxConcurrency: z.number().int().min(1).max(16),
});

const { Workflow, Task, Sequence, Parallel, smithers, outputs } = createSmithers(
  {
    input: inputSchema,
    translation: translationRowSchema,
    output: translationSummarySchema,
  },
  { dbPath: process.env.SMITHERS_PORT_SYNC_DB ?? "smithers.db" },
);

export default smithers((ctx) => {
  const agents = agentsFor({ forkRepoPath: ctx.input.forkRepoPath });
  const portable = ctx.input.classifications.rows.filter(
    (r) => r.action === "port" || r.action === "port-with-replacement",
  );

  const rows = portable
    .map((row) =>
      ctx.outputMaybe(outputs.translation, {
        nodeId: `translate:${stableNodeId(String(row.prNumber))}`,
      }),
    )
    .filter((row): row is any => Boolean(row));

  const allDone = rows.length >= portable.length;

  return (
    <Workflow name="port-sync-delta-translate">
      <Sequence>
        <Parallel maxConcurrency={ctx.input.maxConcurrency}>
          {portable.map((row) => (
            <Task
              key={String(row.prNumber)}
              id={`translate:${stableNodeId(String(row.prNumber))}`}
              output={outputs.translation}
              agent={agents.translator}
              timeoutMs={20 * 60_000}
            >
              <TranslateDeltaPrompt
                prNumber={String(row.prNumber)}
                prTitle={"(see classification rationale)"}
                prUrl=""
                pythonTarget={row.pythonTarget || `smithers_py/runtime/pr_${row.prNumber}.py`}
                action={row.action}
                schema={translationRowSchema}
              />
            </Task>
          ))}
        </Parallel>

        {allDone ? (
          <Task id="translate:summary" output={outputs.output}>
            {() => {
              const drafted = rows.filter((r) => r.status === "drafted").length;
              const failed = rows.filter((r) => r.status === "failed").length;
              const skipped = rows.filter((r) => r.status === "skipped").length;
              const totalIn = rows.reduce((s, r) => s + (r.tokensUsed ?? 0) / 2, 0);
              const totalOut = rows.reduce((s, r) => s + (r.tokensUsed ?? 0) / 2, 0);
              return {
                schema_version: "smithers-port-sync-translate-summary-v0" as const,
                rows,
                metrics: {
                  drafted,
                  failed,
                  skipped,
                  totalTokensIn: Math.round(totalIn),
                  totalTokensOut: Math.round(totalOut),
                  estimatedCostUsdMicrocents: estimateCostMicrocents({
                    tokensIn: Math.round(totalIn),
                    tokensOut: Math.round(totalOut),
                  }),
                },
              };
            }}
          </Task>
        ) : null}
      </Sequence>
    </Workflow>
  );
});
