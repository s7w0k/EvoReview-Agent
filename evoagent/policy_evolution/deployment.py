"""Real canary router + deployment manager (plan section 12).

``PolicyDeploymentManager`` owns the live lifecycle of a policy deployment:

* stable lane assignment via ``hash(task_id + deployment_id)`` -- a retried
  task always returns to the same lane (section 12.2);
* a DRAFT -> REPLAY_PASSED -> SHADOW -> CANARY -> PROMOTED deployment and a
  genuine rollback that cuts traffic back to baseline (sections 12.3 / 12.6);
* staged canary rollout ``5% -> 10% -> 25% -> 50% -> 100%`` gated by sample,
  duration and hard-safety (section 12.4);
* an exposure log so production metrics stay attributable (section 12.5).
"""
import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from evoagent.policy.models import ExecutionPolicy

# Default canary rollout ladder and the guarantees each stage needs.
DEFAULT_TRAFFIC_LADDER: Tuple[float, ...] = (0.05, 0.10, 0.25, 0.50, 1.00)


class DeploymentState(str, Enum):
    DRAFT = "DRAFT"
    REPLAY_PASSED = "REPLAY_PASSED"
    SHADOW = "SHADOW"
    CANARY = "CANARY"
    PROMOTED = "PROMOTED"
    ROLLED_BACK = "ROLLED_BACK"
    PAUSED = "PAUSED"


@dataclass
class CanaryStage:
    traffic_share: float
    min_sample: int = 5
    min_duration_seconds: float = 60.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "traffic_share": self.traffic_share,
            "min_sample": self.min_sample,
            "min_duration_seconds": self.min_duration_seconds,
        }


@dataclass
class PolicyDeployment:
    """A single policy deployment and its live phase."""

    deployment_id: str
    policy_id: str
    baseline_policy_id: str
    tenant_id: str
    repository: str
    risk_level: str
    state: DeploymentState = DeploymentState.DRAFT
    stage_index: int = -1
    traffic_share: float = 0.0
    hypothesis_id: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    staged_at: Optional[float] = None
    stage_entered_at: Optional[float] = None
    promoted_at: Optional[float] = None
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "deployment_id": self.deployment_id,
            "policy_id": self.policy_id,
            "baseline_policy_id": self.baseline_policy_id,
            "tenant_id": self.tenant_id,
            "repository": self.repository,
            "risk_level": self.risk_level,
            "state": self.state.value,
            "stage_index": self.stage_index,
            "traffic_share": self.traffic_share,
            "hypothesis_id": self.hypothesis_id,
            "created_at": self.created_at,
            "staged_at": self.staged_at,
            "stage_entered_at": self.stage_entered_at,
            "promoted_at": self.promoted_at,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "PolicyDeployment":
        data = dict(value)
        data["state"] = DeploymentState(data.get("state", DeploymentState.DRAFT))
        return cls(**{
            key: data[key] for key in (
                "deployment_id", "policy_id", "baseline_policy_id", "tenant_id",
                "repository", "risk_level", "state", "stage_index",
                "traffic_share", "hypothesis_id", "created_at", "staged_at",
                "stage_entered_at", "promoted_at", "notes",
            ) if key in data
        })


@dataclass
class ExposureRecord:
    """One task's routing decision so metrics can be attributed (section 12.5)."""

    task_id: str
    deployment_id: str
    lane: str                       # "baseline" | "candidate"
    baseline_version: int
    candidate_version: int
    traffic_share: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "deployment_id": self.deployment_id,
            "lane": self.lane,
            "baseline_version": self.baseline_version,
            "candidate_version": self.candidate_version,
            "traffic_share": self.traffic_share,
        }


class DeploymentNotFound(Exception):
    pass


class IllegalDeploymentState(Exception):
    pass


