"""Feedback trust and confirmation helpers (Work Package 9).

Two deterministic guards, both disabled by default so the pre-WP9 behavior is
preserved exactly:

- min_confirmers: a missed_issue may only feed a candidate once the same
  (rule_id, evidence) signature has been confirmed by that many independent
  tasks (reuses the Experience corroborate counting idea on the main path).
- trust: when enabled, feedback from users whose historical "accepted" ratio
  is below the configured threshold is downgraded and never directly turns
  into a candidate.
"""
from typing import Any, Dict, List, Optional


def missed_issue_signature(case: Dict[str, Any]) -> tuple:
    """Stable signature for one missed-issue failure case."""
    finding = (case.get("payload") or {}).get("finding") or {}
    return (
        str(finding.get("rule_id", "")).strip().upper(),
        str(finding.get("evidence", "")).strip(),
    )


def confirmed_missed_issue_keys(
    failures: List[dict], min_confirmers: int,
) -> Optional[set]:
    """Keys of missed_issue signatures confirmed by >= min_confirmers tasks.

    Returns None when no confirmation threshold is in effect (min_confirmers<=1)
    so callers can keep their existing fast path.
    """
    if min_confirmers <= 1:
        return None
    groups: Dict[tuple, set] = {}
    for case in failures:
        if case.get("category") != "missed_issue":
            continue
        groups.setdefault(missed_issue_signature(case), set()).add(case["task_id"])
    return {
        key for key, tasks in groups.items() if len(tasks) >= min_confirmers
    }


def trusted_feedbacker_ids(
    failures: List[dict], enabled: bool, min_ratio: float,
) -> Optional[set]:
    """Feedbackers whose accepted ratio is at or above the trust threshold.

    Returns None when trust is disabled (no filtering).  Feedbackers without
    any accepted feedback evaluate to ratio 0.0 and are dropped.
    """
    if not enabled:
        return None
    stats: Dict[str, Dict[str, int]] = {}
    for case in failures:
        feedbacker = str((case.get("payload") or {}).get("feedbacker", "")).strip()
        if not feedbacker:
            continue
        bucket = stats.setdefault(feedbacker, {"accepted": 0, "total": 0})
        bucket["total"] += 1
        if case.get("category") == "accepted":
            bucket["accepted"] += 1
    return {
        feedbacker for feedbacker, bucket in stats.items()
        if bucket["total"]
        and (bucket["accepted"] / bucket["total"]) >= min_ratio
    }


def downgraded_feedbacker(
    case: Dict[str, Any], trusted: Optional[set], enabled: bool,
) -> bool:
    """True when a case comes from a low-trust feedbacker and must be downgraded."""
    if not enabled or trusted is None:
        return False
    feedbacker = str((case.get("payload") or {}).get("feedbacker", "")).strip()
    if not feedbacker:
        # No identity to judge: keep the signal (never guessed as untrusted).
        return False
    return feedbacker not in trusted
