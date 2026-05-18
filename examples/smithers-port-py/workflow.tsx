/** @jsxImportSource smithers-orchestrator */
import {
  ApprovalGate,
  HumanTask,
  Subflow,
} from "@smithers-orchestrator/components";
import { createSmithers } from "smithers-orchestrator";

import {
  approvalSchema,
  classificationSummarySchema,
  operatorPlanSchema,
  parityResultSchema,
  portSyncFinalSchema,
  portSyncInputSchema,
  prDraftSchema,
  translationSummarySchema,
  upstreamWatchResultSchema,
} from "./components/schemas.ts";
import { estimateCostMicrocents, readActualTokenUsage } from "./components/sync-rules.ts";
import OperatorPlanPrompt from "./prompts/operator-plan.mdx";
import classifyWorkflow from "./workflows/delta-classify.tsx";
import translateWorkflow from "./workflows/delta-translate.tsx";
import verifyWorkflow from "./workflows/cross-runtime-verify.tsx";
import emitWorkflow from "./workflows/pr-emit.tsx";
import upstreamWatchWorkflow from "./workflows/upstream-watch.tsx";

const { Workflow, Task, Sequence, smithers, outputs } = createSmithers(
  {
    input: portSyncInputSchema,
    operatorPlan: operatorPlanSchema,
    upstreamWatch: upstreamWatchResultSchema,
    classifySummary: classificationSummarySchema,
    classifyApproval: approvalSchema,
    translateSummary: translationSummarySchema,
    parityResult: parityResultSchema,
    parityApproval: approvalSchema,
    prDraft: prDraftSchema,
    output: portSyncFinalSchema,
  },
  { dbPath: process.env.SMITHERS_PORT_SYNC_DB ?? "smithers.db" },
);

const FORK_REPO_PATH = "/Users/luis/smithers";


