"""Loop-level planning metadata and the coordinator task graph (plan §4, §5).

These structures deliberately record *structured, auditable planning metadata*
(plan §4.1) -- objective, subgoal, next action, reason code, confidence -- and
never a raw chain-of-thought.  The coordinator owns the ``CoordinatorTaskGraph``
which it revises based on agent results, not just on failures.
"""
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AgentPlanState:
    """Auditable planning metadata carried by a single agent loop step."""

    objective: str
    subgoals: List[str] = field(default_factory=list)
    completed: List[str] = field(default_factory=list)
    next_action: str = ""
    revision_reason: str = ""
    confidence: float = 0.5
    plan_version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "AgentPlanState":
        return cls(
            objective=str(value.get("objective", "")),
            subgoals=list(value.get("subgoals", [])),
            completed=list(value.get("completed", [])),
            next_action=str(value.get("next_action", "")),
            revision_reason=str(value.get("revision_reason", "")),
            confidence=float(value.get("confidence", 0.5)),
            plan_version=int(value.get("plan_version", 1)),
        )


class AgentTaskStatus:
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"


@dataclass
class AgentTaskNode:
    node_id: str
    task_type: str
    objective: str
    target_capabilities: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    status: str = AgentTaskStatus.PENDING
    attempt: int = 0
    artifact_ids: List[str] = field(default_factory=list)
    # Optional override of which agent performs this node (empty = coordinator
    # resolves from capabilities).
    agent_id: str = ""
    #: whether the node runs at concurrency 1 (e.g. Fix).
    serial: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "AgentTaskNode":
        return cls(
            node_id=str(value["node_id"]),
            task_type=str(value.get("task_type", "")),
            objective=str(value.get("objective", "")),
            target_capabilities=list(value.get("target_capabilities", [])),
            dependencies=list(value.get("dependencies", [])),
            status=str(value.get("status", AgentTaskStatus.PENDING)),
            attempt=int(value.get("attempt", 0)),
            artifact_ids=list(value.get("artifact_ids", [])),
            agent_id=str(value.get("agent_id", "")),
            serial=bool(value.get("serial", False)),
        )


@dataclass
class CoordinatorTaskGraph:
    """The dynamic task graph the Coordinator builds and revises (§5)."""

    graph_id: str
    nodes: Dict[str, AgentTaskNode] = field(default_factory=dict)
    revision: int = 1

    def add(self, node: AgentTaskNode) -> None:
        self.nodes[node.node_id] = node

    def replace(self, node: AgentTaskNode) -> "CoordinatorTaskGraph":
        """Revise a node; bump the revision so replans are observable/traceable."""
        self.nodes[node.node_id] = node
        self.revision += 1
        return self

    def remove(self, node_id: str) -> None:
        self.nodes.pop(node_id, None)
        self.revision += 1

    def ready(self, node: AgentTaskNode) -> bool:
        return all(
            self.nodes.get(dep_id) is not None
            and self.nodes[dep_id].status == AgentTaskStatus.COMPLETED
            for dep_id in node.dependencies
        )

    def next_ready(self) -> List[AgentTaskNode]:
        return [
            node for node in self.nodes.values()
            if node.status == AgentTaskStatus.PENDING and self.ready(node)
        ]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "revision": self.revision,
            "nodes": {
                key: node.to_dict() for key, node in sorted(self.nodes.items())
            },
        }


# Known task types (§7) and their produced artifact kinds (§7).
TASK_TYPES = {
    "review.security": "security-findings",
    "review.reliability": "reliability-findings",
    "critique.findings": "critique-report",
    "verify.findings": "verification-report",
    "fix.generate": "fix-patch",
}


__all__ = [
    "AgentPlanState",
    "AgentTaskNode",
    "AgentTaskStatus",
    "CoordinatorTaskGraph",
    "TASK_TYPES",
]