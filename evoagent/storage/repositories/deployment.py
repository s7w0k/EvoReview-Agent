"""Persistent policy deployment records (plan section 9.2)."""
from typing import Any, Dict, List, Optional

from ...storage.json_store import JSONFileStore
from .base import PersistentRepository


class DeploymentRepository(PersistentRepository):
    """Persists one ``PolicyDeploymentRow`` per deployment id."""

    table = "runtime_policy_deployments"

    def save_deployment(self, deployment_id: str, row: Dict[str, Any]) -> None:
        self.save(deployment_id, row)

    def all_deployments(self) -> List[Dict[str, Any]]:
        return self.all()

    def by_status(self, status: str) -> List[Dict[str, Any]]:
        return self._by("status", status)

    def by_policy(self, policy_id: str) -> List[Dict[str, Any]]:
        return self._by("policy_id", policy_id)

    def by_scope(self, tenant_id: str, repository: str,
                 risk_level: str) -> List[Dict[str, Any]]:
        return [
            item for item in self.all()
            if str(item.get("tenant_id", "")) == str(tenant_id)
            and str(item.get("repository", "")) == str(repository)
            and str(item.get("risk_level", "")) == str(risk_level)
        ]


__all__ = ["DeploymentRepository"]