export default smithers((ctx) => {
  const operatorPlan = ctx.outputMaybe(outputs.operatorPlan, { nodeId: "main:operator-plan" });
  const operatorDenied = ctx.input.requireOperatorPlan && operatorPlan?.approved === false;
  const canRun = !ctx.input.requireOperatorPlan || operatorPlan?.approved === true;

  const upstream = ctx.outputMaybe(outputs.upstreamWatch, { nodeId: "main:upstream-watch" });
  const classify = ctx.outputMaybe(outputs.classifySummary, { nodeId: "main:classify" });
  const classifyApproval = ctx.outputMaybe(outputs.classifyApproval, { nodeId: "main:classify:approval" });
  const translate = ctx.outputMaybe(outputs.translateSummary, { nodeId: "main:translate" });
  const parity = ctx.outputMaybe(outputs.parityResult, { nodeId: "main:verify" });
  const parityApproval = ctx.outputMaybe(outputs.parityApproval, { nodeId: "main:verify:approval" });
  const prDraft = ctx.outputMaybe(outputs.prDraft, { nodeId: "main:emit" });

  return (
    <Workflow name="smithers-port-py-sync">
      <Sequence>
        {ctx.input.requireOperatorPlan && !operatorPlan ? (
          <HumanTask
            id="main:operator-plan"
            output={outputs.operatorPlan}
            outputSchema={operatorPlanSchema}
            maxAttempts={3}
            timeoutMs={24 * 60 * 60_000}
            prompt={
              <OperatorPlanPrompt
                upstreamRepo={ctx.input.upstreamRepo}
                upstreamBranch={ctx.input.upstreamBranch}
                forkRepo={ctx.input.forkRepo}
                forkBranch={ctx.input.forkBranch}
                sinceIso={ctx.input.sinceIso}
                maxConcurrency={ctx.input.maxConcurrency}
                emitPullRequests={ctx.input.emitPullRequests}
              />
            }
          />
        ) : null}

        {operatorDenied ? (
          <Task id="main:cancelled" output={outputs.output}>
            {{
              schema_version: "smithers-port-sync-final-v0" as const,
              status: "cancelled" as const,
              phasesRun: [],
              prsConsidered: 0,
              prsPorted: 0,
              prsSkipped: 0,
              parityHeld: true,
              pullRequestsOpened: 0,
              summary: `Operator denied the sync run. ${operatorPlan?.comments ?? ""}`.trim(),
              estimatedSpendMicrocents: 0,
              nextActions: [],
            }}
          </Task>
        ) : null}

        {canRun ? (
          <Subflow
            id="main:upstream-watch"
            output={outputs.upstreamWatch}
            workflow={upstreamWatchWorkflow as any}
            input={{
              upstreamRepo: ctx.input.upstreamRepo,
              upstreamBranch: ctx.input.upstreamBranch,
              sinceIso: ctx.input.sinceIso,
              prsToProcess: ctx.input.prsToProcess,
            }}
          />
        ) : null}

        {upstream ? (
          <Subflow
            id="main:classify"
            output={outputs.classifySummary}
            workflow={classifyWorkflow as any}
            input={{
              upstreamRepo: ctx.input.upstreamRepo,
              forkRepoPath: FORK_REPO_PATH,
              prs: upstream.prs,
              maxConcurrency: ctx.input.maxConcurrency,
              rubricRev: "v0.1.0",
            }}
          />
        ) : null}

        {classify && !classifyApproval ? (
          <ApprovalGate
            id="main:classify:approval"
            output={outputs.classifyApproval}
            when={classify.metrics.rejectionRate > ctx.input.thresholds.reviewerRejectionMax}
            request={{
              title: "Approve classification rubric outcome?",
              summary:
                `Classified ${classify.rows.length} PRs. ` +
                `port=${classify.metrics.portCount}, ` +
                `port-with-replacement=${classify.metrics.portWithReplacementCount}, ` +
                `skip-v0=${classify.metrics.skipV0Count}, ` +
                `skip-forever=${classify.metrics.skipForeverCount}, ` +
                `already-ported=${classify.metrics.alreadyPortedCount}. ` +
                `Reject rate ${classify.metrics.rejectionRate}% (threshold ${ctx.input.thresholds.reviewerRejectionMax}%).`,
              metadata: { rows: classify.rows },
            }}
            onDeny="fail"
          />
        ) : null}

        {classify ? (
          <Subflow
            id="main:translate"
            output={outputs.translateSummary}
            workflow={translateWorkflow as any}
            input={{
              forkRepoPath: FORK_REPO_PATH,
              upstreamRepo: ctx.input.upstreamRepo,
              classifications: classify,
              upstreamPrs: upstream?.prs ?? [],
              maxConcurrency: ctx.input.maxConcurrency,
            }}
          />
        ) : null}

        {translate ? (
          <Subflow
            id="main:verify"
            output={outputs.parityResult}
            workflow={verifyWorkflow as any}
            input={{
              forkRepoPath: FORK_REPO_PATH,
              translationSummary: translate,
            }}
          />
        ) : null}

        {parity && !parityApproval ? (
          <ApprovalGate
            id="main:verify:approval"
            output={outputs.parityApproval}
            when={!parity.passed}
            request={{
              title: "Cross-runtime parity test failed — proceed anyway?",
              summary:
                `wire_compat divergences detected. ` +
                `rowsCompared=${parity.rowsCompared} rowsEqual=${parity.rowsEqual}. ` +
                `Top divergence: ${parity.divergences[0] ?? "(none)"}.`,
              metadata: { divergences: parity.divergences },
            }}
            onDeny="fail"
          />
        ) : null}

        {parity && translate && classify ? (
          <Subflow
            id="main:emit"
            output={outputs.prDraft}
            workflow={emitWorkflow as any}
            input={{
              forkRepo: ctx.input.forkRepo,
              forkBranch: ctx.input.forkBranch,
              classifications: classify,
              translations: translate,
              emitPullRequests: ctx.input.emitPullRequests,
            }}
          />
        ) : null}

        {prDraft ? (
          <Task id="main:final" output={outputs.output}>
            {() => {
              const total = classify?.rows.length ?? 0;
              const ported = translate?.metrics.drafted ?? 0;
              const failed = translate?.metrics.failed ?? 0;
              const skipped = (classify?.metrics.skipV0Count ?? 0) +
                (classify?.metrics.skipForeverCount ?? 0) +
                (classify?.metrics.alreadyPortedCount ?? 0) +
                failed;
              // Roll up real classifier + translator cost. Both phases
              // record TokenUsageReported events; we sum classifier
              // costs here and add translate's already-rolled-up cost.
              const classifyUsage = readActualTokenUsage({
                dbPath: process.env.SMITHERS_PORT_SYNC_DB ?? "smithers.db",
                runIdPrefix: ctx.runId,
                nodeIdPrefix: "classify:",
              });
              const classifyCost = estimateCostMicrocents({
                tokensIn: classifyUsage.tokensIn,
                tokensOut: classifyUsage.tokensOut,
              });
              const translateCost = translate?.metrics.estimatedCostUsdMicrocents ?? 0;
              const cost = classifyCost + translateCost;
              return {
                schema_version: "smithers-port-sync-final-v0" as const,
                status: parity?.passed === false
                  ? "blocked-by-parity" as const
                  : "completed" as const,
                phasesRun: ["upstream-watch", "classify", "translate", "verify", "emit"],
                prsConsidered: total,
                prsPorted: ported,
                prsSkipped: skipped,
                parityHeld: parity?.passed ?? false,
                pullRequestsOpened: prDraft.status === "opened" ? 1 : 0,
                summary: `Considered ${total} PRs, ported ${ported}, skipped ${skipped}. ` +
                  `Parity ${parity?.passed ? "held" : "FAILED"}. ` +
                  `PR status: ${prDraft.status}.`,
                estimatedSpendMicrocents: cost,
                nextActions: [
                  "Review the generated PR draft and merge if green.",
                  "Bump `sinceIso` to the latest mergedAt for the next sync run.",
                ],
              };
            }}
          </Task>
        ) : null}
      </Sequence>
    </Workflow>
  );
});
