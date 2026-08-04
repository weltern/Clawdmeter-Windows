# How Anthropic Calculates Overage (Claude Code / Max plan)

> Investigation notes, captured 2026-06-29 from live account data + Clawdmeter source.
> Author context: account was sitting at the top of the 5h window (rejected) during capture.

## TL;DR

- **Overage = a usage window crossing 100%.** Anthropic reports per-window utilization as a
  fraction and does **not** clamp it at `1.0`. When you exhaust a window and keep working on
  paid extra usage, the number climbs past `1.0` (e.g. `1.20` = 20% into overage).
- **Dollar cost of overage** is tracked separately, in `GET /api/oauth/usage` → `spend.used`.
- **The dedicated `…-overage-utilization` header is a red herring** — it tracks "% of your
  extra-usage *credit cap* used," which is `0.0`/`null` for uncapped (pay-as-you-go) accounts,
  so it **never** reflects an actual session/weekly overage.

## Live snapshot used for these notes (2026-06-29)

One real poll against the account (`poller._poll_once`):

| Signal | Value |
|---|---|
| 5h session utilization | **100%**, status **`rejected`**, reset in ~4 min |
| 7d weekly utilization | 39%, reset in ~5244 min (~3.6 days) |
| Extra usage | **enabled**, **uncapped (pay-as-you-go)** |
| Extra spend this month (`spend.used`) | **$21.46** |
| Plan tier | `default_claude_max_5x` |
| Per-model windows | `{'Sonnet': 0}` |

Interpretation: base Max-5x 5h allotment exhausted → the 1-token probe was `rejected`.
Real work continues on paid credit (uncapped), accumulating as dollars in `spend.used`.
The "5 mins left" both Clawdmeter and the desktop app showed was the countdown **to** the
5h reset — the session had **not** reset yet.

## The two data surfaces (same shared account counter)

### 1. Rate-limit headers — on every `POST api.anthropic.com/v1/messages`
This is what Clawdmeter's `poller.py` reads. Real captured sample (`ratelimit_log.jsonl`):

```json
"anthropic-ratelimit-unified-5h-utilization":      "0.35",   // session (5h), 0.0–1.0+ (NOT capped)
"anthropic-ratelimit-unified-5h-status":           "allowed",// allowed / allowed_warning / rejected
"anthropic-ratelimit-unified-5h-reset":            "1781492400", // epoch seconds
"anthropic-ratelimit-unified-7d-utilization":      "0.22",   // weekly (7d)
"anthropic-ratelimit-unified-7d-reset":            "1781816400",
"anthropic-ratelimit-unified-overage-utilization": "0.0",    // RED HERRING (credit-cap %, not overage)
"anthropic-ratelimit-unified-overage-reset":       "1782864000", // ~16 days out
"anthropic-ratelimit-unified-representative-claim":"five_hour",  // which window is binding
"anthropic-ratelimit-unified-fallback-percentage": "0.5"
```

Requires no special header beyond the OAuth bearer token (it rides normal message responses).

### 2. Usage endpoint — `GET api.anthropic.com/api/oauth/usage`
What the desktop/web **Settings → Usage** page uses; gives the **dollar/credit** side.
Requires the beta header `anthropic-beta: oauth-2025-04-20`.
Parsed by `poller.usage_fields_from_json`:

- `spend.used` → `amount_minor / 10**exponent` = **real extra-usage spend in dollars**
  (never read the minor-unit integer as dollars).
- `spend.limit` → monthly cap; **`null` = pay-as-you-go, no cap**.
- `spend.enabled` → whether extra usage is turned on.
- `limits[]` → per-model windows (`scope.model.display_name` + `percent`).
- `GET /api/oauth/profile` → `organization.rate_limit_tier` (e.g. `default_claude_max_5x`).

## How Clawdmeter derives overage (the correct method)

From `src/poller.py`:

- `pct()` (≈ lines 131-137): converts the fraction to a percent and is **deliberately not
  clamped** — a window in overage reports `> 1.0`, surfaced as `>100%`.
- `sample_from_headers()` (≈ lines 143-152): `session_pct` / `weekly_pct` come straight from the
  5h / 7d utilization headers.
- Comment block (≈ lines 139-142): overage is derived **per-window from 5h/7d crossing 100%**,
  explicitly **not** from the `unified-overage-*` bucket.

UI side:
- `dashboard.py` `apply_overage_bar()` restarts the bar red past 100% with an `OVERAGE` tag.
- `approaching_notify.py` (`OVERAGE_PCT = 100`): edge-triggered alert when a window crosses 100%.
- `dashboard.py:2599-2603`: shows `$spend.used` and either the cap or "pay-as-you-go · no monthly cap".

## The gotcha, stated plainly

There are **two different "overage" numbers** and only one is real:

1. ✅ **Window overflow** — `5h`/`7d` utilization > 100%, plus dollars in `spend.used`.
   This is your actual overage.
2. ❌ **`overage-utilization` header / `extra_usage.utilization`** — "% of your extra-usage
   **credit cap** used." `0.0`/`null` when there is no cap, so it is **useless** as an overage
   signal for pay-as-you-go accounts. Ignore it.

## Caveat on the local log

`ratelimit_log.jsonl` in the repo root had only **2 stale samples** (35% / 22%, overage `0.0`)
captured at an earlier idle time — it did **not** record the maxed-out (100% / rejected) state.
The live snapshot above came from an on-demand `poller._poll_once`, not the log. To capture a
real >100% crossing, run `tools/ratelimit_logger.py` while actually in overage.

## How to reproduce the live read

```bash
cd C:\Claude\ClonedRepos\Clawdmeter-Windows
./.venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'src'); import poller; \
t=poller.read_token(); s=poller._poll_once(t); print(s)"
```

(`read_token()` pulls the OAuth bearer from `~/.claude/.credentials.json`; the probe uses
`max_tokens: 1` to stay cheap.)

## Source references

- `src/poller.py` — `UsageSample`, `pct()`, `sample_from_headers()`, `usage_fields_from_json()`
- `src/approaching_notify.py` — `OVERAGE_PCT`, edge-triggered crossing detection
- `src/dashboard.py` — `apply_overage_bar()`, spend/cap display, "7-day window already in overage"
- `src/session_shelf.py` — overage bar rendering (`_BAR_OVERAGE`)
- `tools/ratelimit_logger.py` — header logger that catches a >100% crossing
- `ratelimit_log.jsonl` — raw captured header samples
