"""Read-only curator recommendations for evolved review skills.

The curator only reads version history and usage metrics and produces
advisory recommendations.  It holds no Store write methods, never changes a
skill's status and never calls activate/archive itself.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional


DUPLICATE = "duplicate"
TIGHTEN_TRIGGER = "tighten_trigger"
STALE_CANDIDATE = "stale_candidate"

_RECOMMENDATIONS = (DUPLICATE, TIGHTEN_TRIGGER, STALE_CANDIDATE)


class SkillCurator:
    """Generate read-only recommendations from versions and usage metrics."""

    def __init__(self, min_samples: int = 20, stale_days: int = 30):
        self.min_samples = int(min_samples)
        self.stale_days = int(stale_days)

    @staticmethod
    def _rule_identity(rule: Dict[str, Any]):
        return (
            str(rule.get("match", "")).strip(),
            tuple(sorted(rule.get("include_paths") or [])),
            tuple(sorted(rule.get("exclude_paths") or [])),
        )

    def _recommend_duplicates(self, store, tenant_id: str) -> List[Dict[str, Any]]:
        recommendations = []
        for version in store.list_active_skill_artifacts(tenant_id):
            rules = (version.get("artifact") or {}).get("rules", [])
            seen = {}
            for rule in rules:
                identity = self._rule_identity(rule)
                if identity in seen:
                    recommendations.append({
                        "type": DUPLICATE, "tenant_id": tenant_id,
                        "skill_name": version["skill_name"],
                        "version": version["version"],
                        "message": (
                            "rule %s duplicates rule %s (identical match and scope)"
                            % (rule.get("rule_id"), seen[identity])
                        ),
                        "rule_ids": [seen[identity], rule.get("rule_id")],
                    })
                else:
                    seen[identity] = rule.get("rule_id")
        return recommendations

    @staticmethod
    def _stamp(value) -> Optional[datetime]:
        if value is None:
            return None
        if hasattr(value, "isoformat"):
            return value
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

    def _days_since(self, value, now: datetime) -> float:
        stamp = self._stamp(value)
        if stamp is None:
            return float("inf")
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=now.tzinfo)
        return max(0.0, (now - stamp).total_seconds() / 86400.0)

    def _recommend_stale(self, store, tenant_id: str, now: datetime) -> List[Dict[str, Any]]:
        recommendations = []
        usage = {
            (item["skill_name"], int(item["version"])): item
            for item in store.list_skill_usage_stats(tenant_id)
        }
        for skill in store.list_skill_artifact_versions_for_tenant(tenant_id):
            key = (skill["skill_name"], int(skill["version"]))
            stats = usage.get(key)
            if stats is None:
                # No usage at all: flag only versions old enough to be stale.
                if self._days_since(skill.get("created_at"), now) > self.stale_days:
                    if skill.get("status") == "active":
                        message = "active skill has never been executed for more than %d days" % self.stale_days
                    else:
                        message = (
                            "skill candidate has no executions or independent contribution "
                            "for more than %d days" % self.stale_days
                        )
                    recommendations.append({
                        "type": STALE_CANDIDATE, "tenant_id": tenant_id,
                        "skill_name": skill["skill_name"],
                        "version": skill["version"],
                        "message": message,
                    })
                continue
            if self._days_since(stats.get("last_used_at"), now) > self.stale_days:
                recommendations.append({
                    "type": STALE_CANDIDATE, "tenant_id": tenant_id,
                    "skill_name": skill["skill_name"],
                    "version": skill["version"],
                    "message": "skill has not been used in more than %d days" % self.stale_days,
                })
        return recommendations

    def _recommend_tighten(self, store, tenant_id: str) -> List[Dict[str, Any]]:
        recommendations = []
        for stats in store.list_skill_usage_stats(tenant_id):
            proposed = int(stats.get("findings_proposed") or 0)
            false_positive = int(stats.get("false_positive_feedback") or 0)
            if (
                proposed > 0
                and false_positive >= self.min_samples
                and false_positive * 2 >= proposed
            ):
                recommendations.append({
                    "type": TIGHTEN_TRIGGER, "tenant_id": tenant_id,
                    "skill_name": stats["skill_name"],
                    "version": stats["version"],
                    "message": (
                        "%d false positive feedbacks out of %d proposed findings "
                        "reaches the trigger threshold"
                        % (false_positive, proposed)
                    ),
                    "false_positive_feedback": false_positive,
                    "findings_proposed": proposed,
                })
        return recommendations

    def recommend(self, store, tenant_id: str = "default") -> List[Dict[str, Any]]:
        """Compute recommendations for one tenant. Read-only."""
        from .store import utc_now

        now = self._stamp(utc_now()) or datetime.now()
        recommendations = []
        recommendations.extend(self._recommend_duplicates(store, tenant_id))
        recommendations.extend(self._recommend_tighten(store, tenant_id))
        recommendations.extend(self._recommend_stale(store, tenant_id, now))
        return recommendations
