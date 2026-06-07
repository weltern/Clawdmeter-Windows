"""Decide when a usage-limit reset is worth notifying about.

This is the pure, Qt-free core behind the "your limit has reset — you can
resume" notification. It watches consecutive UsageSamples and fires an
edge-triggered decision when a limit resets, but only when the user actually
cared: their utilization was high (>= NOTIFY_THRESHOLD) or they were already
being warned/throttled just before the reset. All Qt side effects (toast,
sound, window-pop, tray flash) live in the dashboard; this module only decides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # type-only — keeps this module free of Qt/httpx at runtime
    from poller import UsageSample

# A reset shows up as a sharp drop in utilization between two OK samples.
# Mirrors mood.SESSION_RESET_DROP so the two detectors agree on what a reset is.
SESSION_RESET_DROP = 5.0
WEEKLY_RESET_DROP = 5.0
# Only notify if, just before the reset, the user was at least this busy.
NOTIFY_THRESHOLD = 75


def _status_concerning(status: str) -> bool:
    """True if the status string signals throttling/warning (matches the badge)."""
    s = (status or "").lower()
    return "reject" in s or "block" in s or "warn" in s


@dataclass(frozen=True)
class ResetDecision:
    session_reset: bool = False   # a session (5h) reset was detected
    weekly_reset: bool = False    # a weekly (7d) reset was detected
    notify: bool = False          # at least one detected reset passed gating
    reasons: tuple[str, ...] = field(default_factory=tuple)  # gated axes

    @property
    def any_reset(self) -> bool:
        return self.session_reset or self.weekly_reset


class ResetNotifier:
    """Edge-triggered reset detector with pre-reset gating.

    Feed every UsageSample to observe(); it returns a ResetDecision describing
    whether a reset just occurred and whether it should be surfaced. State only
    advances on OK samples, so transient errors never read as a reset.
    """

    def __init__(
        self,
        session_drop: float = SESSION_RESET_DROP,
        weekly_drop: float = WEEKLY_RESET_DROP,
        threshold: int = NOTIFY_THRESHOLD,
    ) -> None:
        self._session_drop = session_drop
        self._weekly_drop = weekly_drop
        self._threshold = threshold
        self._prev: UsageSample | None = None  # last OK sample only

    def observe(self, s: UsageSample) -> ResetDecision:
        # Error / no-token samples carry pct=0; treating them as state would
        # fake a huge drop (and then a huge rise on recovery). Ignore them
        # entirely without disturbing the baseline.
        if not s.ok:
            return ResetDecision()

        prev = self._prev
        self._prev = s  # advance state; detection compares adjacent OK samples
        if prev is None:  # first OK sample: no baseline to compare against
            return ResetDecision()

        session_reset = (prev.session_pct - s.session_pct) >= self._session_drop
        weekly_reset = (prev.weekly_pct - s.weekly_pct) >= self._weekly_drop
        if not (session_reset or weekly_reset):
            return ResetDecision()

        # Gate each axis on the pre-reset sample: was the user near/at the limit?
        concerning = _status_concerning(prev.status)
        reasons: list[str] = []
        if session_reset and (prev.session_pct >= self._threshold or concerning):
            reasons.append("session")
        if weekly_reset and (prev.weekly_pct >= self._threshold or concerning):
            reasons.append("weekly")

        return ResetDecision(
            session_reset=session_reset,
            weekly_reset=weekly_reset,
            notify=bool(reasons),
            reasons=tuple(reasons),
        )
