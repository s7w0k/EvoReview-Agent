"""Deterministic canary and shadow assignment with automatic rollback."""
import hashlib
from typing import Dict, Optional

from . import candidate_lifecycle as lifecycle
from . import canary
from . import skill_lifecycle


class ReleaseManager:
    def __init__(self, store):
        self.store = store

    def advance_canary(
        self, tenant_id: str, skill_name: str,
        tasks_since_stage: int, gates_passed: bool,
    ) -> bool:
        """Advance a staged canary to its next traffic level (never time-based)."""
        deployment = self.store.get_deployment(tenant_id, skill_name)
        if not deployment or deployment.get("status") != "running":
            return False
        if deployment.get("paused_by"):
            return False
        if not canary.should_advance(
            int(deployment.get("canary_percent", 0)), tasks_since_stage, gates_passed
        ):
            return False
        stage = canary.next_stage(int(deployment.get("canary_percent", 0)))
        percent = stage[0]
        updates = {"canary_percent": percent}
        if percent >= 100:
            updates["status"] = "active"
        self.store.update_deployment(tenant_id, skill_name, **updates)
        return True

    def pause_canary(self, tenant_id: str, skill_name: str, by: str) -> bool:
        deployment = self.store.get_deployment(tenant_id, skill_name)
        if not deployment:
            return False
        self.store.update_deployment(tenant_id, skill_name, paused_by=by)
        return True

    def resume_canary(self, tenant_id: str, skill_name: str) -> bool:
        deployment = self.store.get_deployment(tenant_id, skill_name)
        if not deployment:
            return False
        self.store.update_deployment(tenant_id, skill_name, paused_by=None)
        return True

    def rollback(
        self, tenant_id: str, skill_name: str, reason: str,
        metrics_snapshot: Optional[dict] = None,
    ) -> Optional[dict]:
        """Atomic rollback: zero traffic, record reason/snapshot, raise alert."""
        result = self.store.rollback_deployment(
            tenant_id, skill_name, reason, metrics_snapshot)
        if result:
            self.store.create_alert(
                tenant_id, "rollback:%s" % skill_name, "critical",
                "Candidate %s was rolled back: %s" % (skill_name, reason),
            )
        return result

    def _version_status(self, tenant_id: str, skill_name: str, version: int) -> Optional[str]:
        for item in self.store.list_skill_artifact_versions(skill_name, tenant_id):
            if int(item.get("version")) == int(version):
                return item.get("status")
        return None

    def promote_candidate(
        self, tenant_id: str, skill_name: str, version: int,
        actor: str = lifecycle.ACTOR_DEPLOYMENT,
    ) -> bool:
        """Deployment-controller promotion: validated -> shadow -> canary -> active."""
        current = self._version_status(tenant_id, skill_name, version)
        if current is None:
            return False
        target = {
            skill_lifecycle.VALIDATED: skill_lifecycle.SHADOW,
            skill_lifecycle.SHADOW: skill_lifecycle.CANARY,
            skill_lifecycle.CANARY: skill_lifecycle.ACTIVE,
        }.get(current)
        if target is None:
            return False
        if not lifecycle.permitted(actor, current, target):
            return False
        if target == skill_lifecycle.ACTIVE:
            return self.store.activate_skill_artifact(
                skill_name, version, tenant_id, actor=actor, reason="deployment promotion",
            )
        return self.store.transition_skill_artifact(
            tenant_id, skill_name, version, target, actor, "deployment promotion",
        )

    def rollback_candidate(
        self, tenant_id: str, skill_name: str, version: int,
        actor: str = lifecycle.ACTOR_ADMIN,
    ) -> bool:
        """Admin / rollback-policy rollback: shadow/canary/active -> rolled_back."""
        current = self._version_status(tenant_id, skill_name, version)
        if current is None:
            return False
        if not lifecycle.permitted(actor, current, skill_lifecycle.ROLLED_BACK):
            return False
        return self.store.transition_skill_artifact(
            tenant_id, skill_name, version, skill_lifecycle.ROLLED_BACK, actor, "rollback",
        )

    def configure(self, tenant_id: str, skill_name: str, config: Dict[str, object]) -> dict:
        canary = int(config.get("canary_percent", 0))
        shadow = int(config.get("shadow_percent", 0))
        if not 0 <= canary <= 100 or not 0 <= shadow <= 100:
            raise ValueError("canary_percent and shadow_percent must be between 0 and 100")
        if config.get("candidate_version") is None:
            raise ValueError("candidate_version is required")
        self.store.save_deployment(tenant_id, skill_name, config)
        return self.store.get_deployment(tenant_id, skill_name)

    def assignment(self, tenant_id: str, skill_name: str, key: str) -> Dict[str, object]:
        deployment = self.store.get_deployment(tenant_id, skill_name)
        if not deployment or deployment["status"] != "running":
            return {"lane": "stable", "shadow": False, "deployment": None}
        bucket = int(hashlib.sha256(
            ("%s:%s:%s" % (tenant_id, skill_name, key)).encode("utf-8")
        ).hexdigest()[:8], 16) % 100
        return {
            "lane": "canary" if bucket < deployment["canary_percent"] else "stable",
            "shadow": bucket < deployment["shadow_percent"],
            "deployment": deployment,
        }

    def observe(
        self, tenant_id: str, skill_name: str, failed: bool,
        lane: str = "canary",
    ) -> Optional[dict]:
        if lane != "canary":
            return self.store.get_deployment(tenant_id, skill_name)
        result = self.store.record_deployment_result(tenant_id, skill_name, failed)
        if result and result["status"] == "rolled_back":
            self.store.create_alert(
                tenant_id, "rollout:%s" % skill_name, "critical",
                "Canary %s was automatically rolled back after exceeding its error budget." % skill_name,
            )
        return result

    def observe_shadow(
        self, tenant_id: str, skill_name: str, task_id: str, lane: str,
        primary: Dict[str, object], candidate: Optional[Dict[str, object]],
        candidate_failed: bool = False,
        **observation_fields,
    ) -> Optional[dict]:
        primary_keys = set(primary.get("finding_keys", []))
        candidate_keys = set((candidate or {}).get("finding_keys", []))
        union = primary_keys | candidate_keys
        disagreement = len(primary_keys ^ candidate_keys) / len(union) if union else 0.0
        result = self.store.record_shadow_observation(
            tenant_id, skill_name, task_id, lane, primary, candidate,
            disagreement, candidate_failed, **observation_fields,
        )
        if result and result["status"] == "promoted":
            self.store.create_alert(
                tenant_id, "rollout-promoted:%s" % skill_name, "info",
                "Candidate %s was automatically promoted after shadow verification." % skill_name,
            )
        return result
