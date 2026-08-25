"""Persistent evolution-budget usage repository (plan section 9.6)."""
from typing import Any, Dict, List, Optional

from ...storage.json_store import JSONFileStore
from .base import PersistentRepository


class EvolutionBudgetRepository(PersistentRepository):
    """Persists evolution budget usage over time (``evolution_budget_usage``)."""

    table = "evolution_budget_usage"

    def add_usage(self, usage_id: str, entry: Dict[str, Any]) -> None:
        self.save(usage_id, entry)

    def for_tenant(self, tenant_id: str) -> List[Dict[str, Any]]:
        return self._by("tenant_id", tenant_id)

    def total_spent(self, budget_name: str = "") -> float:
        total = 0.0
        for item in self.all():
            if budget_name and item.get("budget_name") != budget_name:
                continue
            total += float(item.get("cost", 0.0))
        return total


__all__ = ["EvolutionBudgetRepository"]