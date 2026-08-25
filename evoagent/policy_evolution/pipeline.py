"""Orchestrates the runtime-policy evolution lifecycle.

A candidate moves through a fixed, auditable sequence:

    Generate -> Gate -> Replay -> (reject)  |-> Canary -> Promote
                                           (degradation) -> Auto Rollback

Only a candidate that survives the hard gate and replay can enter canary; only a
canary that keeps safety-critical metrics whole can be promoted.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from evoagent.policy.models import ExecutionPolicy

from .candidate import PolicyCandidate, PolicyCandidateGenerator, CandidateOperation
from .canary import CanaryDecision, CanaryVerdict
from .gate import EvolutionGate, GateDecision
from .objective import EvolutionMetrics
from .replay_eval import PolicyReplayEvaluator, ReplayComparison, PolicyRunner
from .rollback import AutoRollback, RollbackDecision


class RunnableStatus(str, Enum):
    GENERATED = "GENERATED"
    REJECTED = "REJECTED"
    REPLAY_PASSED = "REPLAY_PASSED"
    CANARY = "CANARY"
    PROMOTED = "PROMOTED"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass
class PolicyRunningTrace:
    """Record of one candidate's journey across the evolution stages."""

    candidate_id: str
    status: RunnableStatus = RunnableStatus.GENERATED
    gate: Optional[GateDecision] = None
    replay: Optional[ReplayComparison] = None
    canary: Optional[CanaryDecision] = None
    rollback: Optional[RollbackDecision] = None
    notes: List[str] = field(default_factory=list)


class PolicyEvolutionPipeline:
    """Drives a candidate from generation through to promotion / rollback."""

    def __init__(
        self,
        generator: Optional[PolicyCandidateGenerator] = None,
        gate: Optional[EvolutionGate] = None,
        canary_config=None,
        runner: Optional[PolicyRunner] = None,
    ):
        self.generator = generator or PolicyCandidateGenerator()
        self.gate = gate or EvolutionGate()
        self.runner = runner
        self._canary_config = canary_config
        self._rollback = AutoRollback()
        self._traces: Dict[str, List[PolicyRunningTrace]] = {}

    # -- generation ---------------------------------------------------------

    def generate(
        self,
        parent: ExecutionPolicy,
        operations: Optional[List[CandidateOperation]] = None,
        hypothesis_id: Optional[str] = None,
    ) -> List[PolicyCandidate]:
        return self.generator.generate(
            parent, operations=operations, hypothesis_id=hypothesis_id)

    # -- stage runner -------------------------------------------------------

    def evaluate_replay(
        self,
        candidate: PolicyCandidate,
        baseline: ExecutionPolicy,
    ) -> PolicyRunningTrace:
        """Run the hard gate, then the replay comparison for one candidate."""
        trace = PolicyRunningTrace(candidate_id=candidate.candidate_id)

        if self.runner is None:
            raise ValueError("PolicyEvolutionPipeline.runner is required for replay")

        evaluator = PolicyReplayEvaluator(self.runner)
        replay = evaluator.evaluate(baseline, candidate.policy)

        # Hard safety gate is authoritative before any utility comparison.
        gate = self.gate.evaluate(replay.baseline, replay.candidate)
        trace.gate = gate
        trace.replay = replay

        if gate.rejected or replay.utility <= 0:
            if gate.rejected:
                trace.notes = list(gate.reasons)
            else:
                trace.notes = ["replay utility did not improve over baseline"]
            trace.status = RunnableStatus.REJECTED
        else:
            trace.status = RunnableStatus.REPLAY_PASSED

        self._traces.setdefault(candidate.candidate_id, []).append(trace)
        return trace

    def finalise_canary(self, candidate: PolicyCandidate,
                        decision: CanaryDecision) -> PolicyRunningTrace:
        trace = self._trace_for(candidate.candidate_id, create=True)
        trace.canary = decision
        self._finalise_canary(trace)
        return trace

    def _finalise_canary(self, trace: PolicyRunningTrace) -> None:
        if trace.canary is None:
            return
        if trace.canary.verdict is CanaryVerdict.PROMOTE:
            trace.status = RunnableStatus.PROMOTED
        elif trace.canary.verdict is CanaryVerdict.ROLLBACK:
            trace.status = RunnableStatus.ROLLED_BACK
            if trace.replay is not None:
                trace.rollback = self._rollback.evaluate(
                    trace.replay.baseline, trace.replay.candidate)
        else:
            trace.status = RunnableStatus.CANARY

    def _trace_for(self, candidate_id: str, *, create: bool) -> PolicyRunningTrace:
        group = self._traces.setdefault(candidate_id, [])
        if group:
            return group[-1]
        if create:
            trace = PolicyRunningTrace(candidate_id=candidate_id)
            group.append(trace)
            return trace
        return PolicyRunningTrace(candidate_id=candidate_id)

    # -- introspection ------------------------------------------------------

    def trace(self, candidate_id: str) -> Optional[PolicyRunningTrace]:
        group = self._traces.get(candidate_id, [])
        return group[-1] if group else None