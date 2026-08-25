"""Durable repositories for every closed-loop artifact (plan section 9.1).

Each module wraps a ``JSONFileStore`` table so the whole state machine (policies,
deployments, tool audit, failures, recovery, replay, procedures, lineage,
evolution budget and decision traces) survives a worker restart.
"""
from .base import PersistentRepository
from .decision_trace import PersistedDecisionTraceRepository
from .deployment import DeploymentRepository
from .evolution_budget import EvolutionBudgetRepository
from .failure import FailureRepository
from .lineage import LineageRepository
from .procedure import ProcedureRepository
from .recovery import RecoveryRepository
from .replay import ReplayRepository
from .runtime_policy import PersistedRuntimePolicyRepository
from .tool_audit import ToolAuditRepository

__all__ = [
    "DeploymentRepository",
    "EvolutionBudgetRepository",
    "FailureRepository",
    "LineageRepository",
    "PersistedDecisionTraceRepository",
    "PersistedRuntimePolicyRepository",
    "PersistentRepository",
    "ProcedureRepository",
    "RecoveryRepository",
    "ReplayRepository",
    "ToolAuditRepository",
]