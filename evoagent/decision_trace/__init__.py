"""Decision-trace observability (plan section 12).

Upgrades plain logging into an explainable decision trace: an ordered record of
policy-resolution and agent decisions through which the harness can answer
*"why did the agent decide this?"*, and a diff that shows how a candidate
changed behaviour relative to its baseline.
"""
from .trace import (
    DecisionDiff,
    DecisionTrace,
    TraceEvent,
    TraceLogger,
)

__all__ = [
    "DecisionDiff",
    "DecisionTrace",
    "TraceEvent",
    "TraceLogger",
]