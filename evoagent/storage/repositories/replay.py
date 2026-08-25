"""Persistent replay repository (plan section 9.3)."""
from typing import Any, Dict, List, Optional

from ...replay.models import ReplayRun, ReplaySnapshot
from ...storage.json_store import JSONFileStore
from .base import PersistentRepository


class ReplayRepository(PersistentRepository):
    """Persists replay snapshots, tool observations and runs.

    ``snapshot_id`` keys the snapshot; ``replay_tool_observations`` is keyed by
    ``snapshot_id::index``; ``replay_runs`` by run id.
    """

    table = "replay_snapshots"

    def save_snapshot(self, snapshot: ReplaySnapshot) -> ReplaySnapshot:
        self.save(snapshot.snapshot_id, snapshot.to_dict())
        self.store.save("replay_tool_observations", snapshot.snapshot_id,
                        snapshot.tool_observations)
        return snapshot

    def snapshot(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        return self.record(snapshot_id)

    def snapshots_for_task(self, task_id: str) -> List[Dict[str, Any]]:
        return self._by("task_id", task_id)

    def save_run(self, run: ReplayRun) -> ReplayRun:
        self.store.save("replay_runs", run.run_id, run.to_dict())
        return run

    def run(self, run_id: str) -> Optional[Dict[str, Any]]:
        return self.store.get("replay_runs", run_id)

    def runs_for_snapshot(self, snapshot_id: str) -> List[Dict[str, Any]]:
        return [r for r in self.store.all("replay_runs")
                if r.get("snapshot_id") == snapshot_id]


__all__ = ["ReplayRepository"]