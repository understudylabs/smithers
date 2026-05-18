// Dry + real-mode agents for the ongoing-sync workflow.
//
// Real-mode flips when SMITHERS_PORT_PY_REAL_AGENTS=1 — uses ClaudeCode +
// PiAgent for write/review like bun-port-smithers does. Dry-mode returns
// deterministic outputs based on prompt tags so the workflow shape can
// be validated end-to-end with zero LLM budget.

import { AnthropicAgent, ClaudeCodeAgent, PiAgent } from "smithers-orchestrator";

type AgentArgs = { prompt?: string; outputSchema?: unknown };
type AgentResult = { text: string; output: Record<string, unknown> };
type LocalAgent = { id: string; generate(args?: AgentArgs): Promise<AgentResult> };

const useRealAgents = process.env.SMITHERS_PORT_PY_REAL_AGENTS === "1";

// Two real-mode flavors:
//   SMITHERS_PORT_PY_AGENT_MODE=anthropic  →  AnthropicAgent (AI SDK).
//     Text-only generation; no filesystem tools. Generated diffs are
//     captured in the run's row but NOT auto-applied. Safe to spend.
//   SMITHERS_PORT_PY_AGENT_MODE=cli  →  ClaudeCodeAgent + PiAgent.
//     CLI agents that read/write files. Requires `claude` and `pi`
//     binaries on PATH; can mutate the working tree.
//
// Default: "anthropic" (text-only, the safe choice for first run).
const realAgentMode = process.env.SMITHERS_PORT_PY_AGENT_MODE ?? "anthropic";


function readTag(prompt: string, name: string, fallback = ""): string {
  const match = prompt.match(new RegExp(`${name}:\\s*([\\s\\S]*?)(?=(?:\\s+|)[A-Z_]+:|$)`));
  return match?.[1]?.trim() ?? fallback;
}

function readIntTag(prompt: string, name: string, fallback: number): number {
  const text = readTag(prompt, name, "");
  const n = Number.parseInt(text, 10);
  return Number.isFinite(n) ? n : fallback;
}


function dryOutput(kind: string, prompt: string): Record<string, unknown> {
  const prNumber = readIntTag(prompt, "PR_NUMBER", 0);
  const title = readTag(prompt, "PR_TITLE", "untitled");
  const path = readTag(prompt, "PYTHON_TARGET", "smithers_py/runtime/example.py");

  switch (kind) {
    case "classify-delta":
      // Static rules: docs PRs skip-forever; gateway skip-v0;
      // anything mentioning agent/CLI/runtime → port.
      const lowerTitle = title.toLowerCase();
      let action: string = "port";
      let rationale = "dry-run: default to port";
      let confidence = 70;
      if (/^docs|fix doc|readme/i.test(lowerTitle)) {
        action = "skip-forever";
        rationale = "dry-run: docs-only change";
        confidence = 95;
      } else if (/gateway|server|sandbox/i.test(lowerTitle)) {
        action = "skip-v0";
        rationale = "dry-run: gateway/server scope skipped per PORT_PLAN";
        confidence = 90;
      } else if (/agent|cli|runtime|loop|signal|approval|task/i.test(lowerTitle)) {
        action = "port";
        rationale = "dry-run: runtime change — generate Python delta";
        confidence = 80;
      }
      return {
        schema_version: "smithers-port-sync-classify-v0",
        prNumber,
        action,
        pythonTarget: path,
        rationale,
        confidence,
        needsHumanReview: confidence < 70,
        estimatedTokens: 4_000,
      };

    case "translate-delta":
      return {
        schema_version: "smithers-port-sync-translate-v0",
        prNumber,
        pythonTarget: path,
        status: "drafted",
        diffPreview: `# dry-run port stub for PR #${prNumber} ${title}\npass\n`,
        rsLoc: 0,
        pyLoc: 4,
        notes: "dry-run translation",
        tokensUsed: 0,
      };

    case "verify-parity":
      return {
        schema_version: "smithers-port-sync-parity-v0",
        passed: true,
        divergences: [],
        rowsCompared: 12,
        rowsEqual: 12,
        notes: "dry-run: wire_compat snapshot would be re-run here",
      };

    case "emit-pr":
      return {
        schema_version: "smithers-port-sync-pr-draft-v0",
        upstreamPrNumber: prNumber,
        forkBranch: `port/sync/pr-${prNumber}`,
        title: `[port-sync] ${title}`,
        body: `Mirrors upstream PR #${prNumber}.\n\nDry-run PR draft.`,
        filesChanged: [path],
        status: "drafted",
        pullRequestUrl: "",
      };

    default:
      return { kind, ok: true, note: "dry-run default" };
  }
}


