"""Feedback trust gate for production outcomes (plan section 13.4).

A single outcome must NOT trigger evolution on its own.  Before a set of
outcomes is allowed to seed a candidate / experience, this gate enforces:

* ``min_confirmers`` -- the same outcome signature must be confirmed by at
  least that many independent tasks;
* ``trusted_ratio``  -- feedbacker accepted-ratio floor (reuses the existing
  ``feedback_trust`` helpers);
* duplicate merge     -- repeated signatures are merged; counts accumulate;
* cooldown            -- a signature recently actioned is skipped to prevent
  evolution thrash.
"""
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..feedback_trust import (
    downgraded_feedbacker,
    trusted_feedbacker_ids,
)
from .outcome import Outcome


@dataclass
class TrustConfig:
    min_confirmers: int = 1
    trusted_ratio: float = 0.0     # 0 (disabled) .. 1
    cooldown_seconds: float = 0.0  # 0 (disabled) = no cooldown


@dataclass
class TrustDecision:
    """Whether an outcome is trusted enough to influence evolution."""

    trusted: bool
    reasons: List[str] = field(default_factory=list)

    @property
    def rejected(self) -> bool:
        return not self.trusted


class OutcomeTrustGate:
    """Aggregates outcome confirmations and applies the trust rules."""

    def __init__(self, config: Optional[TrustConfig] = None):
        self._config = config or TrustConfig()
        # signature -> set of task_ids that produced it (confirmation counting).
        self._confirmations: Dict[str, set] = {}
        # signature -> last time it was acted on (cooldown).
        self._last_actioned: Dict[str, float] = {}
        self._feedbackers: List[Dict[str, str]] = []

    # -- recording ----------------------------------------------------------

    def record(self, outcome: Outcome) -> None:
        signature = outcome.signature()
        self._confirmations.setdefault(signature, set()).add(outcome.task_id)
        if outcome.finding and outcome.finding.get("feedbacker"):
            self._feedbackers.append({
                "feedbacker": str(outcome.finding["feedbacker"]),
                "category": "accepted" if outcome.is_positive else "rejected",
            })

    # -- evaluation ---------------------------------------------------------

    def evaluate(self, outcome: Outcome) -> TrustDecision:
        signature = outcome.signature()
        reasons: List[str] = []
        config = self._config

        confirmers = len(self._confirmations.get(signature, set()))
        if config.min_confirmers >= 1 and confirmers < config.min_confirmers:
            reasons.append(
                f"signature confirmed by {confirmers} task(s), "
                f"need >= {config.min_confirmers}")

        if config.cooldown_seconds > 0:
            last = self._last_actioned.get(signature)
            if last is not None and (time.time() - last) < config.cooldown_seconds:
                reasons.append("signature is within its cooldown window")

        trusted = trusted_feedbacker_ids(
            self._feedbackers, enabled=bool(config.trusted_ratio),
            min_ratio=config.trusted_ratio,
        ) if config.trusted_ratio > 0 else None
        if downgraded_feedbacker(outcome.finding or {}, trusted,
                                 enabled=bool(config.trusted_ratio)):
            reasons.append("feedbacker below the trusted accepted-ratio")

        return TrustDecision(trusted=not reasons, reasons=reasons)

    def actioned(self, outcome: Outcome) -> None:
        """Mark a signature as actioned to start/refresh its cooldown."""
        self._last_actioned[outcome.signature()] = time.time()