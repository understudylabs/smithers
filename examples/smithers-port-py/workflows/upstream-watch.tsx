/** @jsxImportSource smithers-orchestrator */
import { createSmithers } from "smithers-orchestrator";
import { z } from "zod";

import { fetchRecentPrs } from "../components/upstream-watch.ts";
import {
  upstreamWatchResultSchema,
} from "../components/schemas.ts";

const inputSchema = z.object({
  upstreamRepo: z.string(),
  upstreamBranch: z.string(),
  sinceIso: z.string().default(""),
  prsToProcess: z.array(z.number().int()).default([]),
});

const { Workflow, Task, Sequence, smithers, outputs } = createSmithers(
  {
    input: inputSchema,
    output: upstreamWatchResultSchema,
  },
  { dbPath: process.env.SMITHERS_PORT_SYNC_DB ?? "smithers.db" },
);

export default smithers((ctx) => (
  <Workflow name="port-sync-upstream-watch">
    <Sequence>
      <Task id="upstream:fetch" output={outputs.output}>
        {() => {
          // Either honor an explicit prsToProcess override or scan
          // upstream for recently-merged PRs.
          const prs = ctx.input.prsToProcess.length > 0
            ? ctx.input.prsToProcess.map((n) => ({
                number: n,
                title: `(override) PR #${n}`,
                author: "",
                mergedAt: "",
                htmlUrl: "",
                filesChanged: [] as string[],
                labels: [] as string[],
              }))
            : fetchRecentPrs({ repo: ctx.input.upstreamRepo, sinceIso: ctx.input.sinceIso });
          const docs = prs.filter((p) => /docs|readme/i.test(p.title)).length;
          const gateway = prs.filter((p) =>
            p.filesChanged.some((f) =>
              /^packages\/(gateway|server|sandbox|openapi|devtools)/i.test(f),
            ),
          ).length;
          return {
            schema_version: "smithers-port-sync-upstream-watch-v0" as const,
            sinceIso: ctx.input.sinceIso,
            upstreamHead: "",
            prs,
            metrics: {
              totalPrs: prs.length,
              docsOnlyPrs: docs,
              gatewayOnlyPrs: gateway,
              runtimePrs: Math.max(0, prs.length - docs - gateway),
            },
          };
        }}
      </Task>
    </Sequence>
  </Workflow>
));