function makeDryAgent(kind: string): LocalAgent {
  return {
    id: `smithers-port-sync-dry:${kind}`,
    async generate(args?: AgentArgs): Promise<AgentResult> {
      const output = dryOutput(kind, args?.prompt ?? "");
      return { text: JSON.stringify(output), output };
    },
  };
}


function realWriterAgent(repo: string, kind: string): any {
  if (!useRealAgents) return makeDryAgent(kind);

  if (realAgentMode === "anthropic") {
    // AI-SDK-based Anthropic agent. Text generation only, no
    // filesystem tools. The translate phase captures the model's
    // output text in the diffPreview field; no files are mutated.
    return new AnthropicAgent({
      model: process.env.SMITHERS_PORT_PY_WRITER_MODEL ?? "claude-sonnet-4-5",
    });
  }

  return new ClaudeCodeAgent({
    cwd: repo,
    model: process.env.SMITHERS_PORT_PY_WRITER_MODEL ?? "claude-sonnet-4-5",
    permissionMode: "acceptEdits",
    allowedTools: process.env.SMITHERS_PORT_PY_WRITER_ALLOWED_TOOLS?.split(",") ?? [
      "Read", "Grep", "Glob", "Write", "Edit", "MultiEdit",
      "Bash(uv:*)", "Bash(uv pip:*)", "Bash(uv run:*)",
      "Bash(python:*)", "Bash(pytest:*)", "Bash(ruff:*)", "Bash(mypy:*)",
      "Bash(git status:*)", "Bash(git diff:*)", "Bash(git add:*)",
      "Bash(git commit:*)", "Bash(git push:*)",
      "Bash(rg:*)", "Bash(sed:*)", "Bash(gh:*)",
    ],
    disallowedTools: ["WebFetch", "WebSearch"],
    timeoutMs: 30 * 60 * 1000,
  });
}


function realReviewerAgent(repo: string, kind: string): any {
  if (!useRealAgents) return makeDryAgent(kind);

  if (realAgentMode === "anthropic") {
    // Same agent class as writer (no Pi CLI installed). Anthropic
    // serves both roles in the safe default mode.
    return new AnthropicAgent({
      model: process.env.SMITHERS_PORT_PY_REVIEW_MODEL ?? "claude-sonnet-4-5",
    });
  }

  return new PiAgent({
    cwd: repo,
    provider: process.env.SMITHERS_PORT_PY_REVIEW_PROVIDER ?? "openai-codex",
    model: process.env.SMITHERS_PORT_PY_REVIEW_MODEL ?? "gpt-5.3-codex",
    mode: "rpc",
    thinking: "high",
    tools: ["read", "grep", "bash"],
  });
}


export function agentsFor(args: { forkRepoPath: string }) {
  return {
    classifier:   realReviewerAgent(args.forkRepoPath, "classify-delta"),
    translator:   realWriterAgent(args.forkRepoPath, "translate-delta"),
    verifier:     realReviewerAgent(args.forkRepoPath, "verify-parity"),
    prEmitter:    realWriterAgent(args.forkRepoPath, "emit-pr"),
  };
}
