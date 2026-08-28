"""Semantic Dynamic Planner data structures (plan §4.2).

Only *structured rationale codes* are recorded -- never a raw chain-of-thought.
Every planned task carries machine-checkable evidence requirements and a stop
condition so the Coordinator can reason about *who does what, when, and whether
to continue*.
"""
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PlanningContext:
    """Inputs the Planner uses to derive a task graph for one PR diff."""

    objective: str = ""
    changed_files: List[str] = field(default_factory=list)
    semantic_summary: Dict[str, Any] = field(default_factory=dict)
    risk_profile: Dict[str, Any] = field(default_factory=dict)
    available_agents: List[Dict[str, Any]] = field(default_factory=list)
    execution_policy: Dict[str, Any] = field(default_factory=dict)
    prior_artifacts: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class PlannedTask:
    task_id: str
    agent_id: str
    task_type: str
    objective: str
    dependencies: List[str] = field(default_factory=list)
    priority: int = 0
    required_evidence: List[str] = field(default_factory=list)
    stop_condition: Dict[str, Any] = field(default_factory=dict)
    critical: bool = False
    #: whether the node runs at concurrency 1 (e.g. Fix).
    serial: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "PlannedTask":
        return cls(
            task_id=str(value.get("task_id", "")),
            agent_id=str(value.get("agent_id", "")),
            task_type=str(value.get("task_type", "")),
            objective=str(value.get("objective", "")),
            dependencies=list(value.get("dependencies", [])),
            priority=int(value.get("priority", 0)),
            required_evidence=list(value.get("required_evidence", [])),
            stop_condition=dict(value.get("stop_condition", {})),
            critical=bool(value.get("critical", False)),
            serial=bool(value.get("serial", False)),
        )


@dataclass
class PlanningDecision:
    """The Planner's proposal, validated by the TaskGraphValidator."""

    tasks: List[PlannedTask] = field(default_factory=list)
    rationale_codes: List[str] = field(default_factory=list)
    confidence: float = 0.5

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tasks": [task.to_dict() for task in self.tasks],
            "rationale_codes": list(self.rationale_codes),
            "confidence": round(self.confidence, 3),
        }


__all__ = ["PlanningContext", "PlannedTask", "PlanningDecision"]