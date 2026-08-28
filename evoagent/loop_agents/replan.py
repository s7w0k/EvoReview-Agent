"""Targeted Result-driven Replan (plan §5).

A :class:`ReplanRequest` names *which finding, which agent/capability, why, and
which evidence* is missing.  The Coordinator resolves it to a precise target --
*not* an arbitrary ``review.*`` node -- and inserts a **new** node (e.g. a
``security-recheck``) ahead of an existing downstream node rather than resetting
the original.  A fingerprint + budget prevent replan loops.
"""
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# Replan trigger reason codes emitted by Critic / Verifier (structured only).
REASON_CODES = {
    "MISSING_SOURCE_SINK_EVIDENCE",
    "MISSING_CONFLICTING_EVIDENCE",
    "LOW_VERIFICATION_CONFIDENCE",
    "SPECIALIST_MISS",
    "INSUFFICIENT_EXPLANATION",
    "UNVERIFIED_CHANGE",
}

# capability -> default agent that provides it (plan §5.3 fallback).
CAPABILITY_AGENT = {
    "security": "security-agent",
    "reliability": "reliability-agent",
    "critique": "critic-agent",
    "verification": "verifier-agent",
    "fix": "fix-agent",
    "dataflow": "security-agent",
    "test": "verifier-agent",
}


@dataclass
class ReplanRequest:
    request_id: str
    source_agent: str
    target_agent: Optional[str] = None
    target_capability: Optional[str] = None
    finding_id: Optional[str] = None
    reason_code: str = "SPECIALIST_MISS"
    reason_summary: str = ""
    requested_action: str = ""
    required_evidence: List[str] = field(default_factory=list)
    context_refs: List[str] = field(default_factory=list)
    priority: int = 5
    finding: Dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        return "%s|%s|%s|%s" % (
            self.target_agent or "",
            self.finding_id or "",
            self.requested_action or "",
            "|".join(sorted(self.context_refs or [])),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "ReplanRequest":
        return cls(
            request_id=str(value.get("request_id") or uuid.uuid4().hex),
            source_agent=str(value.get("source_agent", "")),
            target_agent=value.get("target_agent"),
            target_capability=value.get("target_capability"),
            finding_id=value.get("finding_id"),
            reason_code=str(value.get("reason_code", "SPECIALIST_MISS")),
            reason_summary=str(value.get("reason_summary", "")),
            requested_action=str(value.get("requested_action", "")),
            required_evidence=list(value.get("required_evidence", [])),
            context_refs=list(value.get("context_refs", [])),
            priority=int(value.get("priority", 5)),
            finding=dict(value.get("finding") or {}),
        )


@dataclass
class ReplanBudget:
    max_replans_per_review: int = 3
    max_replans_per_finding: int = 2
    max_same_agent_replans: int = 2
    max_graph_revision: int = 8


class ReplanTargetResolver:
    """Resolve a ReplanRequest to a concrete agent id (plan §5.3)."""

    def __init__(self, available_agents: List[str], capability_agent=None):
        self.available_agents = set(available_agents)
        self.capability_agent = dict(capability_agent or CAPABILITY_AGENT)

    def resolve(self, request: ReplanRequest) -> Optional[str]:
        if request.target_agent and request.target_agent in self.available_agents:
            return request.target_agent
        if request.target_capability:
            agent = self.capability_agent.get(request.target_capability)
            if agent in self.available_agents:
                return agent
        # structured fallback keyed off the requested action
        if request.requested_action:
            agent = self.capability_agent.get(request.requested_action)
            if agent in self.available_agents:
                return agent
        return None


class ReplanTracker:
    """Prevents replan loops and enforces the budget (plan §5.5, §5.6)."""

    def __init__(self, budget: Optional[ReplanBudget] = None):
        self.budget = budget or ReplanBudget()
        self._seen: Dict[str, int] = {}
        self._per_agent: Dict[str, int] = {}
        self._per_finding: Dict[str, int] = {}

    def accept(self, request: ReplanRequest) -> bool:
        if (
            self.budget.max_replans_per_review >= 0
            and self._total() >= self.budget.max_replans_per_review
        ):
            return False
        fp = request.fingerprint()
        self._seen[fp] = self._seen.get(fp, 0) + 1
        if self._seen[fp] >= 2:
            return False  # same replan already attempted
        agent = request.target_agent or request.target_capability or ""
        self._per_agent[agent] = self._per_agent.get(agent, 0) + 1
        if self._per_agent[agent] > self.budget.max_same_agent_replans:
            return False
        if request.finding_id:
            self._per_finding[request.finding_id] = (
                self._per_finding.get(request.finding_id, 0) + 1)
            if self._per_finding[request.finding_id] > self.budget.max_replans_per_finding:
                return False
        return True

    def _total(self) -> int:
        return sum(self._seen.values())


def emit_replan_request(
    *, source_agent: str, target_agent: Optional[str] = None,
    target_capability: Optional[str] = None, finding_id: Optional[str] = None,
    reason_code: str, reason_summary: str, requested_action: str = "",
    required_evidence: Optional[List[str]] = None, finding: Optional[Dict[str, Any]] = None,
) -> ReplanRequest:
    return ReplanRequest(
        request_id="R" + uuid.uuid4().hex[:6],
        source_agent=source_agent, target_agent=target_agent,
        target_capability=target_capability, finding_id=finding_id,
        reason_code=reason_code, reason_summary=reason_summary,
        requested_action=requested_action,
        required_evidence=list(required_evidence or []),
        finding=dict(finding or {}),
    )


__all__ = [
    "ReplanRequest", "ReplanBudget", "ReplanTargetResolver", "ReplanTracker",
    "emit_replan_request", "REASON_CODES", "CAPABILITY_AGENT",
]