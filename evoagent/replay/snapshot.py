"""Snapshot persistence and counterfactual substitution.

A snapshot freezes the inputs, versions, context and tool observations of a
historical task.  Counterfactual replay substitutes one variable (prompt / skill
/ policy / model / topology / context strategy) while keeping everything else
fixed.
"""
from typing import Any, Dict, Optional

from .models import ReplaySnapshot


class SnapshotStore:
    """In-memory snapshot store with JSON round-tripping for persistence."""

    def __init__(self):
        self._snapshots: Dict[str, ReplaySnapshot] = {}
        self._runs: Dict[str, Any] = {}

    def save(self, snapshot: ReplaySnapshot) -> ReplaySnapshot:
        self._snapshots[snapshot.snapshot_id] = snapshot
        return snapshot

    def get(self, snapshot_id: str) -> Optional[ReplaySnapshot]:
        return self._snapshots.get(snapshot_id)

    def list(self) -> list:
        return list(self._snapshots.values())

    def save_run(self, run) -> None:
        self._runs[run.run_id] = run

    def get_run(self, run_id: str):
        return self._runs.get(run_id)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "snapshots": [s.to_dict() for s in self._snapshots.values()],
            "runs": [r.to_dict() for r in self._runs.values()],
        }

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "SnapshotStore":
        store = cls()
        for item in value.get("snapshots", []):
            store.save(ReplaySnapshot.from_dict(item))
        return store


class Counterfactual:
    """Produce a derived snapshot with one variable substituted."""

    def __init__(self, store: SnapshotStore):
        self.store = store

    def substitute(
        self,
        snapshot_id: str,
        prompt_version: Optional[str] = None,
        skill_versions: Optional[Dict[str, str]] = None,
        policy_version: Optional[str] = None,
        model_name: Optional[str] = None,
        agent_topology: Optional[list] = None,
        context_strategy: Optional[Dict[str, Any]] = None,
    ) -> ReplaySnapshot:
        base = self.store.get(snapshot_id)
        if base is None:
            raise KeyError("unknown snapshot: %s" % snapshot_id)
        derived = ReplaySnapshot(
            task_id=base.task_id,
            repository=base.repository,
            commit_sha=base.commit_sha,
            diff_hash=base.diff_hash,
            prompt_version=(
                prompt_version if prompt_version is not None else base.prompt_version
            ),
            skill_versions=(
                dict(skill_versions if skill_versions is not None else base.skill_versions)
            ),
            policy_version=policy_version if policy_version is not None else base.policy_version,
            model_name=model_name if model_name is not None else base.model_name,
            model_parameters=dict(base.model_parameters),
            context_snapshot=dict(base.context_snapshot),
            memory_snapshot_ids=list(base.memory_snapshot_ids),
            tool_observations=list(base.tool_observations),
            expected_output=base.expected_output,
        )
        if agent_topology is not None:
            derived.context_snapshot["agent_topology"] = list(agent_topology)
        if context_strategy is not None:
            derived.context_snapshot["context_strategy"] = dict(context_strategy)
        return derived