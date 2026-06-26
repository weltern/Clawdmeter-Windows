r"""Rate-limit header logger — capture what Anthropic's unified rate-limit
headers actually do when a 5h or 7d window crosses 100% into overage.

Run this during a heavy session (when you expect to hit a cap). Each poll it
appends one JSON line of every `anthropic-ratelimit-unified-*` header to
ratelimit_log.jsonl and prints a live readout so you can watch the numbers
cross 100%. Response headers only — the OAuth token is never written or printed.

Usage (from the repo root):
    .\.venv\Scripts\python.exe tools\ratelimit_logger.py            # 60s interval
    .\.venv\Scripts\python.exe tools\ratelimit_logger.py --interval 30
    .\.venv\Scripts\python.exe tools\ratelimit_logger.py --once     # single sample

Each poll is one tiny (1-token) billed request, same as the app's normal probe.
Ctrl+C to stop.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx  # noqa: E402
from poller import read_token, API_URL, API_HEADERS_TEMPLATE, API_BODY  # noqa: E402

LOG_PATH = Path(__file__).resolve().parent.parent / "ratelimit_log.jsonl"


def poll_once() -> dict | None:
    token = read_token()
    if not token:
        print("NO TOKEN — cannot probe", file=sys.stderr)
        return None
    h = dict(API_HEADERS_TEMPLATE)
    h["Authorization"] = f"Bearer {token}"
    now = time.time()
    try:
        with httpx.Client(timeout=20.0) as c:
            r = c.post(API_URL, headers=h, json=API_BODY)
    except httpx.HTTPError as exc:
        return {"ts": now, "error": str(exc)}
    rl = {k: r.headers[k] for k in r.headers if "ratelimit" in k.lower()}
    return {"ts": now, "http_status": r.status_code, "headers": rl}


def fmt_reset(headers: dict, key: str, now: float) -> str:
    raw = headers.get(key)
    if raw is None:
        return "—"
    try:
        dt = float(raw) - now
        return f"{dt/3600:.1f}h" if dt < 36 * 3600 else f"{dt/86400:.1f}d"
    except ValueError:
        return raw


def live_line(sample: dict) -> str:
    if "error" in sample:
        return f"ERROR {sample['error']}"
    h, now = sample["headers"], sample["ts"]

    def util(key: str) -> str:
        v = h.get(key)
        try:
            return f"{float(v) * 100:.1f}%"
        except (TypeError, ValueError):
            return "—"

    p = "anthropic-ratelimit-unified-"
    return (
        f"5h {util(p+'5h-utilization'):>7} [{h.get(p+'5h-status','—'):^16}] "
        f"reset {fmt_reset(h, p+'5h-reset', now):>6}   |   "
        f"7d {util(p+'7d-utilization'):>7} [{h.get(p+'7d-status','—'):^16}] "
        f"reset {fmt_reset(h, p+'7d-reset', now):>6}   |   "
        f"overage {util(p+'overage-utilization'):>7} "
        f"[{h.get(p+'overage-status','—'):^16}] reset {fmt_reset(h, p+'overage-reset', now):>6}   |   "
        f"claim={h.get(p+'representative-claim','—')}  "
        f"unified-status={h.get(p+'status','—')}"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=60, help="seconds between polls")
    ap.add_argument("--once", action="store_true", help="single sample then exit")
    args = ap.parse_args()

    print(f"Logging unified rate-limit headers -> {LOG_PATH}")
    print(f"{'time':^8}  live readout (Ctrl+C to stop)\n")
    try:
        while True:
            sample = poll_once()
            if sample is None:
                return
            with LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(sample) + "\n")
            stamp = time.strftime("%H:%M:%S", time.localtime(sample["ts"]))
            print(f"{stamp}  {live_line(sample)}")
            if args.once:
                return
            time.sleep(max(5, args.interval))
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
