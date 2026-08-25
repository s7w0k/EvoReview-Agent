"""Evolution lineage.

Every self-evolution change must produce a full traceable chain

    Experience -> Reflection -> Hypothesis -> Candidate
        -> Evaluation -> Deployment -> Outcome

rather than just ``v3 -> v4``.  This module records that chain so every change
can answer *"why did this evolution happen?"*.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class LineageStage(str, Enum):
    EXPERIENCE = "EXPERIENCE"
    REFLECTION = "REFLECTION"
    HYPOTHESIS = "HYPOTHESIS"
    CANDIDATE = "CANDIDATE"
    EVALUATION = "EVALUATION"
    DEPLOYMENT = "DEPLOYMENT"
    OUTCOME = "OUTCOME"

    @property
    def order(self) -> int:
        return {stage: index for index, stage in enumerate(LineageStage)}[self]


@dataclass
class LineageNode:
    """One stage in an evolution's lineage chain."""

    stage: LineageStage
    node_id: str
    created_at: str = ""
    source_refs: List[str] = field(default_factory=list)
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage.value,
            "node_id": self.node_id,
            "created_at": self.created_at,
            "source_refs": list(self.source_refs),
            "payload": dict(self.payload),
        }


class EvolutionLineage:
    """The full ordered lineage of a single candidate."""

    def __init__(self, candidate_id: str, source_ids: Optional[List[str]] = None):
        self.candidate_id = candidate_id
        self._nodes: List[LineageNode] = []
        if source_ids:
            self._nodes.append(LineageNode(
                stage=LineageStage.EXPERIENCE, node_id="exp-initial",
                source_refs=list(source_ids)))

    def add(self, node: LineageNode) -> "EvolutionLineage":
        self._nodes.append(node)
        return self

    def add_stage(self, stage: LineageStage, node_id: str, **payload) -> LineageNode:
        node = LineageNode(stage=stage, node_id=node_id, payload=payload)
        self._nodes.append(node)
        return node

    @property
    def nodes(self) -> List[LineageNode]:
        return list(self._nodes)

    def chain(self) -> List[LineageStage]:
        """Return the ordered stages represented by this lineage."""
        stages = [node.stage for node in self._nodes]
        # keep first occurrence, ordered by stage order, to render canonical chain
        ordered: List[LineageStage] = []
        seen = set()
        for s in stages:
            if s not in seen:
                seen.add(s)
                ordered.append(s)
        return sorted(ordered, key=lambda stage: stage.order)

    def has_stage(self, stage: LineageStage) -> bool:
        return any(node.stage is stage for node in self._nodes)

    def node(self, stage: LineageStage) -> Optional[LineageNode]:
        for node in self._nodes:
            if node.stage is stage:
                return node
        return None

    def reported_sources(self) -> List[str]:
        source_ids = []
        for node in self._nodes:
            source_ids.extend(node.source_refs)
        return source_ids

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "stages": [stage.value for stage in self.chain()],
            "nodes": [node.to_dict() for node in self._nodes],
        }


class LineageTracker:
    """Registry of all evolution lineages keyed by candidate id."""

    def __init__(self):
        self._lineages: Dict[str, EvolutionLineage] = {}

    def begin(self, candidate_id: str, source_ids: Optional[List[str]] = None,
              reset: bool = False) -> EvolutionLineage:
        if reset or candidate_id not in self._lineages:
            self._lineages[candidate_id] = EvolutionLineage(
                candidate_id, source_ids=source_ids)
        return self._lineages[candidate_id]

    def add(self, candidate_id: str, stage: LineageStage, node_id: str,
            **payload) -> EvolutionLineage:
        lineage = self.begin(candidate_id)
        lineage.add_stage(stage, node_id, **payload)
        return lineage

    def get(self, candidate_id: str) -> Optional[EvolutionLineage]:
        return self._lineages.get(candidate_id)

    def __len__(self) -> int:
        return len(self._lineages)