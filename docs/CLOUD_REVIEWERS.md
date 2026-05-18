# Cloud reviewers for `understudylabs/smithers`

Three AI reviewers wired against PRs to `main`. Each is independent; the
goal is uncorrelated eyes — Codex, Greptile, and Devin have different
training and different failure modes, so two-of-three approving is a
stronger signal than a single reviewer's thumbs-up.

| Reviewer | How it fires | What you install | Where the comment lands |
| --- | --- | --- | --- |
| **Codex** | GitHub Action `cloud-review-codex.yml` runs `codex review` and posts the output | Repository secret `OPENAI_API_KEY` | PR comment, prefixed `## 🔍 Codex review` |
| **Greptile** | GitHub App, auto-comments on PR open + push | [app.greptile.com](https://app.greptile.com) — install on the `understudylabs` org | PR comment, prefixed with a Greptile mascot avatar |
| **Devin** | GitHub App, posts review + can be `@mentioned` to ask follow-ups | [app.devin.ai](https://app.devin.ai) — install on the `understudylabs` org | PR comment from `devin-ai-integration[bot]` |

## Setup checklist

Run these once per repo:

### 1. Codex (~2 min)

1. Open **Settings → Secrets and variables → Actions → New repository
   secret** on `understudylabs/smithers`.
2. Name: `OPENAI_API_KEY`. Value: a project API key from
   [platform.openai.com](https://platform.openai.com/api-keys) with the
   Codex model access enabled.
3. Verify by re-running the latest PR check — `Codex Cloud Review`
   should show as a check on the PR within a minute of secret save.

### 2. Greptile (~3 min)

1. Go to [app.greptile.com](https://app.greptile.com) → **Sign in with
   GitHub**.
2. **Install GitHub App** → select the `understudylabs` organization →
   restrict to "Only select repositories" → pick `smithers`.
3. (Optional) In Greptile's dashboard, set the review style. The
   defaults are sensible — security + best practices + brief
   explanations.
4. Trigger a re-review on PR #1 by pushing an empty commit or by
   clicking "Re-review" in the Greptile UI on the PR.

### 3. Devin (~5 min)

1. Go to [app.devin.ai](https://app.devin.ai) → sign in.
2. **Integrations → GitHub → Install GitHub App** → select the
   `understudylabs` organization.
3. Approve repo access for `smithers`.
4. In the Devin workspace, enable **PR Review** mode for the repo.
5. Devin will comment on the next push. You can also `@devin-ai-integration`
   in a PR comment to ask follow-up questions during review.

## What each reviewer is good at

Based on community experience as of 2026-05:

- **Codex** — strongest at idiomatic per-language critique. Catches
  Python smells (mutable defaults, `__all__` mismatches, type-hint
  drift) and TypeScript pitfalls (`any` leaks, missing `await`).
  Weakest at cross-file architectural review.
- **Greptile** — best at finding patterns that violate the codebase's
  own conventions ("you have a `ts_*` table naming convention but this
  table is named `_smithers_x`"). Indexes the whole repo, so its
  comments cite related files.
- **Devin** — the most autonomous; will often propose a fix patch
  rather than just identify the issue. Good at end-to-end test gaps
  ("these three test files exist but don't exercise the SSE
  reconnection path"). Most expensive of the three.

## How to interpret the reviews

The PR target is `port/resume → main` and is marked **draft, do not
merge**. Treat each reviewer comment as one of three classes:

1. **Bug, fix it.** Concrete correctness issue with a clear repro.
   Land a follow-up commit.
2. **Drift, document it.** The reviewer found a divergence from
   upstream Smithers' convention. Either reconcile, or write a note in
   `PARITY.md` justifying the divergence.
3. **Style, ignore unless three agree.** Stylistic preference (e.g.,
   "use `dataclass` instead of `BaseModel` here"). Only act if 2 of 3
   reviewers agree, otherwise it's noise.

## Cost

Per-PR cost order-of-magnitude (assuming ~50 files of diff):

- Codex CLI: ~$0.30-1.50 per `codex review` run (Sonnet/o4-mini class
  models)
- Greptile: free tier covers ~50 reviews/month on public repos; paid
  tier $20/seat/month
- Devin: $20/month subscription includes PR reviews; comment-based
  follow-ups consume "ACUs" from the plan budget

Total for the active development of this fork (~20 PRs/month):
**~$80-150/month** across the three. Justifiable for the parity-push
phase; can drop Devin once the workflow is stable.

## When the GitHub Action fails

The Codex Action workflow can fail because:

- `OPENAI_API_KEY` is missing or the secret was added to the wrong
  scope (environment vs repo).
- The Codex CLI rate-limited (5xx from OpenAI). Re-run from the
  Actions tab.
- The model account doesn't have access to the review model. Check
  [platform.openai.com/settings/organization/limits](https://platform.openai.com/settings/organization/limits).

Greptile and Devin failures are visible in their respective dashboards
(app.greptile.com / app.devin.ai). They don't block the PR.
