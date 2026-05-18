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


export function readTargetFile(args: {
  forkRepoPath: string;
  relativePath: string;
  maxChars?: number;
}): { content: string; exists: boolean; truncated: boolean } {
  const max = args.maxChars ?? 16_000;
  const abs = args.relativePath.startsWith("/")
    ? args.relativePath
    : `${args.forkRepoPath}/${args.relativePath}`;
  try {
    const raw = execSync(`cat ${JSON.stringify(abs)}`, {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
      maxBuffer: 10 * 1024 * 1024,
    });
    if (raw.length <= max) {
      return { content: raw, exists: true, truncated: false };
    }
    return {
      content: raw.slice(0, max) + `\n... [truncated; file was ${raw.length} chars]`,
      exists: true,
      truncated: true,
    };
  } catch {
    return { content: "", exists: false, truncated: false };
  }
}


export function fetchPrDiff(args: {
  repo: string;
  number: number;
  maxChars?: number;
}): string {
  const max = args.maxChars ?? 24_000;
  try {
    const raw = execSync(
      `gh pr diff ${args.number} --repo ${args.repo}`,
      { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], maxBuffer: 10 * 1024 * 1024 },
    );
    if (raw.length <= max) return raw;
    return raw.slice(0, max) + `\n... [truncated; full diff was ${raw.length} chars]`;
  } catch (err) {
    return `(diff fetch failed: ${err})`;
  }
}


export function fetchPrsByNumber(args: {
  repo: string;
  numbers: number[];
}): UpstreamPr[] {
  // Enrich a known list of PR numbers via `gh pr view`. Used when the
  // workflow input pins specific PRs (override mode) — gives the
  // classifier real title/author/files instead of a stub.
  const out: UpstreamPr[] = [];
  for (const n of args.numbers) {
    try {
      const raw = execSync(
        `gh pr view ${n} --repo ${args.repo} --json number,title,author,mergedAt,url,labels,files`,
        { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] },
      );
      const row = JSON.parse(raw);
      const filesChanged: string[] = Array.isArray(row?.files)
        ? row.files.map((f: any) => f?.path ?? "").filter(Boolean)
        : [];
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
    } catch (err) {
      console.warn(`[upstream-watch] gh pr view #${n} failed; using stub: ${err}`);
      out.push({
        number: n,
        title: `(override) PR #${n}`,
        author: "",
        mergedAt: "",
        htmlUrl: "",
        filesChanged: [],
        labels: [],
      });
    }
  }
  return out;
}


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
