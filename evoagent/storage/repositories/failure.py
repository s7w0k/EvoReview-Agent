"""Persistent failure-event repository (plan section 9.6)."""
from typing import Any, Dict, List

from ...recovery.failures import FailureEvent
from .base import PersistentRepository


class FailureRepository(PersistentRepository):
    """Persists classified failure events (``failure_events``)."""

    table = "failure_events"

    def add(self, event: FailureEvent) -> None:
        self.save(event.failure_id, event.to_dict())

    def for_task(self, task_id: str) -> List[Dict[str, Any]]:
        return self._by("task_id", task_id)

    def of_type(self, failure_type: str) -> List[Dict[str, Any]]:
        return self._by("failure_type", failure_type)


__all__ = ["FailureRepository"]