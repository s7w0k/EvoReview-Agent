"""Durable store for decision traces (plan section 8.2 / Phase 4).

This in-memory repository is the Phase 4 seam; Phase 5 swaps it for the
``decision_trace_events`` table.  Every real review appends its events here so
the runtime can explain decisions and replay can re-consume them in order.
"""
from typing import Dict, List, Optional

from .trace import DecisionTrace, TraceEvent


class DecisionTraceRepository:
    """Persist and query decision traces keyed by task."""

    def __init__(self):
        self._traces: Dict[str, DecisionTrace] = {}

    def begin(self, task_id: str) -> DecisionTrace:
        trace = self._traces.setdefault(task_id, DecisionTrace(task_id))
        return trace

    def append(self, task_id: str, event: TraceEvent) -> None:
        self.begin(task_id).add(event)

    def save(self, trace: DecisionTrace) -> None:
        self._traces[trace.task_id] = trace

    def trace(self, task_id: str) -> Optional[DecisionTrace]:
        return self._traces.get(task_id)

    def events(self, task_id: str) -> List[TraceEvent]:
        trace = self._traces.get(task_id)
        return trace.events if trace else []

    def task_ids(self) -> List[str]:
        return list(self._traces.keys())