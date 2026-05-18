// Zod twins of the Pydantic schemas in workflow.py. Field names + literal
// schema_version strings match exactly — that's the entire point of the
// cross-runtime parity contract.

import { z } from "zod";

export const wireInputSchema = z.object({
  workload: z.string(),
  branch: z.boolean().default(true),
  iterations: z.number().int().default(3),
});

export const stepOutSchema = z.object({
  schema_version: z.literal("wire-compat-step-v0"),
  step: z.string(),
  value: z.number().int(),
});

export const childOutSchema = z.object({
  schema_version: z.literal("wire-compat-child-v0"),
  label: z.string(),
  payload: z.array(z.string()),
});

export const finalOutSchema = z.object({
  schema_version: z.literal("wire-compat-final-v0"),
  workload: z.string(),
  sequence_total: z.number().int(),
  parallel_total: z.number().int(),
  branch_step: z.string(),
  loop_iterations: z.number().int(),
});

// Permissive approval shape matching the row upstream writes when an
// ApprovalGate resolves. Our Python side writes a synthetic
// `smithers-py-approval-v0` row for the same event; the wire-compat
// README documents that schema_version drift as an expected divergence.
export const approvalSchema = z.object({
  approved: z.boolean(),
}).loose();
