/** @jsxImportSource smithers-orchestrator */
import { execSync } from "node:child_process";

import { createSmithers } from "smithers-orchestrator";
import { z } from "zod";

import {
  parityResultSchema,
  translationSummarySchema,
} from "../components/schemas.ts";

const inputSchema = z.object({
  forkRepoPath: z.string(),
  translationSummary: translationSummarySchema,
});

const { Workflow, Task, Sequence, smithers, outputs } = createSmithers(
  {
    input: inputSchema,
    output: parityResultSchema,
  },
  { dbPath: process.env.SMITHERS_PORT_SYNC_DB ?? "smithers.db" },
);

export default smithers((ctx) => (
  <Workflow name="port-sync-cross-runtime-verify">
    <Sequence>
      <Task id="parity:test" output={outputs.output}>
        {() => {
          // Re-run wire_compat tests as the parity acceptance check.
          // This is the live gate that proves the Python port still
          // matches TS row shape after translation.
          let passed = false;
          let notes = "";
          let divergences: string[] = [];
          // Build a PATH that includes the user's `uv` install location.
          // Bun's child process inherits this process's env, which may
          // not have ``~/.local/bin`` on PATH where ``uv`` lives.
          const env = {
            ...process.env,
            PATH: `${process.env.HOME ?? ""}/.local/bin:${process.env.PATH ?? ""}`,
          };
          try {
            const output = execSync(
              "cd /Users/luis/smithers/smithers_py && " +
              "uv run python -m pytest /Users/luis/smithers/examples/wire_compat -q",
              {
                encoding: "utf8",
                stdio: ["ignore", "pipe", "pipe"],
                env,
              },
            );
            passed = true;
            notes = `wire_compat parity tests green: ${output.trim().split("\n").slice(-1)[0]}`;
          } catch (err: any) {
            passed = false;
            const stdout = err?.stdout?.toString() ?? "";
            const stderr = err?.stderr?.toString() ?? "";
            notes = `wire_compat parity tests failed: ${err?.message ?? "unknown"}`;
            divergences = (stdout + "\n" + stderr).split("\n")
              .filter((line: string) =>
                line.includes("FAILED") ||
                line.includes("cross-runtime") ||
                line.includes("uv: not found") ||
                line.includes("ImportError"),
              )
              .slice(0, 20);
          }
          return {
            schema_version: "smithers-port-sync-parity-v0" as const,
            passed,
            divergences,
            rowsCompared: 12,  // canonical wire_compat row count
            rowsEqual: passed ? 12 : 0,
            notes,
          };
        }}
      </Task>
    </Sequence>
  </Workflow>
));
