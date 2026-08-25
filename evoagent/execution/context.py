"""Execution context that threads task / policy / version identity through the
whole review pipeline (plan section 5.1).

One immutable snapshot per review run; downstream nodes read it instead of
re-deriving identity from scattered config, so the exact policy a report was
produced under is always recoverable and replayable.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from ..diff_parser import ParsedDiff
from ..policy.models import ExecutionPolicy
from ..policy.risk import RiskProfile


@dataclass
class ReviewExecutionContext:
    """Everything a side of the runtime needs to know about one review run."""

    task_id: str
    tenant_id: str
    repository: str
    pull_request: Optional[int] = None

    parsed_diff: Optional[ParsedDiff] = None

    risk_profile: Optional[RiskProfile] = None
    execution_policy: Optional[ExecutionPolicy] = None

    prompt_version: Optional[str] = None
    skill_versions: Dict[str, str] = field(default_factory=dict)
    runtime_policy_version: Optional[int] = None

    model_name: Optional[str] = None

    @property
    def policy_id(self) -> Optional[str]:
        return self.execution_policy.policy_id if self.execution_policy else None

    @property
    def risk_level(self) -> str:
        return self.risk_profile.level if self.risk_profile else "low"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "repository": self.repository,
            "pull_request": self.pull_request,
            "risk_profile": self.risk_profile.to_dict() if self.risk_profile else None,
            "policy_id": self.policy_id,
            "policy_version": self.execution_policy.policy_version
            if self.execution_policy else None,
            "policy_snapshot": self.execution_policy.to_dict()
            if self.execution_policy else None,
            "prompt_version": self.prompt_version,
            "skill_versions": dict(self.skill_versions),
            "runtime_policy_version": self.runtime_policy_version,
            "model_name": self.model_name,
        }