// Zod schemas for the ongoing-sync meta-workflow.
//
// Mirrors the shape Cory's bun-port uses but for a *recurring* sync
// problem rather than a one-shot translation. Phases produce typed
// rows that the engine can pause/resume on, gate against, and re-emit
// to the parity acceptance test.

import { z } from "zod";


// -------------------- Top-level input ----------------------------------------

export const portSyncInputSchema = z.object({
  upstreamRepo: z.string().default("smithersai/smithers"),
  upstreamBranch: z.string().default("main"),
  forkRepo: z.string().default("understudylabs/smithers"),
  forkBranch: z.string().default("port/resume"),
  // ISO-ish date the last successful sync ran. New PRs/commits after
  // this date are considered.
  sinceIso: z.string().default(""),
  // When zero, scan upstream and decide. When set, override.
  prsToProcess: z.array(z.number().int()).default([]),
  // Concurrency for parallel per-PR work.
  maxConcurrency: z.number().int().min(1).max(16).default(4),
  // ApprovalGate thresholds.
  thresholds: z.object({
    reviewerRejectionMax: z.number().int().min(0).max(100).default(15),
    classifierConfidenceMin: z.number().int().min(0).max(100).default(60),
    parityDiffMax: z.number().int().min(0).default(0),
  }).default({
    reviewerRejectionMax: 15,
    classifierConfidenceMin: 60,
    parityDiffMax: 0,
  }),
  // Whether to open PRs at the end. False = dry run that stops at the
  // parity gate.
  emitPullRequests: z.boolean().default(false),
  // Whether to require an operator HumanTask at the start.
  requireOperatorPlan: z.boolean().default(false),
});


// -------------------- HumanTask: operator plan ------------------------------

export const operatorPlanSchema = z.object({
  approved: z.boolean(),
  comments: z.string().default(""),
  prsToInclude: z.array(z.number().int()).default([]),
  prsToExclude: z.array(z.number().int()).default([]),
});


// -------------------- Phase 1: upstream-watch -------------------------------

export const upstreamPrSchema = z.object({
  number: z.number().int(),
  title: z.string(),
  author: z.string(),
  mergedAt: z.string(),
  htmlUrl: z.string().default(""),
  filesChanged: z.array(z.string()).default([]),
  labels: z.array(z.string()).default([]),
});

export const upstreamWatchResultSchema = z.object({
  schema_version: z.literal("smithers-port-sync-upstream-watch-v0"),
  sinceIso: z.string(),
  upstreamHead: z.string().default(""),
  prs: z.array(upstreamPrSchema),
  metrics: z.object({
    totalPrs: z.number().int().min(0),
    docsOnlyPrs: z.number().int().min(0),
    gatewayOnlyPrs: z.number().int().min(0),
    runtimePrs: z.number().int().min(0),
  }),
});


// -------------------- Phase 2: delta-classify -------------------------------

export const deltaActionSchema = z.enum([
  "port",                // generate a Python delta and PR
  "port-with-replacement", // port but use a different idiom (Zod→Pydantic, etc.)
  "skip-v0",             // accept upstream change but no Python equivalent yet
  "skip-forever",        // TS-specific (gateway, bun init, types-only)
  "already-ported",      // we already cover this on port/resume
]);

export const deltaClassificationSchema = z.object({
  schema_version: z.literal("smithers-port-sync-classify-v0"),
  prNumber: z.number().int(),
  action: deltaActionSchema,
  pythonTarget: z.string().default(""),
  rationale: z.string(),
  confidence: z.number().int().min(0).max(100),
  needsHumanReview: z.boolean().default(false),
  estimatedTokens: z.number().int().min(0).default(0),
});

export const classificationSummarySchema = z.object({
  schema_version: z.literal("smithers-port-sync-classify-summary-v0"),
  rows: z.array(deltaClassificationSchema),
  metrics: z.object({
    portCount: z.number().int().min(0),
    portWithReplacementCount: z.number().int().min(0),
    skipV0Count: z.number().int().min(0),
    skipForeverCount: z.number().int().min(0),
    alreadyPortedCount: z.number().int().min(0),
    avgConfidence: z.number().int().min(0).max(100),
    rejectionRate: z.number().int().min(0).max(100),
  }),
});


// -------------------- Phase 3: delta-translate ------------------------------

export const translationRowSchema = z.object({
  schema_version: z.literal("smithers-port-sync-translate-v0"),
  prNumber: z.number().int(),
  pythonTarget: z.string(),
  status: z.enum(["drafted", "skipped", "failed"]),
  diffPreview: z.string().default(""),
  rsLoc: z.number().int().min(0).default(0),
  pyLoc: z.number().int().min(0).default(0),
  notes: z.string().default(""),
  tokensUsed: z.number().int().min(0).default(0),
});

export const translationSummarySchema = z.object({
  schema_version: z.literal("smithers-port-sync-translate-summary-v0"),
  rows: z.array(translationRowSchema),
  metrics: z.object({
    drafted: z.number().int().min(0),
    failed: z.number().int().min(0),
    skipped: z.number().int().min(0),
    totalTokensIn: z.number().int().min(0),
    totalTokensOut: z.number().int().min(0),
    estimatedCostUsdMicrocents: z.number().int().min(0), // store as int microcents
  }),
});


// -------------------- Phase 4: cross-runtime-verify -------------------------

export const parityResultSchema = z.object({
  schema_version: z.literal("smithers-port-sync-parity-v0"),
  passed: z.boolean(),
  divergences: z.array(z.string()).default([]),
  rowsCompared: z.number().int().min(0),
  rowsEqual: z.number().int().min(0),
  notes: z.string().default(""),
});


// -------------------- Phase 5: pr-emit --------------------------------------

export const prDraftSchema = z.object({
  schema_version: z.literal("smithers-port-sync-pr-draft-v0"),
  upstreamPrNumber: z.number().int(),
  forkBranch: z.string(),
  title: z.string(),
  body: z.string(),
  filesChanged: z.array(z.string()).default([]),
  status: z.enum(["drafted", "opened", "skipped", "failed"]),
  pullRequestUrl: z.string().default(""),
});


// -------------------- Approval shape (gate-resolution) ----------------------

export const approvalSchema = z.object({
  approved: z.boolean(),
  note: z.string().nullable().default(""),
  decidedBy: z.string().nullable().default(""),
}).loose();


// -------------------- Final report ------------------------------------------

export const portSyncFinalSchema = z.object({
  schema_version: z.literal("smithers-port-sync-final-v0"),
  status: z.enum(["completed", "cancelled", "partial", "blocked-by-parity"]),
  phasesRun: z.array(z.string()),
  prsConsidered: z.number().int().min(0),
  prsPorted: z.number().int().min(0),
  prsSkipped: z.number().int().min(0),
  parityHeld: z.boolean(),
  pullRequestsOpened: z.number().int().min(0),
  summary: z.string(),
  estimatedSpendMicrocents: z.number().int().min(0),
  nextActions: z.array(z.string()).default([]),
});
