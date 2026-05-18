// Deterministic helpers — no LLM. The methodology of the sync workflow
// lives here.

import { createHash } from "node:crypto";


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
 * Token + cost estimate for a single translate call.
 * Pricing model (claude-sonnet-4-5 as of 2026-05):
 *   input  $3 / MTok  → 300 microcents per Ktok = 0.3 microcents/tok
 *   output $15 / MTok → 1500 microcents per Ktok = 1.5 microcents/tok
 *
 * (Microcents = 1e-6 USD; we use integers so SQLite stores cleanly.)
 */
export function estimateCostMicrocents(args: {
  tokensIn: number;
  tokensOut: number;
}): number {
  const inMicro = Math.round(args.tokensIn * 0.3);
  const outMicro = Math.round(args.tokensOut * 1.5);
  return inMicro + outMicro;
}


/**
 * Format microcents back as USD for human display ($0.123).
 */
export function formatUsd(microcents: number): string {
  const dollars = microcents / 1_000_000;
  return `$${dollars.toFixed(4)}`;
}
