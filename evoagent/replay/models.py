"""Replay core data models."""
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ReplaySnapshot:
    snapshot_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    task_id: str = ""
    repository: str = ""
    commit_sha: str = ""
    diff_hash: str = ""
    prompt_version: str = ""
    skill_versions: Dict[str, str] = field(default_factory=dict)
    policy_version: str = ""
    model_name: str = ""
    model_parameters: Dict[str, Any] = field(default_factory=dict)
    context_snapshot: Dict[str, Any] = field(default_factory=dict)
    memory_snapshot_ids: List[str] = field(default_factory=list)
    tool_observations: List[Dict[str, Any]] = field(default_factory=list)
    expected_output: Optional[Dict[str, Any]] = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        value = {
            key: item for key, item in vars(self).items()
            if key in vars(self)
        }
        return value

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "ReplaySnapshot":
        return cls(**{
            key: value[key] for key in (
                "snapshot_id", "task_id", "repository", "commit_sha", "diff_hash",
                "prompt_version", "skill_versions", "policy_version", "model_name",
                "model_parameters", "context_snapshot", "memory_snapshot_ids",
                "tool_observations", "expected_output", "created_at",
            ) if key in value
        })

    def lookup(self, tool_name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        key = fingerprint(tool_name, arguments)
        for item in self.tool_observations:
            if item.get("fingerprint") == key:
                return item.get("observation")
        return None


def fingerprint(tool_name: str, arguments: Dict[str, Any]) -> str:
    import json as _json
    canonical = _json.dumps(arguments or {}, sort_keys=True, separators=(",", ":"),
                            default=str)
    import hashlib
    return "%s#%s" % (tool_name, hashlib.sha256(canonical.encode("utf-8")).hexdigest())


@dataclass
class ReplayRun:
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    snapshot_id: str = ""
    candidate_label: str = ""
    baseline_label: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)
    decision: str = "PENDING"
    reason: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return dict(vars(self))