#!/usr/bin/env bun
// Compare per-model meta-workflow runs side-by-side.
//
// Reads SQLite output rows from per-model DBs (one per
// SMITHERS_PORT_PY_AGENT_MODE) and prints a comparison table covering
// cost, latency, token usage, and diff coherence. Used after running
// the same PR fixture against multiple models to evaluate which open
// model produces usable output.
//
// Usage:
//   ./scripts/compare-models.ts \
//     --run sonnet=port-sync-pr88-v2:smithers.db \
//     --run glm=port-sync-glm-pr88-v3:smithers.db \
//     --run kimi=kimi-pr88:smithers-kimi.db \
//     --run deepseek=deepseek-pr88:smithers-ds.db

import { Database } from "bun:sqlite";


type Spec = { label: string; runIdPrefix: string; dbPath: string };

type Stats = {
  label: string;
  classifyIn: number;
  classifyOut: number;
  translateIn: number;
  translateOut: number;
  classifyAction: string;
  classifyConfidence: number;
  pythonTarget: string;
  translateStatus: string;
  diffPreviewLen: number;
  pyLoc: number;
  spendMicrocents: number;
  diffFirstLine: string;
  diffLooksLikeUnifiedDiff: boolean;
  notesSnippet: string;
};


function readStats(spec: Spec): Stats {
  const db = new Database(spec.dbPath, { readonly: true });
  try {
    // Real token usage from events.
    const usageRows = db
      .query(
        "SELECT payload_json FROM _smithers_events WHERE type='TokenUsageReported' AND (run_id = ?1 OR run_id LIKE ?1 || ':%')",
      )
      .all(spec.runIdPrefix) as { payload_json: string }[];
    let classifyIn = 0, classifyOut = 0, translateIn = 0, translateOut = 0;
    for (const r of usageRows) {
      const p = JSON.parse(r.payload_json);
      const nid = String(p.nodeId ?? "");
      if (nid.startsWith("classify:")) {
        classifyIn += Number(p.inputTokens ?? 0);
        classifyOut += Number(p.outputTokens ?? 0);
      } else if (nid.startsWith("translate:")) {
        translateIn += Number(p.inputTokens ?? 0);
        translateOut += Number(p.outputTokens ?? 0);
      }
    }

    const classify = db
      .query("SELECT action, confidence, python_target FROM classification WHERE run_id LIKE ?1 || '%' LIMIT 1")
      .get(spec.runIdPrefix) as any;
    const translate = db
      .query("SELECT status, diff_preview, py_loc, notes FROM translation WHERE run_id LIKE ?1 || '%' LIMIT 1")
      .get(spec.runIdPrefix) as any;
    const final = db
      .query(
        "SELECT estimated_spend_microcents FROM output " +
          "WHERE estimated_spend_microcents IS NOT NULL " +
          "AND (run_id = ?1 OR run_id LIKE ?1 || ':%') " +
          "ORDER BY length(run_id) ASC LIMIT 1",
      )
      .get(spec.runIdPrefix) as any;

    const diff = translate?.diff_preview ?? "";
    const firstLine = diff.split("\n")[0] ?? "";

    return {
      label: spec.label,
      classifyIn,
      classifyOut,
      translateIn,
      translateOut,
      classifyAction: classify?.action ?? "(none)",
      classifyConfidence: classify?.confidence ?? 0,
      pythonTarget: classify?.python_target ?? "",
      translateStatus: translate?.status ?? "(none)",
      diffPreviewLen: diff.length,
      pyLoc: translate?.py_loc ?? 0,
      spendMicrocents: final?.estimated_spend_microcents ?? 0,
      diffFirstLine: firstLine.slice(0, 80),
      diffLooksLikeUnifiedDiff:
        firstLine.startsWith("--- ") || firstLine.startsWith("diff ") || firstLine.includes("@@"),
      notesSnippet: (translate?.notes ?? "").slice(0, 100),
    };
  } finally {
    db.close();
  }
}


function fmtUsd(microcents: number): string {
  return "$" + (microcents / 1_000_000).toFixed(4);
}


function parseArgs(argv: string[]): Spec[] {
  const specs: Spec[] = [];
  for (let i = 2; i < argv.length; i++) {
    if (argv[i] === "--run" && i + 1 < argv.length) {
      const value = argv[++i];
      const eq = value.indexOf("=");
      const colon = value.lastIndexOf(":");
      if (eq < 0 || colon < eq) {
        console.error(`bad --run spec: ${value}; expected label=runId:dbPath`);
        process.exit(2);
      }
      specs.push({
        label: value.slice(0, eq),
        runIdPrefix: value.slice(eq + 1, colon),
        dbPath: value.slice(colon + 1),
      });
    }
  }
  return specs;
}


const specs = parseArgs(process.argv);
if (specs.length === 0) {
  console.error("Usage: compare-models.ts --run label=runId:dbPath [--run ...]");
  process.exit(2);
}

const rows = specs.map(readStats);

console.log("\n=== Token usage + cost ===\n");
console.log("model       classify(in/out)   translate(in/out)   total$ (per-model rate)");
for (const r of rows) {
  const tot = r.spendMicrocents;
  console.log(
    `  ${r.label.padEnd(10)} ${String(r.classifyIn).padStart(5)}/${String(r.classifyOut).padStart(4)}  ` +
      `       ${String(r.translateIn).padStart(5)}/${String(r.translateOut).padStart(5)}    ${fmtUsd(tot)}`,
  );
}

console.log("\n=== Quality signals ===\n");
console.log("model       classify   translate    diff_len   pyLoc   unified_diff?  target");
for (const r of rows) {
  console.log(
    `  ${r.label.padEnd(10)} ${r.classifyAction.padEnd(10)} ${r.translateStatus.padEnd(11)} ` +
      `${String(r.diffPreviewLen).padStart(6)}   ${String(r.pyLoc).padStart(5)}   ${r.diffLooksLikeUnifiedDiff ? "yes" : " NO"}            ${r.pythonTarget}`,
  );
}

console.log("\n=== Diff first line per model ===\n");
for (const r of rows) {
  console.log(`  ${r.label}:  ${r.diffFirstLine || "(empty)"}`);
}

console.log();
