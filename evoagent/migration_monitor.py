"""Continuous learning, migration and forgetting monitoring (closed-loop WP7).

Pure, deterministic analytics over per-version usage/evaluation records.  These
helpers produce the migration matrix, marginal-contribution evidence, stale
detection and forgetting-trend signals required by the plan.
"""
from typing import Any, Dict, List, Optional


def migration_matrix(records: List[Dict[str, Any]]) -> Dict[tuple, Dict]:
    """Build a ``(repository, language) -> {version: metric}`` matrix."""
    matrix: Dict[tuple, Dict] = {}
    for record in records:
        key = (record.get("repository") or "", record.get("language") or "")
        version = record.get("version")
        metric = record.get("metric")
        matrix.setdefault(key, {})[version] = metric
    return matrix


def marginal_contribution(
    with_experience: float, without_experience: float,
) -> Dict[str, Any]:
    """Whether an experience yields a positive marginal metric in an unseen repo."""
    delta = with_experience - without_experience
    return {"delta": round(delta, 4), "positive": delta > 0}


def is_stale(
    usage: Dict[str, Any],
    *,
    min_independent_tp: int = 0,
    inactive_days: int = 30,
) -> bool:
    """A version is stale when it has no independent contribution and is inactive."""
    if int(usage.get("independent_new_tp", 0)) > min_independent_tp:
        return False
    return int(usage.get("last_active_days_ago", 0)) >= inactive_days


def forgetting_trend(series: List[Dict[str, Any]]) -> List[str]:
    """Return domains whose metric declined for two consecutive windows."""
    if len(series) < 3:
        return []
    domains = set()
    for window in series:
        domains.update(window.keys())
    declining = set()
    for domain in domains:
        values = [window.get(domain) for window in series]
        for i in range(1, len(values) - 1):
            if (
                values[i - 1] is not None
                and values[i] is not None
                and values[i + 1] is not None
                and values[i] < values[i - 1]
                and values[i + 1] < values[i]
            ):
                declining.add(domain)
                break
    return sorted(declining)
