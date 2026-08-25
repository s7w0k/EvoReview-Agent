"""Persistent policy exposure log (plan section 6).

Each task's routing decision is written here so production metrics stay
attributable across restarts.  The natural key is ``task_id:deployment_id`` so a
retried task never pollutes the exposure metrics twice (unique constraint).
"""
from typing import Any, Dict, List

from .base import PersistentRepository

TABLE = "policy_exposures"


class PolicyExposureRepository(PersistentRepository):
    """Persists one exposure record per ``(task_id, deployment_id)``."""

    table = TABLE

    def add(self, record: Any) -> None:
        data = record.to_dict() if hasattr(record, "to_dict") else record
        self.save(self._key(data["task_id"], data["deployment_id"]), data)

    def for_deployment(self, deployment_id: str) -> List[Dict[str, Any]]:
        return self._by("deployment_id", deployment_id)

    def for_task(self, task_id: str) -> List[Dict[str, Any]]:
        return self._by("task_id", task_id)

    def all_exposures(self) -> List[Dict[str, Any]]:
        return self.all()

    @staticmethod
    def _key(task_id: str, deployment_id: str) -> str:
        return f"{task_id}:{deployment_id}"


__all__ = ["PolicyExposureRepository", "TABLE"]