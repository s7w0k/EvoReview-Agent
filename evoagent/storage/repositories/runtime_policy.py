"""Persistent runtime policy repository (plan section 9.2)."""
from typing import Any, Dict, List, Optional

from ...storage.json_store import JSONFileStore
from .base import PersistentRepository

TABLE = "runtime_policy_versions"


class PersistedRuntimePolicyRepository(PersistentRepository):
    """Stores serialized ``ExecutionPolicy`` versions by policy_id."""

    table = TABLE

    def __init__(self, store: JSONFileStore):
        super().__init__(store)
        self._overrides_table = "runtime_policy_overrides"

    def save_policy(self, policy_id: str, version: int, content: Dict[str, Any],
                    *, risk_level: str = "", parent_version: Optional[int] = None,
                    status: str = "ACTIVE", hypothesis_id: str = "",
                    tenant_id: str = "default") -> None:
        self.save(policy_id, {
            "tenant_id": tenant_id, "policy_id": policy_id,
            "risk_level": risk_level, "version": version,
            "parent_version": parent_version, "content": content,
            "status": status, "hypothesis_id": hypothesis_id,
        })

    def latest(self, policy_id: str) -> Optional[Dict[str, Any]]:
        return self.record(policy_id)

    def active_baseline_policy(self, risk_level: str):
        """Return the persisted ``baseline-{risk_level}`` policy if present.

        Baselines are bootstrapped once under a stable id so a service restart
        restores the same baseline object instead of creating a newer version.
        """
        from ...policy.codec import policy_from_dict

        row = self.record(f"baseline-{risk_level}")
        if row is None or not row.get("content"):
            return None
        try:
            return policy_from_dict(row["content"])
        except Exception:
            return None

    def version(self, policy_id: str, version: int) -> Optional[Dict[str, Any]]:
        record = self.record(policy_id)
        if record and int(record.get("version", 0)) == version:
            return record
        return None

    def save_override(self, policy_id: str, override: Dict[str, Any]) -> None:
        self.store.save(self._overrides_table, policy_id, {
            "policy_id": policy_id, "override": override,
        })

    def overrides(self) -> List[Dict[str, Any]]:
        return self.store.all(self._overrides_table)


__all__ = ["PersistedRuntimePolicyRepository"]