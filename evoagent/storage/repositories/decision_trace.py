"""Persistent decision-trace repository (plan section 9.6 / Phase 5).

Phase 4 kept traces in an in-memory repository.  This durable variant persists
every event to the ``decision_trace_events`` table so a worker restart can
reconstruct the trace and replay can re-consume it in order.
"""
from typing import Any, Dict, List, Optional

from ...decision_trace.trace import DecisionTrace, TraceEvent
from ...storage.json_store import JSONFileStore
from .base import PersistentRepository

TABLE = "decision_trace_events"


class PersistedDecisionTraceRepository(PersistentRepository):
    """Durable decision traces keyed by ``task_id::event_index``."""

    table = TABLE

    def begin(self, task_id: str) -> DecisionTrace:
        return self.trace(task_id) or DecisionTrace(task_id)

    def append(self, task_id: str, event: TraceEvent) -> None:
        index = self.count_slice(task_id)
        self.save("%s::%d" % (task_id, index),
                  dict(event.to_dict(), task_id=task_id))
        self._trace_cache = None

    def count_slice(self, task_id: str) -> int:
        return len(self.events(task_id))

    def save(self, record_id: str, record: Dict[str, Any]) -> None:
        self.store.save(self.table, record_id, record)

    def trace(self, task_id: str) -> Optional[DecisionTrace]:
        return self._build_trace(task_id)

    def _build_trace(self, task_id: str) -> Optional[DecisionTrace]:
        events = self.events(task_id)
        if not events:
            return None
        trace = DecisionTrace(task_id)
        for event in events:
            item = dict(event)
            item.pop("task_id", None)
            trace.add(TraceEvent(**item))
        return trace

    def events(self, task_id: str) -> List[Dict[str, Any]]:
        return [item for item in self.all()
                if item.get("task_id") == task_id]

    def task_ids(self) -> List[str]:
        return sorted({item.get("task_id", "") for item in self.all()
                       if item.get("task_id")})


__all__ = ["PersistedDecisionTraceRepository"]