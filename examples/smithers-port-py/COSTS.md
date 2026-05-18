# Real-mode cost model for `smithers-port-py-sync`

What it costs to run the ongoing-sync workflow with `SMITHERS_PORT_PY_REAL_AGENTS=1`.
Numbers below assume `claude-sonnet-4-5` for the writer agent and a
GPT-5-class reviewer agent. Costs are in USD, rounded to 4 decimal places.

## Per-PR cost breakdown

Each upstream PR flowing through the workflow incurs up to four LLM
calls. The classifier shortcut + retry policy mean most PRs cost less
than the full pipeline.

| Stage | Tokens in | Tokens out | Cost (sonnet-4-5) | Cost (gpt-5-class reviewer) |
| --- | --- | --- | --- | --- |
| `delta-classify` (1 call) | ~2,000 | ~500 | $0.0135 | n/a — runs as the static fallback path |
| `delta-translate` (1 call, writer) | ~10,000 | ~2,000 | $0.0600 | n/a |
| Reviewer pass (optional, post-translate) | ~5,000 | ~1,000 | n/a | ~$0.0300 |
| Failure retry (1.5× expected) | — | — | +0.5× translate cost | +0.5× reviewer cost |
| `verify-parity` | $0 — runs wire_compat test locally, no LLM | | | |
| `emit-pr` | $0 — runs `gh pr create`, no LLM | | | |

**Tokens-in assumptions:**

- Classify prompt: PR title + author + ≤30 changed-file paths + rubric (~2,000 tokens).
- Translate prompt: target Python file's current content (avg 3,000 tokens) + the upstream PR's diff (avg 5,000 tokens) + paradigm rules + schema definition (~10,000 tokens total).
- Reviewer prompt: drafted diff + the original spec + relevant Python tests (~5,000 tokens).

**Static-rule shortcut hit rate:** historically ~40–60% of upstream PRs route through `staticClassification` and skip the LLM classifier call entirely (docs-only, gateway, `.d.ts`). Effective per-PR cost drops to roughly:

- $0.013 × 0.5 (classify) + $0.060 (translate) + $0.030 (review) + 1.5× retry = **~$0.14 per PR average**

## Frequency and rate

Upstream `smithersai/smithers:main` has merged **23 PRs since 2026-01-23** (a ~4-month window). That's roughly **1.5 PRs per week** — though the distribution is bursty (5 PRs landed on `2026-05-04` alone).

Of those 23, only 18 were Tier-1/Tier-2 work that the Python port actually mirrors. The other 9 were docs/gateway/Bun-specific. So the steady-state expected count of LLM-eligible PRs is closer to **1 per week**.

| Frequency assumption | LLM-eligible PRs / week | Weekly cost | Monthly cost | Annual cost |
| --- | --- | --- | --- | --- |
| Conservative (cheap PRs, high shortcut hit) | 1 × $0.10 | $0.10 | $0.45 | $5 |
| Expected (per-PR average above) | 1 × $0.14 | $0.14 | $0.60 | $7 |
| High activity (bursty week, complex PRs) | 3 × $0.30 | $0.90 | $3.90 | $47 |
| One-off catch-up (lapsed sync, ~60 PRs at once) | $0.14 × 60 | $8.40 | $8.40 | $8.40 |

**Honest estimate:** **$5–$10/month** under steady state; **<$50/year** even with bursty weeks. The catch-up case is also cheap.

## Latency

| Stage | Dry mode | Real mode (per PR) |
| --- | --- | --- |
| upstream-watch | ~150ms (gh CLI) | ~150ms |
| classify | 1ms (instant) | ~5–15s (per PR via Pi/GPT-5) |
| translate | 1ms | ~30–60s (per PR via Claude Sonnet) |
| verify-parity (live wire_compat) | ~400ms | ~400ms |
| emit-pr | 1ms (drafted) | ~3s (gh pr create) |

Total per-PR real-mode latency: **~30–80 seconds**. Parallelizable up to `maxConcurrency` (default 4) for the translate step, so 4 PRs in a batch finish in roughly the same wall-clock time as one.

## Stop-loss recommendations

- **Hard budget cap.** Set `SMITHERS_PORT_PY_MAX_SPEND_USD=20` (env, not wired in v0 — TODO add a check in the runner) and refuse to start a run that would project to exceed it. Estimated as `len(prsToProcess) * 0.30` for a worst-case complex-PR batch.
- **Static-rule first.** The `staticClassification` helper short-circuits ~50% of PRs without an LLM call. Keep extending it (e.g., release-tag PRs, version-bump PRs) to grow the shortcut rate.
- **Reviewer pass conditional.** Only fire the reviewer Subflow when `translate.metrics.confidence < 80` to cut review cost in half on confident translations.
- **Failure quarantine.** A PR whose translation fails 3× should be auto-classified `needs-human-review` and emitted to a parking-lot label, not retried indefinitely.

