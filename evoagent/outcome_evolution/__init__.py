"""Production outcome -> experience (plan section 13).

Closes the final loop: every production outcome is attributed, converted into a
trusted experience, and appended as the ``OUTCOME`` layer of the evolution
lineage so the full ``Experience -> ... -> Deployment -> Outcome`` chain is
traceable.
"""
from .builder import (
    COST,
    FAILURE,
    NEGATIVE,
    POSITIVE,
    SAFETY,
    OutcomeExperience,
    OutcomeExperienceBuilder,
)
from .outcome import (
    POSITIVE_OUTCOME_KINDS,
    SAFETY_OUTCOME_KINDS,
    Outcome,
    OutcomeAttribution,
    OutcomeKind,
    RuntimeMetrics,
)
from .store import OutcomeRecord, OutcomeStore
from .trust import (
    OutcomeTrustGate,
    TrustConfig,
    TrustDecision,
)

__all__ = [
    "COST",
    "FAILURE",
    "NEGATIVE",
    "OUTCOME_KINDS",
    "POSITIVE",
    "POSITIVE_OUTCOME_KINDS",
    "SAFETY",
    "SAFETY_OUTCOME_KINDS",
    "Outcome",
    "OutcomeAttribution",
    "OutcomeExperience",
    "OutcomeExperienceBuilder",
    "OutcomeKind",
    "OutcomeRecord",
    "OutcomeStore",
    "OutcomeTrustGate",
    "RuntimeMetrics",
    "TrustConfig",
    "TrustDecision",
]

OUTCOME_KINDS = list(OutcomeKind)