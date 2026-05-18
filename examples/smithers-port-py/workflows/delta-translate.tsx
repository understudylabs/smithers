/** @jsxImportSource smithers-orchestrator */
import { createSmithers } from "smithers-orchestrator";
import { z } from "zod";

import { agentsFor } from "../components/agents.ts";
import { estimateCostMicrocents, readActualTokenUsage, stableNodeId } from "../components/sync-rules.ts";
import { fetchPrDiff, readTargetFile } from "../components/upstream-watch.ts";
import {
  classificationSummarySchema,
  translationRowSchema,
  translationSummarySchema,
  upstreamPrSchema,
} from "../components/schemas.ts";
import TranslateDeltaPrompt from "../prompts/translate-delta.mdx";

const inputSchema = z.object({
  forkRepoPath: z.string(),
  upstreamRepo: z.string().default(""),
  classifications: classificationSummarySchema,
  upstreamPrs: z.array(upstreamPrSchema).default([]),
  maxConcurrency: z.number().int().min(1).max(16),
});

const DIFF_MAX_CHARS = 24_000;

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
  const upstreamByNumber = new Map(
    ctx.input.upstreamPrs.map((p) => [p.number, p] as const),
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
          {portable.map((row) => {
            const upstream = upstreamByNumber.get(row.prNumber);
            const diff = ctx.input.upstreamRepo
              ? fetchPrDiff({
                  repo: ctx.input.upstreamRepo,
                  number: row.prNumber,
                  maxChars: DIFF_MAX_CHARS,
                })
              : "(no upstreamRepo provided — diff unavailable)";
            const targetPath = row.pythonTarget || `smithers_py/runtime/pr_${row.prNumber}.py`;
            const target = readTargetFile({
              forkRepoPath: ctx.input.forkRepoPath,
              relativePath: targetPath,
            });
            return (
              <Task
                key={String(row.prNumber)}
                id={`translate:${stableNodeId(String(row.prNumber))}`}
                output={outputs.translation}
                agent={agents.translator}
                timeoutMs={20 * 60_000}
              >
                <TranslateDeltaPrompt
                  prNumber={String(row.prNumber)}
                  prTitle={upstream?.title ?? "(unknown)"}
                  prUrl={upstream?.htmlUrl ?? ""}
                  pythonTarget={targetPath}
                  targetExists={target.exists ? "yes" : "no"}
                  targetContent={target.exists ? target.content : "(file does not exist yet — emit new)"}
                  action={row.action}
                  prDiff={diff}
                  diffMaxChars={String(DIFF_MAX_CHARS)}
                  schema={translationRowSchema}
                />
              </Task>
            );
          })}
        </Parallel>

        {allDone ? (
          <Task id="translate:summary" output={outputs.output}>
            {() => {
              const drafted = rows.filter((r) => r.status === "drafted").length;
              const failed = rows.filter((r) => r.status === "failed").length;
              const skipped = rows.filter((r) => r.status === "skipped").length;
              // Real token usage from engine-recorded events — strictly
              // more accurate than the model's self-reported tokensUsed.
              const actual = readActualTokenUsage({
                dbPath: process.env.SMITHERS_PORT_SYNC_DB ?? "smithers.db",
                runIdPrefix: ctx.runId,
                nodeIdPrefix: "translate:",
              });
              return {
                schema_version: "smithers-port-sync-translate-summary-v0" as const,
                rows,
                metrics: {
                  drafted,
                  failed,
                  skipped,
                  totalTokensIn: actual.tokensIn,
                  totalTokensOut: actual.tokensOut,
                  estimatedCostUsdMicrocents: estimateCostMicrocents({
                    tokensIn: actual.tokensIn,
                    tokensOut: actual.tokensOut,
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
