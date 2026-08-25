"""Persistent recovery-event repository (plan section 9.6)."""
from typing import Any, Dict, List, Optional

from ...storage.json_store import JSONFileStore
from .base import PersistentRepository


class RecoveryRepository(PersistentRepository):
    """Persists recovery plans / executions (``recovery_events``)."""

    table = "recovery_events"

    def add(self, recovery_id: str, event: Dict[str, Any]) -> None:
        self.save(recovery_id, event)

    def for_task(self, task_id: str) -> List[Dict[str, Any]]:
        return self._by("task_id", task_id)

    def by_action(self, action: str) -> List[Dict[str, Any]]:
        return self._by("recovery_action", action)


__all__ = ["RecoveryRepository"]