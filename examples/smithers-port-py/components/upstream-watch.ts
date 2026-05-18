// Helper that fetches recently-merged PRs from upstream via `gh api`.
// Used by the upstream-watch Subflow. Synchronous + deterministic given
// (repo, branch, sinceIso) — sufficient for the workflow's cache key.

import { execSync } from "node:child_process";


export type UpstreamPr = {
  number: number;
  title: string;
  author: string;
  mergedAt: string;
  htmlUrl: string;
  filesChanged: string[];
  labels: string[];
};


export function fetchRecentPrs(args: {
  repo: string;
  sinceIso: string;
}): UpstreamPr[] {
  // Build a minimal `gh` search query.
  const query = args.sinceIso
    ? `is:merged is:pr base:main merged:>${args.sinceIso}`
    : "is:merged is:pr base:main";
  const cmd = [
    "gh", "search", "prs",
    "--repo", args.repo,
    "--limit", "30",
    "--json", "number,title,author,mergedAt,url,labels",
    "--", query,
  ].map((a) => JSON.stringify(a)).join(" ");
  let raw = "";
  try {
    raw = execSync(cmd, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
  } catch (err) {
    // gh may not be installed or auth'd; fall back to an empty list so
    // the workflow can still run in dry mode.
    console.warn(`[upstream-watch] gh search failed; returning empty list: ${err}`);
    return [];
  }
  let parsed: any[] = [];
  try {
    parsed = JSON.parse(raw);
  } catch {
    parsed = [];
  }
  const out: UpstreamPr[] = [];
  for (const row of parsed) {
    if (typeof row?.number !== "number") continue;
    let filesChanged: string[] = [];
    // Optional per-PR enrich: list files. Skipped if PR list is large.
    if (parsed.length <= 10) {
      try {
        const files = execSync(
          `gh pr view ${row.number} --repo ${args.repo} --json files --jq '.files[].path'`,
          { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] },
        );
        filesChanged = files.split("\n").map((s) => s.trim()).filter(Boolean);
      } catch {
        // Best-effort; leave empty if files list fails.
      }
    }
    out.push({
      number: row.number,
      title: row.title ?? "",
      author: typeof row.author === "object" ? (row.author?.login ?? "") : (row.author ?? ""),
      mergedAt: row.mergedAt ?? "",
      htmlUrl: row.url ?? "",
      filesChanged,
      labels: Array.isArray(row.labels)
        ? row.labels.map((l: any) => l?.name ?? "").filter(Boolean)
        : [],
    });
  }
  return out;
}
