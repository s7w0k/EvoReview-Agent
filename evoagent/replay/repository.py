"""Replay snapshot repository facade (Phase 4/5 seam).

Wraps the in-memory :class:`SnapshotStore` so runtime code asks one repository
for auto-snapshot capture and replay consumption.
"""
from typing import Any, Dict, List, Optional

from .models import ReplaySnapshot
from .snapshot import SnapshotStore


class ReplayRepository:
    """Store and query replay snapshots and their runs."""

    def __init__(self, store: Optional[SnapshotStore] = None):
        self._store = store or SnapshotStore()

    def snapshot(self, snapshot_id: str) -> Optional[ReplaySnapshot]:
        return self._store.get(snapshot_id)

    def save(self, snapshot: ReplaySnapshot) -> ReplaySnapshot:
        return self._store.save(snapshot)

    def snapshots_for_task(self, task_id: str) -> List[ReplaySnapshot]:
        return [s for s in self._store.list() if s.task_id == task_id]

    def list_snapshots(self) -> List[ReplaySnapshot]:
        return self._store.list()

    def latest_for_task(self, task_id: str) -> Optional[ReplaySnapshot]:
        matching = self.snapshots_for_task(task_id)
        if not matching:
            return None
        return max(matching, key=lambda s: s.snapshot_id)

    def snapshot_dict(self) -> Dict[str, Any]:
        return self._store.as_dict()