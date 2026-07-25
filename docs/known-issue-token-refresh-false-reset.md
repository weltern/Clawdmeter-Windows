# Fixed: a failed usage probe caused a false "limit reset" + repeat "approaching" alert

> Reported 2026-07-07 by observed behavior. Root cause confirmed 2026-07-09 against
> 22.5 days of `usage_history.jsonl` (5589 records, 2026-06-17 -> 2026-07-09) and
> fixed the same day. See "Confirmed against real data" and "Fix applied" below.

## Symptom (as reported)

Sometime around an OAuth access-token refresh, while a window's utilization is
close enough to a threshold to be notification-worthy:

1. Clawdmeter fires a **"limit has reset"** notification that isn't real — the
   limit didn't actually reset.
2. Once the refresh finishes and Clawdmeter can see the real (still-high)
   utilization again, it fires an **"approaching limit"** notification again,
   as if freshly crossing the threshold.

Net effect: one real near-limit state produces two spurious notifications
bracketing a token refresh.

## Suspected root cause

`_poll_once()` in `src/poller.py` (≈ lines 199-227) never checks the HTTP
response status before parsing it:

```python
resp = http.post(API_URL, headers=headers, json=API_BODY)
sample = sample_from_headers(resp.headers, now)
```

There is no `resp.raise_for_status()` and no `resp.status_code` check anywhere
in the file. httpx does **not** raise on a non-2xx response unless you ask it
to, so a `401` (token expired / rotated out from under this request — see
`src/token_refresh.py` module docstring: "the usage API returns 401" on an
expired token) is treated exactly like a `200`.

`sample_from_headers()` (poller.py ≈ lines 114-152) then runs against the 401
response's headers. A 401 won't carry the
`anthropic-ratelimit-unified-{5h,7d}-utilization` headers (auth failed before
rate-limit accounting), so every `hdr()` lookup falls back to its default and:

- `session_pct` / `weekly_pct` → **0**
- `status` → `"unknown"`
- `ok` → **hardcoded `True`** (poller.py line ~149 — `sample_from_headers`
  always returns `ok=True`; it has no failure path of its own)

That fabricated-but-"ok" 0% sample is exactly the shape both notifiers treat
as a real state to act on:

- `ResetNotifier.observe()` (`src/reset_notify.py` ≈ lines 68-79) only ignores
  samples where `s.ok` is `False`. This sample says `ok=True`, so it isn't
  filtered. Compared against the previous high-utilization OK sample, a drop
  to 0% is ≥ `SESSION_RESET_DROP`/`WEEKLY_RESET_DROP` (5 points) →
  **fires the false "reset" notification**, gated in only because the
  pre-drop sample was already above `NOTIFY_THRESHOLD` (75%) or `status`
  looked concerning — i.e. this only shows up when a real alert-worthy
  situation exists, matching what was reported.
- `ApproachingNotifier.observe()` (`src/approaching_notify.py` ≈ lines 67-90)
  also only filters on `s.ok`, so the same 0% sample re-arms
  `_warned[(axis, "thr")]` (utilization now reads well below `threshold -
  REARM_MARGIN`). When the *next* real poll succeeds after the refresh
  completes and reports the true (still ≥ threshold) utilization, it reads as
  a fresh upward crossing and **fires "approaching limit" again**.

So the actual bug isn't in either notifier's edge-triggering logic — both
correctly gate on `sample.ok`. It's that the sample source (`_poll_once` /
`sample_from_headers`) mislabels a failed (401) probe as a legitimate `ok=True`
0% reading, which fools both downstream consumers.

## Where a fix would go

`_poll_once()` needs to check `resp.status_code` (or call
`resp.raise_for_status()`) *before* handing `resp.headers` to
`sample_from_headers()`, and return an `ok=False` sample (same shape as the
existing `except httpx.HTTPError` branch, poller.py line ~219) for any non-2xx
response — not just transport-level exceptions. That keeps a genuine
auth-failure-during-refresh probe out of both `ResetNotifier` and
`ApproachingNotifier`, the same way a network error already is.

## Confirmed against real data (2026-07-09)

`usage_history.jsonl` persists a snapshot every ~5 min; `tokens_5h`/`tokens_7d`
are computed independently (a local transcript scan, not from headers), so a
0% header reading alongside substantial real `t5`/`t7` is a direct
contradiction, not a coincidence.

Scanned the full 22.5-day history and cross-checked every such contradiction
against the account's actual, fixed reset cadence (5h windows on a strict 5h
clock; the 7d window fixed at Thursdays 17:00 — both confirmed live via
`tools/probe_rate_headers.py`):

- **44 of 45** weekly-axis false-zero episodes, and **47 of 52** session-axis
  ones, fell at essentially random offsets from the real boundary — not
  clustered near it. Only one weekly episode (2026-06-25 17:02, a 2-minute
  offset) was a real reset.
- Off-cycle (i.e. bug-caused) false-zero time totaled **124.5 hours** on the
  weekly axis and **103.8 hours** on the session axis, out of 539.7 hours
  observed (~23% and ~19% respectively).
- 43 of the 44 off-cycle weekly episodes also saw `plan`/`extra_usage_used_usd`
  degrade (null/0.0) at some point in the episode — i.e. the K1 enrichment
  calls (`USAGE_URL`/`PROFILE_URL`) failed too, not just the probe.

This is a far larger/more frequent failure than a rare refresh-window race: the
plan/usd fields staying *correct* through most of a long episode (same bearer
token as the failing probe) points at the probe itself getting rejected
(most likely 429, once a window is at/over cap and this account's overage is
`out_of_credits`/disabled) rather than a wholesale expired-token 401 — though
a real 401 likely explains the later, shorter stretches where plan/usd also
degrade. The fix below doesn't need to distinguish which: it treats any
non-2xx uniformly.

One gap remains: no raw HTTP status code was captured mid-episode (the app
never logged one) — the case above is built entirely from the persisted
percentages/tokens and the reset-cadence cross-check, not a captured 401/429
response body.

## Fix applied (2026-07-09)

`_poll_once()` (`src/poller.py`) now calls `resp.raise_for_status()` right
after the probe POST, before handing `resp.headers` to `sample_from_headers()`.
`httpx.HTTPStatusError` is a subclass of `httpx.HTTPError`, so a non-2xx now
falls into the existing `except httpx.HTTPError` branch and returns the
already-correct `ok=False` shape — no changes needed in `sample_from_headers()`
or either notifier, since both already gated correctly on `sample.ok`.

Covered by `tests/test_poller_probe.py` (401 -> `ok=False`, 429 -> `ok=False`,
200 -> `ok=True` with headers parsed normally, via `httpx.MockTransport`). Full
suite: 213 passed.

## Source references

- `src/poller.py` — `_poll_once()`, `sample_from_headers()`
- `src/reset_notify.py` — `ResetNotifier.observe()`
- `src/approaching_notify.py` — `ApproachingNotifier.observe()`
- `src/token_refresh.py` — refresh mechanics, 401-on-expiry note in module docstring
