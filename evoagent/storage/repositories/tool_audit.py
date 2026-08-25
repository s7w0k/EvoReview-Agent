"""Persistent tool-invocation audit repository (plan section 9.6)."""
from typing import Any, Dict, List

from ...tools.audit import AuditEntry
from .base import PersistentRepository


class ToolAuditRepository(PersistentRepository):
    """Persists every governed tool invocation (``tool_invocation_audit``)."""

    table = "tool_invocation_audit"

    def add(self, entry: AuditEntry) -> None:
        self.save(entry.invocation_id, entry.to_dict())

    def for_task(self, task_id: str) -> List[Dict[str, Any]]:
        return self._by("task_id", task_id)

    def by_agent(self, agent_id: str) -> List[Dict[str, Any]]:
        return self._by("agent_id", agent_id)


__all__ = ["ToolAuditRepository"]