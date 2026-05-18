// Deterministic helpers — no LLM. The methodology of the sync workflow
// lives here.

import { createHash } from "node:crypto";
import { Database } from "bun:sqlite";


export function stableNodeId(text: string): string {
  return text.replace(/[^a-zA-Z0-9_]/g, "_").slice(-48);
}


export function classifyCacheKey(args: {
  upstreamRepo: string;
  prNumber: number;
  rubricRev: string;
}): string {
  const h = createHash("sha256");
  h.update(args.upstreamRepo);
  h.update("|");
  h.update(String(args.prNumber));
  h.update("|");
  h.update(args.rubricRev);
  return h.digest("hex").slice(0, 16);
}


/**
 * Static-rule heuristic: which PRs we can short-circuit without an LLM call.
 * Saves the per-PR classification cost for the obvious cases.
 *
 * Returns null when the rules are ambiguous; caller should route through
 * the LLM classifier.
 */
export function staticClassification(pr: {
  title: string;
  filesChanged: string[];
}): { action: "skip-forever" | "skip-v0" | "already-ported"; rationale: string } | null {
  const title = pr.title.toLowerCase();
  const files = pr.filesChanged.map((f) => f.toLowerCase());

  if (/^docs|fix doc|readme|markdown|spelling/i.test(pr.title)) {
    return { action: "skip-forever", rationale: "docs-only change" };
  }
  if (files.length > 0 && files.every((f) => f.endsWith(".md") || f.endsWith(".mdx"))) {
    return { action: "skip-forever", rationale: "all-docs filechange" };
  }
  if (
    files.some((f) =>
      f.startsWith("packages/gateway") ||
      f.startsWith("packages/server") ||
      f.startsWith("packages/sandbox") ||
      f.startsWith("packages/openapi") ||
      f.startsWith("packages/devtools")
    )
  ) {
    return {
      action: "skip-v0",
      rationale: "touches packages skipped per PORT_PLAN (gateway/server/sandbox/openapi/devtools)",
    };
  }
  if (
    files.some((f) =>
      f.endsWith(".d.ts") || f.startsWith("apps/cli") || f.startsWith("packages/cli")
    ) &&
    files.every((f) => f.endsWith(".d.ts") || f.startsWith("apps/cli") || f.startsWith("packages/cli"))
  ) {
    return {
      action: "skip-forever",
      rationale: "TS-only: types or CLI tooling",
    };
  }
  return null;
}


/**
 * Token + cost estimate for a single call.
 *
 * Pricing (claude-sonnet-4-5 as of 2026-05, microcents = 1e-6 USD):
 *   input  $3  / MTok = $3e-6/tok =  3 microcents/tok
 *   output $15 / MTok = $15e-6/tok = 15 microcents/tok
 *
 * The earlier version of this function used 0.3 / 1.5 — a 10x
 * understatement that confused $0.30/MTok with $3/MTok. Fixed
 * 2026-05-18 after comparing engine-reported usage against the
 * Anthropic console invoice.
 */
export function estimateCostMicrocents(args: {
  tokensIn: number;
  tokensOut: number;
}): number {
  const inMicro = Math.round(args.tokensIn * 3);
  const outMicro = Math.round(args.tokensOut * 15);
  return inMicro + outMicro;
}


/**
 * Format microcents back as USD for human display ($0.123).
 */
export function formatUsd(microcents: number): string {
  const dollars = microcents / 1_000_000;
  return `$${dollars.toFixed(4)}`;
}


/**
 * Sum TokenUsageReported events recorded by the smithers engine for a
 * given run. Returns actual API-reported token counts — strictly more
 * accurate than the model's self-reported tokensUsed field, which is
 * a guess.
 *
 * Filter by nodeIdPrefix to scope to a phase (e.g., "translate:"). The
 * runIdPrefix accepts either the exact run_id or a wildcard match
 * (e.g., "port-sync-real-1pr-v5"); the parent run plus all child
 * subflow run_ids are matched.
 */
export function readActualTokenUsage(args: {
  dbPath: string;
  runIdPrefix: string;
  nodeIdPrefix?: string;
}): { tokensIn: number; tokensOut: number; calls: number } {
  let db: Database;
  try {
    db = new Database(args.dbPath, { readonly: true });
  } catch {
    return { tokensIn: 0, tokensOut: 0, calls: 0 };
  }
  try {
    const sql =
      "SELECT payload_json FROM _smithers_events " +
      "WHERE type='TokenUsageReported' " +
      "AND (run_id = ?1 OR run_id LIKE ?1 || '%')";
    const rows = db.query(sql).all(args.runIdPrefix) as { payload_json: string }[];
    let tokensIn = 0;
    let tokensOut = 0;
    let calls = 0;
    for (const row of rows) {
      const p = JSON.parse(row.payload_json);
      if (args.nodeIdPrefix && !String(p.nodeId ?? "").startsWith(args.nodeIdPrefix)) {
        continue;
      }
      tokensIn += Number(p.inputTokens ?? 0);
      tokensOut += Number(p.outputTokens ?? 0);
      calls += 1;
    }
    return { tokensIn, tokensOut, calls };
  } finally {
    db.close();
  }
}
