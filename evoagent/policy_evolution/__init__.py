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
)
from .canary import (
    CanaryConfig,
    CanaryDecision,
    CanaryVerdict,
    PolicyCanary,
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

__all__ = [
    "AutoRollback",
    "CanaryConfig",
    "CanaryDecision",
    "CanaryVerdict",
    "CandidateOperation",
    "DEFAULT_WEIGHTS",
    "EvolutionGate",
    "EvolutionMetrics",
    "GateDecision",
    "PolicyCanary",
    "PolicyCandidate",
    "PolicyCandidateGenerator",
    "PolicyEvolutionPipeline",
    "PolicyReplayEvaluator",
    "PolicyRunner",
    "PolicyRunningTrace",
    "ReplayComparison",
    "RollbackDecision",
    "RollbackThresholds",
    "RunnableStatus",
    "evolution_utility",
]