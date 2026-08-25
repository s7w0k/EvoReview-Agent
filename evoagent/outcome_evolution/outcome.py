"""Production Outcome model + attribution (plan section 13.1 / 13.2).

Every production review produces an ``Outcome`` describing what actually
happened (runtime, quality and safety signals) together with the exact versions
that were in force, so the outcome can be attributed to the prompt / rule /
procedure / runtime-policy / deployment-lane that produced it.
"""
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class OutcomeKind(str, Enum):
    # -- runtime (section 13.1) --
    TASK_SUCCESS = "task_success"
    TASK_FAILURE = "task_failure"
    # -- quality --
    FINDING_ACCEPTED = "finding_accepted"
    FINDING_REJECTED = "finding_rejected"
    FALSE_POSITIVE = "false_positive"
    FALSE_NEGATIVE = "false_negative"
    FIX_ACCEPTED = "fix_accepted"
    # -- safety --
    CRITICAL_MISS = "critical_miss"
    POLICY_VIOLATION = "policy_violation"
    SANDBOX_VIOLATION = "sandbox_violation"
    SIDE_EFFECT_INCIDENT = "side_effect_incident"


# Outcomes that are always safety-significant (never ignored by evolution).
SAFETY_OUTCOME_KINDS = {
    OutcomeKind.CRITICAL_MISS,
    OutcomeKind.POLICY_VIOLATION,
    OutcomeKind.SANDBOX_VIOLATION,
    OutcomeKind.SIDE_EFFECT_INCIDENT,
}

# Outcomes that should normally produce a useful experience.
POSITIVE_OUTCOME_KINDS = {
    OutcomeKind.TASK_SUCCESS,
    OutcomeKind.FINDING_ACCEPTED,
    OutcomeKind.FIX_ACCEPTED,
}


@dataclass
class OutcomeAttribution:
    """Which artifact versions produced this outcome (section 13.2)."""

    prompt_version: str = ""
    rule_skill_version: str = ""
    procedure_version: str = ""
    runtime_policy_version: str = ""
    deployment_lane: str = ""
    candidate_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RuntimeMetrics:
    latency_ms: float = 0.0
    cost: float = 0.0
    tool_calls: int = 0
    recovery_count: int = 0


@dataclass
class Outcome:
    """A single, attributed production outcome."""

    task_id: str
    kind: OutcomeKind
    tenant_id: str = "default"
    repository: str = ""
    risk_level: str = "low"
    outcome_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    attribution: OutcomeAttribution = field(default_factory=OutcomeAttribution)
    metrics: RuntimeMetrics = field(default_factory=RuntimeMetrics)
    finding: Optional[Dict[str, Any]] = None
    created_at: float = field(default_factory=time.time)

    @property
    def is_safety(self) -> bool:
        return self.kind in SAFETY_OUTCOME_KINDS

    @property
    def is_positive(self) -> bool:
        return self.kind in POSITIVE_OUTCOME_KINDS

    def signature(self) -> str:
        """Stable signature: tenant + repository + kind + finding rule/evidence.

        Used for duplicate-merge, confirmation counting and cooldown in the
        feedback-trust gate (section 13.4).
        """
        import hashlib

        finding = self.finding or {}
        payload = "|".join([
            self.tenant_id, self.repository, self.kind.value,
            str(finding.get("rule_id", "")),
            str(finding.get("evidence", ""))[:240],
        ])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["kind"] = self.kind.value
        return value

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "Outcome":
        data = dict(value)
        data["kind"] = OutcomeKind(data.get("kind"))
        data["attribution"] = OutcomeAttribution(**data.get("attribution", {}))
        data["metrics"] = RuntimeMetrics(**data.get("metrics", {}))
        return cls(**{key: data[key] for key in (
            "outcome_id", "task_id", "tenant_id", "repository", "risk_level",
            "kind", "attribution", "metrics", "finding", "created_at",
        ) if key in data})