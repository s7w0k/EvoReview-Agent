"""Runtime-policy self-evolution.

Once the agent learns prompts / rules / procedures, the next step is to let it
learn *how it should run*.  This package turns a ``PolicyEvolution`` candidate
into an auditable sequence -- generate -> gate -> replay -> canary -> promote /
rollback -- while keeping hard safety constraints strictly ahead of any
optimisation score.
"""
from .candidate import (
    CandidateOperation,
    PolicyCandidate,
    PolicyCandidateGenerator,
    candidate_signature,
)
from .canary import (
    CanaryConfig,
    CanaryDecision,
    CanaryVerdict,
    PolicyCanary,
)
from .dataset import DatasetSplit, split_dataset
from .deployment import (
    CanaryStage,
    DEFAULT_TRAFFIC_LADDER,
    DeploymentNotFound,
    DeploymentState,
    ExposureRecord,
    IllegalDeploymentState,
    PolicyDeployment,
    PolicyDeploymentManager,
)
from .evolution_scope import (
    EVOLVABLE_FIELDS,
    FORBIDDEN_FIELDS,
    ForbiddenEvolutionField,
    assert_evolvable,
    audit_mutation,
    validate_mutated_fields,
)
from .gate import EvolutionGate, GateDecision
from .objective import (
    DEFAULT_WEIGHTS,
    EvolutionMetrics,
    evolution_utility,
)
from .pipeline import (
    PolicyEvolutionPipeline,
    PolicyRunningTrace,
    RunnableStatus,
)
from .replay_eval import (
    PolicyReplayEvaluator,
    ReplayComparison,
    PolicyRunner,
)
from .rollback import (
    AutoRollback,
    RollbackDecision,
    RollbackThresholds,
)
from .runner import PolicyReplayRunner

__all__ = [
    "AutoRollback",
    "CanaryConfig",
    "CanaryDecision",
    "CanaryStage",
    "CanaryVerdict",
    "CandidateOperation",
    "DEFAULT_TRAFFIC_LADDER",
    "DEFAULT_WEIGHTS",
    "DatasetSplit",
    "DeploymentNotFound",
    "DeploymentState",
    "EVOLVABLE_FIELDS",
    "ExposureRecord",
    "ForbiddenEvolutionField",
    "FORBIDDEN_FIELDS",
    "EvolutionGate",
    "EvolutionMetrics",
    "GateDecision",
    "IllegalDeploymentState",
    "PolicyCanary",
    "PolicyCandidate",
    "PolicyCandidateGenerator",
    "PolicyDeployment",
    "PolicyDeploymentManager",
    "PolicyEvolutionPipeline",
    "PolicyReplayEvaluator",
    "PolicyReplayRunner",
    "PolicyRunner",
    "PolicyRunningTrace",
    "ReplayComparison",
    "RollbackDecision",
    "RollbackThresholds",
    "RunnableStatus",
    "assert_evolvable",
    "audit_mutation",
    "candidate_signature",
    "evolution_utility",
    "split_dataset",
    "validate_mutated_fields",
]