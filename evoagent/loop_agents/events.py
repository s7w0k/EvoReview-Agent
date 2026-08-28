"""Structured events emitted by the result-driven multi-agent runtime."""
from dataclasses import asdict, dataclass, field
from typing import Any, Dict


AGENT_COMPLETED = "AGENT_COMPLETED"
AGENT_FAILED = "AGENT_FAILED"
FINDINGS_EMITTED = "FINDINGS_EMITTED"
CRITIQUE_EMITTED = "CRITIQUE_EMITTED"
REPLAN_REQUESTED = "REPLAN_REQUESTED"
FINDING_UPDATED = "FINDING_UPDATED"
VERIFICATION_COMPLETED = "VERIFICATION_COMPLETED"
FIX_REQUESTED = "FIX_REQUESTED"
FIX_COMPLETED = "FIX_COMPLETED"
GRAPH_MUTATED = "GRAPH_MUTATED"
ARTIFACT_SUPERSEDED = "ARTIFACT_SUPERSEDED"


@dataclass(frozen=True)
class RuntimeGraphEvent:
    kind: str
    node_id: str = ""
    artifact_id: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


__all__ = [
    "RuntimeGraphEvent", "AGENT_COMPLETED", "AGENT_FAILED",
    "FINDINGS_EMITTED", "CRITIQUE_EMITTED", "REPLAN_REQUESTED",
    "FINDING_UPDATED", "VERIFICATION_COMPLETED", "FIX_REQUESTED",
    "FIX_COMPLETED", "GRAPH_MUTATED", "ARTIFACT_SUPERSEDED",
]
