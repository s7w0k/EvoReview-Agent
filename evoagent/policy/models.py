"""Declarative execution policy model.

A policy is a bundle of every runtime constraint so the agent runtime is fully
driven by one object rather than scattered configuration reads.  Policies are
immutable once resolved; callers produce derived policies through the resolver.
"""
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

RISK_LEVELS = ("low", "medium", "high", "critical")
RISK_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


@dataclass(frozen=True)
class ExecutionBudget:
    max_steps: int = 1
    max_tool_calls: int = 1
    max_wall_time_seconds: float = 120.0
    max_tokens: Optional[int] = None
    max_cost_usd: Optional[float] = None

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        if self.max_tool_calls < 1:
            raise ValueError("max_tool_calls must be at least 1")
        if self.max_wall_time_seconds < 1:
            raise ValueError("max_wall_time_seconds must be positive")


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 0
    backoff_seconds: float = 1.0
    exponential_backoff: bool = False
    retryable_failures: set = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        if not isinstance(self.retryable_failures, (set, frozenset)):
            object.__setattr__(self, "retryable_failures", set(self.retryable_failures or ()))


@dataclass(frozen=True)
class VerificationPolicy:
    critic_required: bool = False
    evidence_required: bool = False
    verifier_required: bool = False
    sandbox_required: bool = False
    minimum_confidence: float = 0.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.minimum_confidence <= 1.0:
            raise ValueError("minimum_confidence must be between 0 and 1")


@dataclass(frozen=True)
class AgentPolicy:
    enabled_agents: List[str] = field(default_factory=list)
    max_parallel_agents: int = 1
    fallback_agents: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.max_parallel_agents < 1:
            raise ValueError("max_parallel_agents must be at least 1")


@dataclass(frozen=True)
class ToolPermission:
    tool_name: str
    allow: bool = True
    max_calls: Optional[int] = None
    requires_sandbox: bool = False
    requires_approval: bool = False


@dataclass(frozen=True)
class ExecutionPolicy:
    policy_id: str
    policy_version: int = 1
    risk_level: str = "low"
    budget: ExecutionBudget = field(default_factory=ExecutionBudget)
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    verification: VerificationPolicy = field(default_factory=VerificationPolicy)
    agents: AgentPolicy = field(default_factory=AgentPolicy)
    tool_permissions: List[ToolPermission] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.policy_id:
            raise ValueError("policy_id is required")
        if self.risk_level not in RISK_LEVELS:
            raise ValueError("invalid risk_level: %s" % self.risk_level)
        if self.policy_version < 1:
            raise ValueError("policy_version must be at least 1")
        if self.metadata is None:
            object.__setattr__(self, "metadata", {})

    def tool_permission(self, tool_name: str) -> Optional[ToolPermission]:
        for permission in self.tool_permissions:
            if permission.tool_name == tool_name:
                return permission
        return None

    def allows(self, tool_name: str) -> bool:
        permission = self.tool_permission(tool_name)
        return permission.allow if permission else False

    def tool_max_calls(self, tool_name: str) -> Optional[int]:
        permission = self.tool_permission(tool_name)
        return permission.max_calls if permission else None

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["retry"]["retryable_failures"] = sorted(value["retry"]["retryable_failures"])
        return value

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "ExecutionPolicy":
        data = dict(value)
        data["retry"] = RetryPolicy(**{
            key: item for key, item in data.get("retry", {}).items()
        })
        data["verification"] = VerificationPolicy(**data.get("verification", {}))
        data["agents"] = AgentPolicy(**data.get("agents", {}))
        data["budget"] = ExecutionBudget(**data.get("budget", {}))
        data["tool_permissions"] = [
            ToolPermission(**item) for item in data.get("tool_permissions", [])
        ]
        data["metadata"] = dict(data.get("metadata", {}))
        return cls(**data)