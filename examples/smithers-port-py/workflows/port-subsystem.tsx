/** @jsxImportSource smithers-orchestrator */
// Sister meta-workflow: port a whole subsystem from a markdown spec.
//
// While `workflow.tsx` ports merged upstream PRs (diff against existing
// Python), this workflow ports brand-new *subsystems* the Python port
// doesn't have yet (memory, scorers, tools, serve, ...). One Parallel
// fan-out task per file in the subsystem; each is fed the same spec
// plus the file's role/hints and asked to emit complete Python source.
//
// The output is a `subsystemPortFinalSchema` row plus one
// `subsystemFileTranslationSchema` row per file. Apply-to-disk is
// opt-in via `applyToDisk: true` in the input fixture so the workflow
// can dry-run safely.
import { createSmithers } from "smithers-orchestrator";

import { agentsFor } from "../components/agents.ts";
import { readActualTokenUsage, estimateCostMicrocents, stableNodeId } from "../components/sync-rules.ts";
import {
  subsystemFileTranslationSchema,
  subsystemPortFinalSchema,
  subsystemPortInputSchema,
} from "../components/schemas.ts";
import PortSubsystemFilePrompt from "../prompts/port-subsystem-file.mdx";

import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";


const { Workflow, Task, Sequence, Parallel, smithers, outputs } = createSmithers(
  {
    input: subsystemPortInputSchema,
    file: subsystemFileTranslationSchema,
    output: subsystemPortFinalSchema,
  },
  { dbPath: process.env.SMITHERS_PORT_SYNC_DB ?? "smithers.db" },
);


export default smithers((ctx) => {
  const agents = agentsFor({ forkRepoPath: ctx.input.forkRepoPath });
  const files = ctx.input.files;

  // Read each per-file translation result; presence drives the fan-in
  // task that writes to disk + emits the summary.
  const rows = files
    .map((f) =>
      ctx.outputMaybe(outputs.file, {
        nodeId: `port:${stableNodeId(f.path)}`,
      }),
    )
    .filter((row): row is any => Boolean(row));
  const allDone = rows.length >= files.length;

  return (
    <Workflow name="port-subsystem">
      <Sequence>
        <Parallel maxConcurrency={Math.min(files.length, 4)}>
          {files.map((file) => (
            <Task
              key={file.path}
              id={`port:${stableNodeId(file.path)}`}
              output={outputs.file}
              agent={agents.translator}
              timeoutMs={20 * 60_000}
              retries={2}
            >
              <PortSubsystemFilePrompt
                subsystemName={ctx.input.subsystemName}
                path={file.path}
                role={file.role}
                hints={file.hints}
                spec={ctx.input.spec}
                upstreamReferenceDts={ctx.input.upstreamReferenceDts}
                schema={subsystemFileTranslationSchema}
              />
            </Task>
          ))}
        </Parallel>

        {allDone ? (
          <Task id="port:apply" output={outputs.output}>
            {() => {
              // Apply each file to disk (when applyToDisk=true).
              const applied: string[] = [];
              const baseDir = resolve(
                ctx.input.forkRepoPath,
                ctx.input.pythonTargetDir,
              );
              if (ctx.input.applyToDisk) {
                mkdirSync(baseDir, { recursive: true });
                for (const row of rows) {
                  const dest = resolve(baseDir, row.path);
                  if (!dest.startsWith(baseDir)) {
                    throw new Error(
                      `refusing to write outside target dir: ${dest}`,
                    );
                  }
                  mkdirSync(dirname(dest), { recursive: true });
                  writeFileSync(dest, row.content, "utf8");
                  applied.push(dest);
                }
              }

              // Real token usage from engine-recorded events.
              const usage = readActualTokenUsage({
                dbPath: process.env.SMITHERS_PORT_SYNC_DB ?? "smithers.db",
                runIdPrefix: ctx.runId,
                nodeIdPrefix: "port:",
              });
              const mode = process.env.SMITHERS_PORT_PY_AGENT_MODE ?? "anthropic";
              const cost = estimateCostMicrocents({
                tokensIn: usage.tokensIn,
                tokensOut: usage.tokensOut,
                modeOrModel: mode,
              });

              const totalLoc = rows.reduce(
                (s: number, r: any) => s + (r.loc ?? 0),
                0,
              );

              return {
                schema_version: "smithers-port-subsystem-final-v0" as const,
                subsystem: ctx.input.subsystemName,
                filesProduced: rows.map((r: any) => r.path),
                totalLoc,
                appliedPath: ctx.input.applyToDisk ? baseDir : "",
                tokensIn: usage.tokensIn,
                tokensOut: usage.tokensOut,
                estimatedSpendMicrocents: cost,
                summary:
                  `Ported ${ctx.input.subsystemName}: ${rows.length} files, ` +
                  `${totalLoc} LoC. ` +
                  (ctx.input.applyToDisk
                    ? `Wrote to ${baseDir}.`
                    : `Dry-run (not written).`),
              };
            }}
          </Task>
        ) : null}
      </Sequence>
    </Workflow>
  );
});
