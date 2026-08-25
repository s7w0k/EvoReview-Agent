"""Assemble a ``ReplaySnapshot`` from a real review run (plan section 8.3).

A snapshot freezes everything replayable for a finished task: the execution
context (policy / prompt / skill / model versions), the decision trace, the
tool audit, the task input and a ``replay_level`` stating how the snapshot may
be replayed.  ``ReviewCompleted`` / ``ReviewFailed`` callers build one snapshot
per real task so Phase 5 can persist it for the evolution loop.
"""
import time
import uuid
from typing import Any, Dict, List, Optional

from ..decision_trace.trace import DecisionTrace
from ..tools.audit import AuditEntry
from .models import ReplayLevel, ReplaySnapshot


class ReplaySnapshotBuilder:
    """Build a replay snapshot from execution context + trace + audit + input."""

    def __init__(self, repository: Optional[Any] = None):
        self.repository = repository

    def build(
        self,
        *,
        execution_context: Optional[Any] = None,
        decision_trace: Optional[DecisionTrace] = None,
        audit_entries: Optional[List[AuditEntry]] = None,
        tool_observations: Optional[List[Dict[str, Any]]] = None,
        task_id: str = "",
        repository: str = "",
        commit_sha: str = "",
        diff: str = "",
        expected_output: Optional[Dict[str, Any]] = None,
        replay_level: str = ReplayLevel.L1_TOOL.value,
        **extra: Any,
    ) -> ReplaySnapshot:
        hashlib = _import_sha()
        context = _context_dict(execution_context) if execution_context is not None else {}
        observations = list(tool_observations or [])
        if not observations and audit_entries:
            # Derive observations from the audit log (arguments hash only,
            # observations themselves are not recoverable from audit alone).
            observations = []
        snapshot = ReplaySnapshot(
            snapshot_id=uuid.uuid4().hex,
            task_id=task_id or str(context.get("task_id", "")),
            repository=repository or str(context.get("repository", "")),
            commit_sha=commit_sha,
            diff_hash=hashlib.sha256((diff or "").encode("utf-8")).hexdigest(),
            prompt_version=str(context.get("prompt_version", "") or ""),
            skill_versions=dict(context.get("skill_versions") or {}),
            policy_version=str(
                context.get("runtime_policy_version")
                or context.get("policy_version") or ""
            ),
            model_name=str(context.get("model_name", "") or ""),
            context_snapshot=dict(context),
            tool_observations=observations,
            expected_output=expected_output,
            replay_level=replay_level,
            created_at=time.time(),
        )
        for key, value in extra.items():
            setattr(snapshot, key, value)
        return snapshot

    def from_recorder(self, recorder, **kwargs) -> ReplaySnapshot:
        """Capture a snapshot from a live :class:`ReplayRecorder`."""
        snapshot = recorder.to_snapshot(**kwargs)
        if self.repository is not None:
            self.repository.save(snapshot)
        return snapshot


# --------------------------------------------------------------------------- #


def _context_dict(context: Any) -> Dict[str, Any]:
    if hasattr(context, "to_dict"):
        return context.to_dict()
    return dict(context or {})


def _import_sha():
    import hashlib

    return hashlib


__all__ = ["ReplaySnapshotBuilder"]