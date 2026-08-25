"""Persistent evolution lineage repository (plan section 9.5)."""
from typing import Any, Dict, List, Optional

from ...storage.json_store import JSONFileStore
from .base import PersistentRepository


class LineageRepository(PersistentRepository):
    """Persists evolution lineage graphs, attributions and outcomes."""

    table = "evolution_lineages"

    def save_lineage(self, lineage_id: str, lineage: Dict[str, Any]) -> None:
        self.save(lineage_id, lineage)

    def add_node(self, node_id: str, node: Dict[str, Any]) -> None:
        self.store.save("evolution_lineage_nodes", node_id, node)

    def node(self, node_id: str) -> Optional[Dict[str, Any]]:
        return self.store.get("evolution_lineage_nodes", node_id)

    def add_attribution(self, attribution_id: str, attribution: Dict[str, Any]) -> None:
        self.store.save("evolution_attributions", attribution_id, attribution)

    def add_outcome(self, outcome_id: str, outcome: Dict[str, Any]) -> None:
        self.store.save("evolution_outcomes", outcome_id, outcome)

    def outcomes(self) -> List[Dict[str, Any]]:
        return self.store.all("evolution_outcomes")


__all__ = ["LineageRepository"]