class PolicyDeploymentManager:
    """Routes tasks to baseline / candidate lanes and drives the rollout."""

    def __init__(
        self,
        *,
        traffic_ladder: Optional[Tuple[float, ...]] = None,
        repo=None,
        exposure_repo=None,
        min_sample: int = 5,
        min_duration_seconds: float = 60.0,
    ):
        self._ladder = traffic_ladder or DEFAULT_TRAFFIC_LADDER
        self._stages = [
            CanaryStage(share, min_sample, min_duration_seconds)
            for share in self._ladder
        ]
        # Injectable persistence (optional, default in-memory).
        self._repo = repo
        self._exposure_repo = exposure_repo
        self._deployments: Dict[str, PolicyDeployment] = {}
        self._exposure: List[ExposureRecord] = []
        # (tenant_id, repository, risk_level) -> current deployment_id
        self._active: Dict[Tuple[str, str, str], str] = {}
        # policy_id -> ExecutionPolicy (both baseline and candidate).
        self._policies: Dict[str, ExecutionPolicy] = {}

    # -- registration -------------------------------------------------------

    def register_policy(self, policy: ExecutionPolicy) -> None:
        self._policies[policy.policy_id] = policy

    def create(
        self,
        candidate: ExecutionPolicy,
        baseline: ExecutionPolicy,
        *,
        tenant_id: str = "default",
        repository: str = "",
        risk_level: str = "low",
        hypothesis_id: Optional[str] = None,
    ) -> PolicyDeployment:
        """Create a DRAFT deployment of ``candidate`` against ``baseline``."""
        self.register_policy(candidate)
        self.register_policy(baseline)
        deployment_id = hashlib.sha256(
            f"{candidate.policy_id}:{candidate.policy_version}:{risk_level}"
            .encode("utf-8")).hexdigest()[:16]
        deployment = PolicyDeployment(
            deployment_id=deployment_id,
            policy_id=candidate.policy_id,
            baseline_policy_id=baseline.policy_id,
            tenant_id=tenant_id,
            repository=repository,
            risk_level=risk_level,
            state=DeploymentState.DRAFT,
            hypothesis_id=hypothesis_id,
        )
        self._deployments[deployment_id] = deployment
        self._active[(tenant_id, repository, risk_level)] = deployment_id
        self._flush(deployment)
        return deployment

    # -- lifecycle transitions ----------------------------------------------

    def replay_pass(self, deployment_id: str) -> PolicyDeployment:
        """DRAFT -> REPLAY_PASSED (candidate survived the replay dataset)."""
        deployment = self._require(deployment_id)
        self._expect(deployment, {DeploymentState.DRAFT},
                     "replay-pass", target=DeploymentState.REPLAY_PASSED)
        deployment.state = DeploymentState.REPLAY_PASSED
        deployment.notes.append("replay gate passed")
        self._flush(deployment)
        return deployment

    def shadow(self, deployment_id: str) -> PolicyDeployment:
        """REPLAY_PASSED -> SHADOW (observe, no traffic shifted yet)."""
        deployment = self._require(deployment_id)
        self._expect(deployment, {DeploymentState.REPLAY_PASSED},
                     "shadow", target=DeploymentState.SHADOW)
        deployment.state = DeploymentState.SHADOW
        deployment.notes.append("moved to shadow")
        self._flush(deployment)
        return deployment

    def start_canary(self, deployment_id: str) -> PolicyDeployment:
        """SHADOW -> CANARY at the smallest traffic share (5%)."""
        deployment = self._require(deployment_id)
        self._expect(deployment, {DeploymentState.SHADOW},
                     "start-canary", target=DeploymentState.CANARY)
        deployment.state = DeploymentState.CANARY
        deployment.stage_index = 0
        deployment.traffic_share = self._stages[0].traffic_share
        deployment.staged_at = time.time()
        deployment.stage_entered_at = time.time()
        deployment.notes.append(f"canary started at {deployment.traffic_share:.0%}")
        self._flush(deployment)
        return deployment

    def advance_stage(
        self,
        deployment_id: str,
        *,
        min_sample_ok: bool,
        min_duration_ok: bool,
        hard_safety_pass: bool,
    ) -> PolicyDeployment:
        """Move one stage up the ladder when every stage gate holds.

        ``hard_safety_pass`` is authoritative: a false value rolls the
        deployment straight back to baseline instead of advancing.
        """
        deployment = self._require(deployment_id)
        self._expect(deployment, {DeploymentState.CANARY},
                     "advance-stage", target=DeploymentState.CANARY)

        if not hard_safety_pass:
            return self.rollback(
                deployment_id, reason="hard safety gate failed during canary")

        if not (min_sample_ok and min_duration_ok):
            deployment.notes.append("stage gates not met; holding")
            self._flush(deployment)
            return deployment

        next_index = deployment.stage_index + 1
        if next_index >= len(self._stages):
            # Reached 100% and passed -> promote fully.
            return self.promote(deployment_id)

        deployment.stage_index = next_index
        deployment.traffic_share = self._stages[next_index].traffic_share
        deployment.stage_entered_at = time.time()
        deployment.notes.append(
            f"advanced to {deployment.traffic_share:.0%}")
        self._flush(deployment)
        return deployment

    def promote(self, deployment_id: str) -> PolicyDeployment:
        """CANARY -> PROMOTED: candidate becomes fully active (100%)."""
        deployment = self._require(deployment_id)
        self._expect(deployment, {DeploymentState.CANARY},
                     "promote", target=DeploymentState.PROMOTED)
        deployment.state = DeploymentState.PROMOTED
        deployment.traffic_share = 1.0
        deployment.promoted_at = time.time()
        deployment.notes.append("promoted to full active")
        self._flush(deployment)
        return deployment

    def rollback(
        self,
        deployment_id: str,
        reason: str = "automatic rollback",
    ) -> PolicyDeployment:
        """CANARY / SHADOW -> ROLLED_BACK and cut traffic back to baseline.

        This is the *real* rollback of section 12.6: the candidate is disabled,
        the baseline is restored as the active policy, and every new task
        resolves to baseline.
        """
        deployment = self._require(deployment_id)
        if deployment.state not in (
                DeploymentState.CANARY, DeploymentState.SHADOW,
                DeploymentState.REPLAY_PASSED):
            raise IllegalDeploymentState(
                f"cannot roll back deployment {deployment_id!r} from "
                f"{deployment.state.value}")
        deployment.state = DeploymentState.ROLLED_BACK
        deployment.traffic_share = 0.0
        deployment.notes.append(f"rolled back: {reason}")
        self._flush(deployment)
        return deployment

    def pause(self, deployment_id: str) -> PolicyDeployment:
        deployment = self._require(deployment_id)
        if deployment.state not in (
                DeploymentState.CANARY, DeploymentState.SHADOW):
            raise IllegalDeploymentState(
                f"cannot pause a {deployment.state.value} deployment")
        deployment.state = DeploymentState.PAUSED
        deployment.traffic_share = 0.0
        deployment.notes.append("deployment paused")
        self._flush(deployment)
        return deployment

    # -- resolution ---------------------------------------------------------

    def resolve_policy(
        self,
        tenant_id: str,
        repository: str,
        risk_level: str,
        task_id: str,
    ) -> ExecutionPolicy:
        """Return the ``ExecutionPolicy`` a task should actually use.

        A live canary deployment routes the task to a lane via a stable hash of
        ``task_id + deployment_id`` and records the exposure.  Otherwise the
        active (promoted) or baseline policy is returned.
        """
        deployment = self._active_deployment(
            tenant_id, repository, risk_level)

        # No deployment -> the registered baseline or a hard default.
        if deployment is None:
            baseline = self._policies.get(self._default_baseline_id(
                tenant_id, repository, risk_level))
            if baseline is None:
                raise DeploymentNotFound(
                    f"no policy available for {tenant_id}/{repository}/{risk_level}")
            return baseline

        # Fully adopted -> the candidate policy is the active one.
        if deployment.state is DeploymentState.PROMOTED:
            return self._policies[deployment.policy_id]

        if deployment.state is not DeploymentState.CANARY:
            # DRAFT / REPLAY_PASSED / SHADOW / ROLLED_BACK / PAUSED -> baseline.
            return self._policies[deployment.baseline_policy_id]

        # Stable canary lane assignment (section 12.2).
        lane = self._lane(task_id, deployment.deployment_id,
                          deployment.traffic_share)
        baseline = self._policies[deployment.baseline_policy_id]
        candidate = self._policies[deployment.policy_id]
        self._record_exposure(
            task_id, deployment, lane,
            baseline_version=baseline.policy_version,
            candidate_version=candidate.policy_version,
        )
        return candidate if lane == "candidate" else baseline

    def active_deployment(
        self, tenant_id: str, repository: str, risk_level: str,
    ) -> Optional[PolicyDeployment]:
        return self._active_deployment(tenant_id, repository, risk_level)

    def exposure(self) -> List[ExposureRecord]:
        return list(self._exposure)

    # -- internals ----------------------------------------------------------

    def _active_deployment(self, tenant_id, repository, risk_level) -> Optional[PolicyDeployment]:
        deployment_id = self._active.get((tenant_id, repository, risk_level))
        return self._deployments.get(deployment_id) if deployment_id else None

    def _default_baseline_id(self, tenant_id, repository, risk_level) -> str:
        return f"baseline-{risk_level}"

    def _require(self, deployment_id: str) -> PolicyDeployment:
        deployment = self._deployments.get(deployment_id)
        if deployment is None:
            raise DeploymentNotFound(
                f"no deployment {deployment_id!r}")
        return deployment

    def _expect(self, deployment: PolicyDeployment, allowed, label, *, target):
        if deployment.state not in allowed:
            raise IllegalDeploymentState(
                f"cannot {label} deployment {deployment.deployment_id!r} from "
                f"{deployment.state.value} (target {target.value})")

    def _lane(self, task_id: str, deployment_id: str, traffic_share: float) -> str:
        hashed = int(hashlib.sha256(
            f"{task_id}:{deployment_id}".encode("utf-8")).hexdigest()[:8], 16)
        percentage = (hashed % 100) / 100.0
        return "candidate" if percentage < traffic_share else "baseline"

    def _record_exposure(self, task_id, deployment, lane, *,
                     baseline_version, candidate_version) -> None:
        record = ExposureRecord(
            task_id=task_id,
            deployment_id=deployment.deployment_id,
            lane=lane,
            baseline_version=baseline_version,
            candidate_version=candidate_version,
            traffic_share=deployment.traffic_share,
        )
        self._exposure.append(record)
        if self._exposure_repo is not None:
            self._exposure_repo.add(record)

    def _flush(self, deployment: PolicyDeployment) -> None:
        if self._repo is not None:
            self._repo.save_deployment(
                deployment.deployment_id, deployment.to_dict())