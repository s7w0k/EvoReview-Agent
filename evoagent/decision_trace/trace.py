"""Decision trace for the harness (plan section 12).

Beyond logging, the harness must be able to explain *why* an agent made a
decision.  A decision trace is an ordered record of policy-resolution and agent
steps, and ``diff`` shows how a candidate changed behaviour relative to its
baseline (e.g. that Procedure Evolution inserted a ``find_callers`` step).
"""
from dataclasses import dataclass, field as dc_field
from typing import Any, Dict, List, Optional


@dataclass
class TraceEvent:
    """One recorded harness / agent step (see plan section 12.2)."""

    step_id: str
    action_type: str                       # "policy_resolution" | "agent_step" | ...
    agent_id: str = ""
    policy_id: str = ""
    tool: str = ""
    arguments_hash: str = ""
    observation_hash: str = ""
    input_context_hash: str = ""
    token_usage: int = 0
    cost: float = 0.0
    latency: float = 0.0
    failure: bool = False
    recovery_action: str = ""
    data: Dict[str, Any] = dc_field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "action_type": self.action_type,
            "agent_id": self.agent_id,
            "policy_id": self.policy_id,
            "tool": self.tool,
            "arguments_hash": self.arguments_hash,
            "observation_hash": self.observation_hash,
            "input_context_hash": self.input_context_hash,
            "token_usage": self.token_usage,
            "cost": self.cost,
            "latency": self.latency,
            "failure": self.failure,
            "recovery_action": self.recovery_action,
            "data": dict(self.data),
        }


class DecisionTrace:
    """An ordered sequence of trace events for one task / decision."""

    def __init__(self, task_id: str = ""):
        self.task_id = task_id
        self.events: List[TraceEvent] = []

    def add(self, event: TraceEvent) -> "DecisionTrace":
        self.events.append(event)
        return self

    @property
    def agent_actions(self) -> List[TraceEvent]:
        return [event for event in self.events if event.action_type == "agent_step"]

    def tool_path(self) -> List[str]:
        """The sequence of tools used, e.g. ['search_diff', 'find_callers']."""
        return [event.tool for event in self.agent_actions if event.tool]

    def to_dict(self) -> Dict[str, Any]:
        return {"task_id": self.task_id,
                "events": [event.to_dict() for event in self.events]}


@dataclass
class DecisionDiff:
    """Structural difference between a baseline and a candidate trace."""

    baseline_path: List[str]
    candidate_path: List[str]

    @property
    def added_steps(self) -> List[str]:
        return [step for step in self.candidate_path if step not in self.baseline_path]

    @property
    def removed_steps(self) -> List[str]:
        return [step for step in self.baseline_path if step not in self.candidate_path]

    @property
    def differs(self) -> bool:
        return self.baseline_path != self.candidate_path

    def render(self) -> str:
        base = " -> ".join(self.baseline_path) if self.baseline_path else "(none)"
        cand = " -> ".join(self.candidate_path) if self.candidate_path else "(none)"
        lines = ["Baseline:", base, "", "Candidate:", cand, ""]
        if self.added_steps:
            lines += ["Added: " + ", ".join(self.added_steps)]
        if self.removed_steps:
            lines += ["Removed: " + ", ".join(self.removed_steps)]
        return "\n".join(lines)


class TraceLogger:
    """Collects trace events and can compute a decision diff between runs."""

    def __init__(self):
        self._traces: Dict[str, DecisionTrace] = {}

    def begin(self, task_id: str) -> DecisionTrace:
        trace = DecisionTrace(task_id)
        self._traces[task_id] = trace
        return trace

    def record(self, task_id: str, event: TraceEvent) -> None:
        self._traces.setdefault(task_id, DecisionTrace(task_id)).add(event)

    def trace(self, task_id: str) -> Optional[DecisionTrace]:
        return self._traces.get(task_id)

    @staticmethod
    def diff(baseline: DecisionTrace, candidate: DecisionTrace) -> DecisionDiff:
        return DecisionDiff(baseline.tool_path(), candidate.tool_path())