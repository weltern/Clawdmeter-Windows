"""Persisted usage history for Clawdmeter — the data behind Stats trends.

Keeps two things from the poll stream:
  * an in-memory ring of recent snapshots (for live sparklines), and
  * a throttled append-only JSONL on disk (for spend/utilisation over days),
    so trends survive restarts.

Snapshots are compact and PII-free (utilisation %, window token totals, the
extra-usage spend, plan tier — never account identifiers). The disk file lives
in the per-user app-data dir and is pruned to a retention window on startup.
"""

from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import QStandardPaths

PERSIST_INTERVAL_DEFAULT = 300   # min seconds between disk writes (5-min granularity)
RING_MAX = 240                   # in-memory recent snapshots (~4h at the 60s poll)
RETENTION_DAYS = 90


def default_history_path() -> Path:
    """`%APPDATA%/Clawdmeter/usage_history.jsonl` (falls back to ~/.clawdmeter)."""
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    d = Path(base) if base else (Path.home() / ".clawdmeter")
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d / "usage_history.jsonl"


def snapshot(sample) -> dict:
    """Compact, PII-free record from a UsageSample."""
    return {
        "ts": round(sample.timestamp or time.time(), 1),
        "s": int(sample.session_pct),
        "w": int(sample.weekly_pct),
        "t5": int(sample.tokens_5h),
        "t7": int(sample.tokens_7d),
        "usd": float(sample.extra_usage_used_usd),
        "plan": sample.plan_tier,
    }


class UsageHistory:
    """Records poll snapshots to a ring + a throttled JSONL log."""

    def __init__(self, path: Path | None = None,
                 persist_interval: int = PERSIST_INTERVAL_DEFAULT,
                 retention_days: int = RETENTION_DAYS,
                 persist: bool = True) -> None:
        # persist=False keeps everything in-memory (used in --mock so synthetic
        # samples never pollute the real on-disk history).
        self._persist = persist
        self._path = Path(path) if path else (default_history_path() if persist else None)
        self._interval = persist_interval
        self._retention = retention_days
        self._last_persist = 0.0
        self.ring: deque[dict] = deque(maxlen=RING_MAX)
        if persist:
            self._prune()

    def record(self, sample) -> None:
        """Add a snapshot to the ring; append to disk if the throttle has elapsed.
        Ignores failed samples so a network blip can't poison the history."""
        if not getattr(sample, "ok", False):
            return
        snap = snapshot(sample)
        self.ring.append(snap)
        if self._persist and snap["ts"] - self._last_persist >= self._interval:
            self._last_persist = snap["ts"]
            self._append(snap)

    def load(self, since_ts: float = 0.0) -> list[dict]:
        """Persisted snapshots with ts >= since_ts, oldest first."""
        if not self._persist:
            return []
        out: list[dict] = []
        try:
            with open(self._path, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if rec.get("ts", 0) >= since_ts:
                        out.append(rec)
        except OSError:
            pass
        return out

    # -- internals ----------------------------------------------------------
    def _append(self, snap: dict) -> None:
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(snap) + "\n")
        except OSError:
            pass

    def _prune(self) -> None:
        """Drop snapshots older than the retention window. Cheap: only rewrites
        when the first (oldest) line is actually stale."""
        try:
            with open(self._path, encoding="utf-8") as f:
                first = f.readline()
        except OSError:
            return
        if not first:
            return
        try:
            oldest = json.loads(first).get("ts", 0)
        except ValueError:
            oldest = 0
        cutoff = time.time() - self._retention * 86400
        if oldest >= cutoff:
            return  # nothing stale; skip the rewrite
        kept = [r for r in self.load() if r.get("ts", 0) >= cutoff]
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                for r in kept:
                    f.write(json.dumps(r) + "\n")
        except OSError:
            pass
