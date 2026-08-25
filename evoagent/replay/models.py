"""Replay core data models."""
import enum
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional


class ReplayLevel(str, enum.Enum):
    """How faithfully a replay reproduces the original task (plan section 8.5).

    * ``L1_TOOL``            - replay recorded tool observations only.
    * ``L2_TOOL_AND_MODEL``  - additionally pin the recorded model output, used
                               for deterministic harness replay.
    * ``L3_LIVE_COUNTERFACTUAL`` - re-invoke candidate prompt / model to test a
                               real candidate.
    """

    L1_TOOL = "L1_TOOL"
    L2_TOOL_AND_MODEL = "L2_TOOL_AND_MODEL"
    L3_LIVE_COUNTERFACTUAL = "L3_LIVE_COUNTERFACTUAL"


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
    replay_level: str = ReplayLevel.L1_TOOL.value
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
                "tool_observations", "expected_output", "replay_level", "created_at",
            ) if key in value
        })

    def lookup(self, tool_name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        key = fingerprint(tool_name, arguments)
        for item in self.tool_observations:
            if item.get("fingerprint") == key:
                return item.get("observation")
        return None


class ReplayObservationIndex:
    """Ordered, occurrence-aware observation store (plan section 8.4).

    Repeated calls to the same tool + args are kept as an ordered deque keyed by
    fingerprint, so a replay that invokes the same tool several times consumes
    each recorded observation exactly once, in the original order.
    """

    def __init__(self, observations: List[Dict[str, Any]]):
        self._queues: Dict[str, Deque[Dict[str, Any]]] = {}
        for item in observations or []:
            key = str(item.get("fingerprint", ""))
            if not key:
                continue
            self._queues.setdefault(key, deque()).append((item.get("observation"), item))

    def take(self, tool_name: str, arguments: Dict[str, Any]) -> Optional[Any]:
        """Pop the next observation for ``tool_name`` + ``arguments`` or return None."""
        key = fingerprint(tool_name, arguments)
        queue = self._queues.get(key)
        if not queue:
            return None
        observation, _meta = queue.popleft()
        if not queue:
            del self._queues[key]
        return observation

    def peek(self, tool_name: str, arguments: Dict[str, Any]) -> Optional[Any]:
        queue = self._queues.get(fingerprint(tool_name, arguments))
        if not queue:
            return None
        observation, _meta = queue[0]
        return observation

    def remaining(self) -> int:
        return sum(len(queue) for queue in self._queues.values())

    def __len__(self) -> int:
        return self.remaining()


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