## When real-mode is worth it

| Scenario | Recommendation |
| --- | --- |
| Pre-launch demo | Dry-mode only. Show the workflow ran the canonical 6-PR fixture in 3s. |
| Internal validation | Real-mode on a small slice (3-5 PRs). Cost <$1. |
| Steady-state Python parity | Real-mode weekly. <$1/week. |
| Recovering from a long lapse | Real-mode in one batch. <$10 one-off. |
| Continuous + auto-PR | Wire to `cron` daily. ~$5/month. Use a budget guard. |

## Hard numbers from this run

Today's dry-mode smoke (6 fixture PRs):
```json
{
  "prsConsidered": 6,
  "prsPorted": 6,
  "prsSkipped": 0,
  "parityHeld": true,
  "pullRequestsOpened": 0,
  "estimatedSpendMicrocents": 0,
  "summary": "Considered 6 PRs, ported 6, skipped 0. Parity held. PR status: drafted."
}
```

Projected real-mode equivalent: **~$0.84** for the same 6-PR fixture
(6 × $0.14 average), 3–5 minutes wall-clock. Comparable runs:

- One week's normal upstream activity: **~$0.14**.
- A month of normal upstream activity: **~$0.60**.
- The full 18-PR catch-up we already did manually this morning would have cost **~$2.52** real-mode and ~30 minutes wall-clock.

## Live real-mode numbers (2026-05-18)

Ran the 1-PR fixture (PR #130) against the live Anthropic API six
times today shaking out the override-mode metadata bug, the
prompt-threading bug, the MDX-fenced-code-block interpolation bug,
and a 10x rate-table error in `estimateCostMicrocents`. Final v6
numbers, using engine-recorded `TokenUsageReported` events (real
API counts, not the model's self-report):

| Metric | v6 (final) |
| --- | --- |
| Wall-clock | ~20s |
| Classify input tokens | 1,193 |
| Classify output tokens | 228 |
| Translate input tokens | 12,316 |
| Translate output tokens | 192 |
| Total tokens | 13,929 |
| `estimatedSpendMicrocents` | **46,827** |
| **Per-PR USD** | **$0.047** |

**Projection vs reality.** The per-PR model in
[Per-PR cost breakdown](#per-pr-cost-breakdown) assumed $0.14/PR
average. Real cost is **$0.047/PR** — ~3x cheaper than projected.
Three reasons:
- The translate response is short (model returns a concise JSON
  with reasoning, not a full file). 192 output tokens vs the 2,000
  in the model.
- No reviewer pass fired (skip path → no need to verify).
- No retry (the classifier got a clean structured-output response
  on attempt 1).

**Steady-state recalibration (real numbers):**

| Frequency assumption | Weekly | Monthly | Annual |
| --- | --- | --- | --- |
| Expected (1 PR/wk avg) | $0.05 | $0.20 | $2.40 |
| High activity (3 PR/wk) | $0.15 | $0.65 | $7.80 |
| Catch-up (60 PRs at once) | — | $2.82 | — |

**Layered-judgment observation.** PR #130's run produced the exact
safety property the workflow was designed for:

1. Classifier (title + files) said `port`, 85% confidence, citing
   bugs mentioned in the PR title.
2. Translator (full diff in prompt) read the actual changes and
   overruled the classifier: status `skipped`, notes captured the
   substantive reason ("TS-only packaging, tsup config, bun.lock —
   no Python equivalents").
3. Parity check (live wire_compat re-run) stayed green throughout.

That layered judgment — classifier proposes, translator with the
full diff disposes — is exactly the safety property a recursive
maintenance workflow needs, and it costs ~$0.05.

## Cost tracking implementation note

`estimatedSpendMicrocents` in the final output is computed from
engine-recorded `TokenUsageReported` events in the
`_smithers_events` table, not from the model's self-reported
`tokensUsed` field. The model's number is a guess; the events come
from the AI SDK's `result.usage.inputTokens` / `outputTokens` which
the Anthropic SDK populates from the API response. To get the same
numbers in a shell:

```sql
SELECT
  json_extract(payload_json, '$.nodeId')        AS node,
  json_extract(payload_json, '$.inputTokens')   AS in_tok,
  json_extract(payload_json, '$.outputTokens')  AS out_tok
FROM _smithers_events
WHERE run_id LIKE 'YOUR_RUN_ID%'
  AND type = 'TokenUsageReported';
```

The rates baked into `estimateCostMicrocents` are claude-sonnet-4-5
list pricing as of 2026-05: $3/MTok in, $15/MTok out. Update them
when the model changes or when the SDK starts reporting cache-read
discounts.
