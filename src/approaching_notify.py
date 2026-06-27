"""Decide when usage is *approaching* (or has crossed) a limit.

The Qt-free core behind the "you're nearing your limit" and "you've crossed
into overage" alerts. It watches consecutive UsageSamples and fires
edge-triggered events when the session (5h) or weekly (7d) utilization crosses
a configured threshold upward, and again when it crosses 100% into paid credits.

Edge-triggering is the whole point: each axis warns *once* per cycle and re-arms
only after utilization falls back below the level (which a reset does sharply),
so an alert can't repeat on every poll while you sit above the threshold. A
small re-arm margin keeps noise around the boundary from re-firing it.

All Qt side effects (toast, sound, push) live in the dashboard; this module
only decides, mirroring reset_notify.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # type-only — keeps this module free of Qt/httpx at runtime
    from poller import UsageSample

# How far below a level utilization must fall before that level can fire again.
# Guards against re-alerting when the number jitters around the boundary.
REARM_MARGIN = 5
OVERAGE_PCT = 100


@dataclass(frozen=True)
class LimitEvent:
    window: str    # "Session (5h)" / "Weekly (7d)"
    kind: str      # "approaching" / "overage"
    pct: int       # utilization at the crossing
    threshold: int # the level crossed (the configured % or 100 for overage)


class ApproachingNotifier:
    """Edge-triggered threshold/overage detector for the two usage windows.

    Feed every UsageSample to observe() along with the live settings; it returns
    the list of LimitEvents that just fired (usually empty). State advances only
    on OK samples. The first OK sample primes the baseline silently, so launching
    the app while already above a threshold doesn't nag — only an actual upward
    crossing during a running session alerts.
    """

    def __init__(self, rearm_margin: int = REARM_MARGIN) -> None:
        self._margin = rearm_margin
        self._primed = False
        # (axis, level) -> already-warned-this-cycle. level: "thr" | "ovr".
        self._warned: dict[tuple[str, str], bool] = {
            ("session", "thr"): False, ("session", "ovr"): False,
            ("weekly", "thr"): False, ("weekly", "ovr"): False,
        }

    def observe(
        self,
        s: UsageSample,
        *,
        enabled: bool,
        session_threshold: int,
        weekly_threshold: int,
        overage_enabled: bool,
    ) -> list[LimitEvent]:
        if not getattr(s, "ok", False):
            return []  # ignore error/no-token samples; don't disturb state

        axes = (
            ("session", "Session (5h)", int(s.session_pct), int(session_threshold)),
            ("weekly", "Weekly (7d)", int(s.weekly_pct), int(weekly_threshold)),
        )

        # First OK sample: seed "already warned" from the current level so we
        # don't fire for a threshold that was already exceeded before launch.
        if not self._primed:
            self._primed = True
            for axis, _label, pct, thr in axes:
                self._warned[(axis, "thr")] = pct >= thr
                self._warned[(axis, "ovr")] = pct >= OVERAGE_PCT
            return []

        events: list[LimitEvent] = []
        for axis, label, pct, thr in axes:
            # Re-arm when utilization drops clear of a level (covers resets).
            if pct < thr - self._margin:
                self._warned[(axis, "thr")] = False
            if pct < OVERAGE_PCT - self._margin:
                self._warned[(axis, "ovr")] = False

            if not enabled:
                continue

            # Approaching: at/above the threshold but not yet into overage.
            if thr <= pct < OVERAGE_PCT and not self._warned[(axis, "thr")]:
                self._warned[(axis, "thr")] = True
                events.append(LimitEvent(label, "approaching", pct, thr))

            # Overage: crossed 100% onto paid credits.
            if overage_enabled and pct >= OVERAGE_PCT and not self._warned[(axis, "ovr")]:
                self._warned[(axis, "ovr")] = True
                # A jump straight past the threshold into overage shouldn't also
                # queue an approaching alert for the same cycle.
                self._warned[(axis, "thr")] = True
                events.append(LimitEvent(label, "overage", pct, OVERAGE_PCT))

        return events
