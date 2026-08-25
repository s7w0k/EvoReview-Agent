"""Outcome -> Experience builder (plan section 13.3).

Converts an attributed production ``Outcome`` into a ready-to-persist
experience of one of five kinds: ``positive``, ``negative``, ``failure``,
``cost`` or ``safety``.  The mapping is deterministic; a safety outcome always
yields a ``safety`` experience so it is never down-weighted.
"""
from dataclasses import dataclass, field
from typing import Any, Dict

from .outcome import Outcome

# Experience kinds produced by this builder (section 13.3).
POSITIVE = "positive"
NEGATIVE = "negative"
FAILURE = "failure"
COST = "cost"
SAFETY = "safety"


@dataclass
class OutcomeExperience:
    """A single experience derived from one production outcome."""

    outcome_id: str
    task_id: str
    experience_type: str
    source_type: str = "outcome"
    fingerprint: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "outcome_id": self.outcome_id,
            "task_id": self.task_id,
            "experience_type": self.experience_type,
            "source_type": self.source_type,
            "fingerprint": self.fingerprint,
            "confidence": self.confidence,
            "payload": dict(self.payload),
        }


def _classify(outcome: Outcome) -> tuple:
    """Return ``(experience_type, confidence)`` for an outcome kind."""
    kind = outcome.kind
    if kind.value in (
        "task_success", "finding_accepted", "fix_accepted"):
        return POSITIVE, 0.9
    if kind.value == "task_failure":
        return FAILURE, 0.7
    if kind.value == "finding_rejected":
        return NEGATIVE, 0.6
    if kind.value == "false_positive":
        return NEGATIVE, 0.7
    if kind.value == "false_negative":
        return NEGATIVE, 0.85   # a miss is a strong learning signal
    if kind in ("critical_miss", "policy_violation", "sandbox_violation",
                "side_effect_incident"):
        return SAFETY, 1.0
    return NEGATIVE, 0.5


class OutcomeExperienceBuilder:
    """Deterministically turns outcomes into experiences."""

    def build(self, outcome: Outcome) -> OutcomeExperience:
        experience_type, confidence = _classify(outcome)
        payload = {
            "risk_level": outcome.risk_level,
            "deployment_lane": outcome.attribution.deployment_lane,
            "candidate_id": outcome.attribution.candidate_id,
            "runtime_policy_version":
                outcome.attribution.runtime_policy_version,
        }
        if outcome.finding:
            payload["finding"] = outcome.finding
        return OutcomeExperience(
            outcome_id=outcome.outcome_id,
            task_id=outcome.task_id,
            experience_type=experience_type,
            fingerprint=outcome.signature(),
            payload=payload,
            confidence=confidence,
        )