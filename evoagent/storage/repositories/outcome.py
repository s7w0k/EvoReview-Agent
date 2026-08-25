"""Persistent production outcome log (plan section 8.3).

Outcomes carry attribution (which policy / deployment / lane produced them) and
must survive restarts so the regression monitor can replay them.  This is a thin
store-backed companion to the in-memory ``OutcomeStore``.
"""
from typing import Any, Dict, List, Optional

from ...storage.json_store import JSONFileStore
from .base import PersistentRepository

TABLE = "production_outcomes"


class OutcomeRepository(PersistentRepository):
    """Persists one outcome record per ``outcome_id``."""

    table = TABLE

    def save_outcome(self, outcome_id: str, record: Dict[str, Any]) -> None:
        self.save(outcome_id, record)

    def by_task(self, task_id: str) -> List[Dict[str, Any]]:
        return self._by("task_id", task_id)

    def of_kind(self, kind: str) -> List[Dict[str, Any]]:
        return self._by("kind", kind)

    def all_outcomes(self) -> List[Dict[str, Any]]:
        return self.all()


__all__ = ["OutcomeRepository", "TABLE"]