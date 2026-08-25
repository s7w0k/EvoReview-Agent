"""Strict Procedure candidate lifecycle (plan section 10.5).

A candidate must pass every evaluation stage *in order* before it can reach
ACTIVE:

    DRAFT
    -> STATIC_VALIDATED
    -> REPLAY_PASSED
    -> HOLDOUT_PASSED
    -> SHADOW
    -> CANARY
    -> ACTIVE

Skipping an evaluation stage is forbidden -- e.g. a VALIDATED candidate may
*not* jump straight to ACTIVE.  Failure at any pre-ACTIVE stage moves the
candidate to REJECTED; a regression from ACTIVE moves it to ROLLED_BACK.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from .schema import ProcedureSkill


class CandidateStatus(str, Enum):
    DRAFT = "DRAFT"
    STATIC_VALIDATED = "STATIC_VALIDATED"
    REPLAY_PASSED = "REPLAY_PASSED"
    HOLDOUT_PASSED = "HOLDOUT_PASSED"
    SHADOW = "SHADOW"
    CANARY = "CANARY"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    ROLLED_BACK = "ROLLED_BACK"


# Allowed forward transitions.  Nothing may skip a stage or jump to ACTIVE
# from a non-evaluated state.
_FORWARD = {
    CandidateStatus.DRAFT: {CandidateStatus.STATIC_VALIDATED},
    CandidateStatus.STATIC_VALIDATED: {CandidateStatus.REPLAY_PASSED},
    CandidateStatus.REPLAY_PASSED: {CandidateStatus.HOLDOUT_PASSED},
    CandidateStatus.HOLDOUT_PASSED: {CandidateStatus.SHADOW},
    CandidateStatus.SHADOW: {CandidateStatus.CANARY},
    CandidateStatus.CANARY: {CandidateStatus.ACTIVE},
}

# Failure exits reachable from any pre-ACTIVE stage.
_FAILURE_EXITS = {
    CandidateStatus.DRAFT,
    CandidateStatus.STATIC_VALIDATED,
    CandidateStatus.REPLAY_PASSED,
    CandidateStatus.HOLDOUT_PASSED,
    CandidateStatus.SHADOW,
    CandidateStatus.CANARY,
}


class CandidateTransitionError(Exception):
    """Raised for an illegal lifecycle transition (e.g. skipping a gate)."""


@dataclass
class Transition:
    from_status: CandidateStatus
    to_status: CandidateStatus
    evidence: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from": self.from_status.value,
            "to": self.to_status.value,
            "evidence": self.evidence,
        }


@dataclass
class ProcedureCandidate:
    """A single evolving procedure candidate and its lifecycle history."""

    skill: ProcedureSkill
    status: CandidateStatus = CandidateStatus.DRAFT
    hypothesis_id: Optional[str] = None
    parent_version: Optional[int] = None
    history: List[Transition] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.skill.name,
            "version": self.skill.version,
            "status": self.status.value,
            "hypothesis_id": self.hypothesis_id,
            "parent_version": self.parent_version,
            "history": [step.to_dict() for step in self.history],
            "skill": self.skill.to_dict(),
        }


class ProcedureCandidateLifecycle:
    """Owns an immutable candidate and enforces gated transitions on it."""

    def __init__(
        self,
        candidate: ProcedureCandidate,
        *,
        hypothesis_id: Optional[str] = None,
        parent_version: Optional[int] = None,
    ):
        self._candidate = candidate
        if hypothesis_id is not None:
            candidate.hypothesis_id = hypothesis_id
        if parent_version is not None:
            candidate.parent_version = parent_version

    # -- property / state ---------------------------------------------------

    @property
    def candidate(self) -> ProcedureCandidate:
        return self._candidate

    @property
    def status(self) -> CandidateStatus:
        return self._candidate.status

    @property
    def skill(self) -> ProcedureSkill:
        return self._candidate.skill

    # -- gated transitions --------------------------------------------------

    def transition(
        self,
        to_status: CandidateStatus,
        evidence: Optional[str] = None,
    ) -> ProcedureCandidate:
        """Attempt ``status -> to_status``; reject illegal skips."""
        current = self._candidate.status
        if current is to_status:
            raise CandidateTransitionError(
                f"candidate {self._candidate.skill.name!r} is already "
                f"{current.value}")
        if to_status in (CandidateStatus.REJECTED, CandidateStatus.ROLLED_BACK):
            if not self._is_failure_exit(current, to_status):
                raise CandidateTransitionError(
                    f"cannot move {current.value} to {to_status.value}")
        elif to_status not in _FORWARD.get(current, set()):
            raise CandidateTransitionError(
                f"illegal transition {current.value} -> {to_status.value}; "
                "an evaluation gate would be skipped")
        self._candidate.status = to_status
        self._candidate.history.append(
            Transition(current, to_status, evidence=evidence))
        return self._candidate

    # -- convenience wrappers ----------------------------------------------

    def static_validate(self, evidence: Optional[str] = None) -> ProcedureCandidate:
        return self.transition(CandidateStatus.STATIC_VALIDATED, evidence=evidence)

    def replay_pass(self, evidence: Optional[str] = None) -> ProcedureCandidate:
        return self.transition(CandidateStatus.REPLAY_PASSED, evidence=evidence)

    def holdout_pass(self, evidence: Optional[str] = None) -> ProcedureCandidate:
        return self.transition(CandidateStatus.HOLDOUT_PASSED, evidence=evidence)

    def enter_shadow(self, evidence: Optional[str] = None) -> ProcedureCandidate:
        return self.transition(CandidateStatus.SHADOW, evidence=evidence)

    def enter_canary(self, evidence: Optional[str] = None) -> ProcedureCandidate:
        return self.transition(CandidateStatus.CANARY, evidence=evidence)

    def activate(self, evidence: Optional[str] = None) -> ProcedureCandidate:
        return self.transition(CandidateStatus.ACTIVE, evidence=evidence)

    def reject(self, evidence: Optional[str] = None) -> ProcedureCandidate:
        return self.transition(CandidateStatus.REJECTED, evidence=evidence)

    def rollback(self, evidence: Optional[str] = None) -> ProcedureCandidate:
        return self.transition(CandidateStatus.ROLLED_BACK, evidence=evidence)

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _is_failure_exit(current: CandidateStatus, to_status: CandidateStatus) -> bool:
        if current is CandidateStatus.ACTIVE and to_status is CandidateStatus.ROLLED_BACK:
            return True
        if to_status is CandidateStatus.REJECTED and current in _FAILURE_EXITS:
            return True
        return False