/** @jsxImportSource smithers-orchestrator */
import { createSmithers } from "smithers-orchestrator";
import { z } from "zod";

import { agentsFor } from "../components/agents.ts";
import {
  classifyCacheKey,
  staticClassification,
  stableNodeId,
} from "../components/sync-rules.ts";
import { listPythonSourceTree } from "../components/upstream-watch.ts";
import {
  classificationSummarySchema,
  deltaClassificationSchema,
  upstreamPrSchema,
} from "../components/schemas.ts";
import ClassifyDeltaPrompt from "../prompts/classify-delta.mdx";

const inputSchema = z.object({
  upstreamRepo: z.string(),
  forkRepoPath: z.string(),
  prs: z.array(upstreamPrSchema),
  maxConcurrency: z.number().int().min(1).max(16),
  rubricRev: z.string().default("v0.1.0"),
});

const { Workflow, Task, Sequence, Parallel, smithers, outputs } = createSmithers(
  {
    input: inputSchema,
    classification: deltaClassificationSchema,
    output: classificationSummarySchema,
  },
  { dbPath: process.env.SMITHERS_PORT_SYNC_DB ?? "smithers.db" },
);

export default smithers((ctx) => {
  const agents = agentsFor({ forkRepoPath: ctx.input.forkRepoPath });
  // List the existing Python tree so the classifier can suggest a
  // real target file path instead of a per-PR scratch file.
  const pythonTree = listPythonSourceTree({
    forkRepoPath: ctx.input.forkRepoPath,
    maxFiles: 200,
  });

  // Short-circuit obvious cases with deterministic rules.
  const llmPrs: any[] = [];
  const staticRows: any[] = [];
  for (const pr of ctx.input.prs) {
    const sc = staticClassification({ title: pr.title, filesChanged: pr.filesChanged });
    if (sc) {
      staticRows.push({
        schema_version: "smithers-port-sync-classify-v0" as const,
        prNumber: pr.number,
        action: sc.action,
        pythonTarget: "",
        rationale: `static rule: ${sc.rationale}`,
        confidence: 95,
        needsHumanReview: false,
        estimatedTokens: 0,
      });
    } else {
      llmPrs.push(pr);
    }
  }

  const llmRows = llmPrs
    .map((pr) =>
      ctx.outputMaybe(outputs.classification, {
        nodeId: `classify:${stableNodeId(String(pr.number))}`,
      }),
    )
    .filter((row): row is any => Boolean(row));

  const allDone = llmRows.length >= llmPrs.length;

  return (
    <Workflow name="port-sync-delta-classify">
      <Sequence>
        <Parallel maxConcurrency={ctx.input.maxConcurrency}>
          {llmPrs.map((pr) => {
            const cacheKey = classifyCacheKey({
              upstreamRepo: ctx.input.upstreamRepo,
              prNumber: pr.number,
              rubricRev: ctx.input.rubricRev,
            });
            return (
              <Task
                key={String(pr.number)}
                id={`classify:${stableNodeId(String(pr.number))}`}
                output={outputs.classification}
                agent={agents.classifier}
                timeoutMs={5 * 60_000}
                cache={{ by: () => cacheKey, version: "v1" }}
              >
                <ClassifyDeltaPrompt
                  prNumber={pr.number}
                  prTitle={pr.title}
                  prAuthor={pr.author}
                  prUrl={pr.htmlUrl}
                  filesChanged={pr.filesChanged}
                  labels={pr.labels}
                  pythonTree={pythonTree}
                  schema={deltaClassificationSchema}
                />
              </Task>
            );
          })}
        </Parallel>

        {allDone ? (
          <Task id="classify:summary" output={outputs.output}>
            {() => {
              const rows = [...staticRows, ...llmRows];
              const port = rows.filter((r) => r.action === "port").length;
              const repl = rows.filter((r) => r.action === "port-with-replacement").length;
              const skipV0 = rows.filter((r) => r.action === "skip-v0").length;
              const skipForever = rows.filter((r) => r.action === "skip-forever").length;
              const ported = rows.filter((r) => r.action === "already-ported").length;
              const avgConfidence = rows.length === 0
                ? 0
                : Math.round(rows.reduce((s, r) => s + r.confidence, 0) / rows.length);
              const rejections = rows.filter((r) => r.needsHumanReview).length;
              const rejectionRate = rows.length === 0
                ? 0
                : Math.round((rejections / rows.length) * 100);
              return {
                schema_version: "smithers-port-sync-classify-summary-v0" as const,
                rows,
                metrics: {
                  portCount: port,
                  portWithReplacementCount: repl,
                  skipV0Count: skipV0,
                  skipForeverCount: skipForever,
                  alreadyPortedCount: ported,
                  avgConfidence,
                  rejectionRate,
                },
              };
            }}
          </Task>
        ) : null}
      </Sequence>
    </Workflow>
  );
});
