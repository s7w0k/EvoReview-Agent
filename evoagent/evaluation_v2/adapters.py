"""Unified reviewer adapters for Evaluation Harness V2.

Each adapter exposes the same :class:`EvaluationExecutionResult` so the runner can
score every system with the *same* one-to-one matcher (only the evaluated system
changes; the scorer is fixed for the duration of a comparison).

- ``SingleAgentEvaluationAdapter``: the original single ``LocalRuleReviewer``.
- ``LegacyMultiAgentEvaluationAdapter``: the original deterministic collaboration
  ``LocalRuleReviewer() + ContextRuleReviewer()`` (reproduces the 82.5% baseline).
- ``CurrentHarnessEvaluationAdapter``: the real production path
  ``RiskProfile -> ExecutionPolicy -> ReviewExecutionContext -> ReviewHarness ->
   AgentRuntime -> MultiAgentCoordinator -> Critic/Evidence/Verifier/Arbiter``.
- ``EvolvedHarnessEvaluationAdapter``: the *same* harness with an added frozen
  declarative evolution candidate; the only difference is the frozen policy/skill.
"""
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from evoagent.agents import MultiAgentCoordinator
from evoagent.config import Settings
from evoagent.diff_parser import parse_unified_diff
from evoagent.evaluation_benchmark import ContextRuleReviewer
from evoagent.models import Finding, Severity
from evoagent.reviewer import (
    LocalRuleReviewer, OpenAICompatibleReviewer, Reviewer, SecurityRuleReviewer,
)
from evoagent.service import ReviewService
from evoagent.storage.repositories.recovery import RecoveryRepository

EVAL_TENANT = "evaluation-v2"
MAX_DIFF_BYTES = 300000


def _severity_of(value: Any) -> Severity:
    if isinstance(value, Severity):
        return value
    return Severity(str(value).lower())


@dataclass
class EvaluationExecutionResult:
    """Unified adapter output: findings plus runtime / governance telemetry."""

    findings: List[Finding] = field(default_factory=list)
    success: bool = True
    latency_ms: float = 0.0
    agent_steps: int = 0
    tool_calls: int = 0
    recovery_attempts: int = 0
    recovery_successes: int = 0
    policy_denials: int = 0
    circuit_breaker_trips: int = 0
    timeouts: int = 0
    side_effect_blocks: int = 0
    policy_id: str = ""
    policy_version: int = 0
    deployment_lane: str = ""
    decision_trace_created: bool = False
    replay_snapshot_created: bool = False
    trace_event_count: int = 0
    resolved_policy: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    # Ground-truth signal that the multi-agent collaboration actually executed,
    # sourced from the coordinator's collaboration_summary (not the harness
    # decision-trace, which only carries harness-lifecycle events).
    collaboration: Dict[str, Any] = field(default_factory=dict)
    architecture: str = ""
    called_agents: List[str] = field(default_factory=list)
    graph_revision: int = 0
    graph_shapes: List[dict] = field(default_factory=list)
    loop_steps_by_agent: Dict[str, int] = field(default_factory=dict)
    parallel_batches: List[dict] = field(default_factory=list)
    replan_count: int = 0
    replan_targets: List[str] = field(default_factory=list)
    feature_flags: Dict[str, Any] = field(default_factory=dict)
    skill_invocations: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        value: Dict[str, Any] = {
            "success": self.success,
            "latency_ms": round(self.latency_ms, 4),
            "agent_steps": self.agent_steps,
            "tool_calls": self.tool_calls,
            "recovery_attempts": self.recovery_attempts,
            "recovery_successes": self.recovery_successes,
            "policy_denials": self.policy_denials,
            "circuit_breaker_trips": self.circuit_breaker_trips,
            "timeouts": self.timeouts,
            "side_effect_blocks": self.side_effect_blocks,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "deployment_lane": self.deployment_lane,
            "decision_trace_created": self.decision_trace_created,
            "replay_snapshot_created": self.replay_snapshot_created,
            "trace_event_count": self.trace_event_count,
            "resolved_policy": self.resolved_policy,
            "error": self.error,
            "collaboration": self.collaboration,
            "architecture": self.architecture,
            "called_agents": list(self.called_agents),
            "graph_revision": self.graph_revision,
            "graph_shapes": list(self.graph_shapes),
            "loop_steps_by_agent": dict(self.loop_steps_by_agent),
            "parallel_batches": list(self.parallel_batches),
            "replan_count": self.replan_count,
            "replan_targets": list(self.replan_targets),
            "feature_flags": dict(self.feature_flags),
            "skill_invocations": dict(self.skill_invocations),
        }
        value["findings"] = [
            {
                "path": f.path, "line": f.line, "rule_id": f.rule_id,
                "severity": f.severity.value, "source": f.source_skill or "rule",
            }
            for f in self.findings
        ]
        return value


class BaseEvaluationAdapter:
    name = "base"

    def review_case(self, case: dict) -> EvaluationExecutionResult:
        raise NotImplementedError

    def close(self) -> None:  # noqa: B027 - default no-op
        return


class _ReviewerAdapter(BaseEvaluationAdapter):
    """Adapts a plain :class:`Reviewer` (no harness) to the unified result."""

    def __init__(self, reviewer: Reviewer):
        self.reviewer = reviewer
        self.name = reviewer.name

    def review_case(self, case: dict) -> EvaluationExecutionResult:
        parsed = parse_unified_diff(case["diff"])
        started = time.monotonic()
        try:
            findings = self.reviewer.review(case["diff"], parsed)
            return EvaluationExecutionResult(
                findings=list(findings), success=True,
                latency_ms=(time.monotonic() - started) * 1000.0,
                resolved_policy={"target": "legacy-reviewer"},
            )
        except Exception as exc:  # noqa: BLE001 - surface as unmet execution success
            return EvaluationExecutionResult(
                findings=[], success=False,
                latency_ms=(time.monotonic() - started) * 1000.0,
                error=str(exc)[:1000],
            )


class SingleAgentEvaluationAdapter(_ReviewerAdapter):
    name = "single_agent"

    def __init__(self) -> None:
        super().__init__(LocalRuleReviewer())


class LegacyMultiAgentEvaluationAdapter(_ReviewerAdapter):
    name = "legacy_multi_agent"

    def __init__(self) -> None:
        super().__init__(MultiAgentCoordinator([LocalRuleReviewer(), ContextRuleReviewer()]))


def build_evaluation_service(db_path: str) -> ReviewService:
    """Create an isolated ``ReviewService`` on a fresh SQLite evaluation store."""
    return ReviewService(Settings(
        host="127.0.0.1", port=8080, db_path=db_path, max_diff_bytes=MAX_DIFF_BYTES,
        max_steps=8, timeout_seconds=120, llm_base_url="", llm_api_key="",
        llm_model="", github_webhook_secret="", github_token="",
        auto_post_review=False,
        agent_architecture="six-agent-v2",
    ))


def build_evaluation_leader(
    svc: ReviewService, execution_policy, evolved_skill: Optional[Reviewer] = None,
    candidate_id: str = "", reviewers: Optional[List[Reviewer]] = None,
):
    """Build stable/evolved leaders with the candidate as the sole variable.

    Plan §Phase 3: the candidate is composed into the specialist(s) that own
    its rules.  A security candidate joins ``security_reviewers``, a reliability
    candidate joins ``reliability_reviewers``, and a shared candidate joins both,
    so a Reliability mapping is never silently side-lined.
    """
    from evoagent.reviewer import ReliabilityRuleReviewer
    from evoagent.loop_agents.planning.risk_signals import (
        SECURITY, RELIABILITY, SHARED)

    security_reviewers: List[Reviewer] = [SecurityRuleReviewer()]
    security_reviewer_ids = ["security-rule@1"]
    reliability_reviewers: List[Reviewer] = [ReliabilityRuleReviewer()]
    reliability_reviewer_ids = ["reliability-rule@1"]
    if evolved_skill is not None:
        candidate_name = candidate_id or str(
            getattr(evolved_skill, "name", "evolved-skill"))
        artifact = getattr(evolved_skill, "artifact", {}) or {}
        domain = str(artifact.get("domain") or SHARED)
        if domain in (SECURITY, RELIABILITY, SHARED):
            pass  # valid domain
        else:
            domain = SHARED
        if domain in (SECURITY, SHARED):
            security_reviewers.append(evolved_skill)
            security_reviewer_ids.append(candidate_name)
        if domain in (RELIABILITY, SHARED):
            reliability_reviewers.append(evolved_skill)
            reliability_reviewer_ids.append(candidate_name)
    if reviewers is None:
        reviewers = [
            item for item in svc.registry.reviewers()
            if not isinstance(item, OpenAICompatibleReviewer)
        ]
    return svc._build_leader(
        list(reviewers), execution_policy=execution_policy,
        tool_context_config={
            "security_reviewers": security_reviewers,
            "security_reviewer_ids": security_reviewer_ids,
            "reliability_reviewers": reliability_reviewers,
            "reliability_reviewer_ids": reliability_reviewer_ids,
        },
    )


class _HarnessAdapter(BaseEvaluationAdapter):
    """Base for the Current and Evolved adapters that go through the real Harness.

    Reuses the exact :class:`ReviewService` production path (identical policy
    resolution route + ``ReviewHarness`` build) so attributed artifacts
    (DecisionTrace / ReplaySnapshot / Outcome / Exposure) are produced per case.
    """

    def __init__(
        self, db_path: str, extra_reviewers: Optional[List[Reviewer]] = None,
        candidate_id: str = "",
    ):
        self.db_path = db_path
        self.extra_reviewers = list(extra_reviewers or [])
        if len(self.extra_reviewers) > 1:
            raise ValueError("Evaluation V2 supports one frozen evolved skill")
        self.evolved_skill = self.extra_reviewers[0] if self.extra_reviewers else None
        self.candidate_id = candidate_id
        self.service: Optional[ReviewService] = None

    def _ensure_service(self) -> ReviewService:
        if self.service is None:
            self.service = build_evaluation_service(self.db_path)
        return self.service

    def validate_execution(
        self, case: dict, result: EvaluationExecutionResult,
    ) -> None:
        """Fail fast when a formal result is not from the real Six-Agent DAG."""
        violations = []
        if result.architecture != "six-agent-v2":
            violations.append("architecture is not six-agent-v2")
        if not result.graph_shapes:
            violations.append("runtime graph is empty")
        if not result.called_agents:
            violations.append("no specialist agent was called")
        if case.get("expected_findings") and not (
                {"security-agent", "reliability-agent"}
                & set(result.called_agents)):
            violations.append("risk case did not call a risk specialist")
        if self.evolved_skill is not None and bool(
                {"security-agent", "reliability-agent"} & set(result.called_agents)):
            candidate_id = self.candidate_id or str(
                getattr(self.evolved_skill, "name", ""))
            if not candidate_id or result.skill_invocations.get(candidate_id, 0) < 1:
                violations.append("frozen candidate was loaded but not invoked")
        if violations:
            raise RuntimeError(
                "Evaluation V2 runtime wiring gate failed for %s: %s" % (
                    case.get("id", "unknown"), "; ".join(violations)))

    def _lineup(self, svc: ReviewService) -> List[Reviewer]:
        reviewers = [
            item for item in svc.registry.reviewers()
            if not isinstance(item, OpenAICompatibleReviewer)
        ]
        return reviewers

    def review_case(self, case: dict) -> EvaluationExecutionResult:
        svc = self._ensure_service()
        task_id = "eval-v2-%s-%s" % (self.name, case["id"])
        repository = str(case["repository"])
        pull_request = int(case["pull_request"])
        diff = str(case["diff"])
        started = time.monotonic()
        findings: List[Finding] = []
        error: Optional[str] = None
        context = None
        coordination: Dict[str, Any] = {}
        leader = None
        try:
            context = svc._resolve_execution_context(
                task_id, repository, pull_request, diff, EVAL_TENANT)
            leader = build_evaluation_leader(
                svc, context.execution_policy,
                evolved_skill=self.evolved_skill,
                candidate_id=self.candidate_id,
                reviewers=self._lineup(svc),
            )
            harness = svc._build_harness(
                leader, context.execution_policy, context)
            report = harness.run(task_id, repository, pull_request, diff, EVAL_TENANT)
            findings = list(report.findings)
            try:
                coordination = (
                    leader.runtime_summary()
                    if hasattr(leader, "runtime_summary") else {}) or {}
            except Exception:  # noqa: BLE001
                coordination = {}
        except Exception as exc:  # noqa: BLE001 - capture unmet execution success
            error = str(exc)[:1000]
        latency_ms = (time.monotonic() - started) * 1000.0
        return self._telemetry(
            svc, task_id, findings, latency_ms, context, error, coordination)

    def _telemetry(self, svc: ReviewService, task_id: str, findings: List[Finding],
                   latency_ms: float, context, error: Optional[str],
                   collaboration: Optional[Dict[str, Any]] = None) -> EvaluationExecutionResult:
        trace = None
        snapshots: List[Any] = []
        events: List[Any] = []
        recovery_events: List[Dict[str, Any]] = []
        try:
            trace = svc.trace_repository.trace(task_id)
        except Exception:  # noqa: BLE001
            trace = None
        try:
            snapshots = svc.replay_repository.snapshots_for_task(task_id)
        except Exception:  # noqa: BLE001
            snapshots = []
        try:
            recovery_events = RecoveryRepository(svc.control_store).for_task(task_id)
        except Exception:  # noqa: BLE001
            recovery_events = []
        if trace is not None:
            events = getattr(trace, "events", []) or []
        # ``agent_actions`` is a *property* on DecisionTrace; one-shot rule
        # specialists take no tool actions, so this is honestly 0 for the harness
        # systems -- the multi-agent DAG is proven by ``collaboration`` instead.
        try:
            agent_steps = len(trace.agent_actions) if trace is not None else 0
        except Exception:  # noqa: BLE001
            agent_steps = 0
        try:
            tool_calls = len(trace.tool_path()) if trace is not None else 0
        except Exception:  # noqa: BLE001
            tool_calls = 0
        runtime_steps = dict(
            (collaboration or {}).get("loop_steps_by_agent") or {})
        runtime_tools = dict(
            (collaboration or {}).get("tool_calls_by_agent") or {})
        agent_steps = max(agent_steps, sum(int(v or 0) for v in runtime_steps.values()))
        tool_calls = max(tool_calls, sum(int(v or 0) for v in runtime_tools.values()))
        rec_attempts = len(recovery_events)
        rec_successes = sum(
            1 for event in recovery_events
            if str(event.get("status") or event.get("outcome") or "").lower()
            in {"recovered", "success", "succeeded", "resolved"}
        )
        policy_detail = {}
        if context is not None:
            policy = getattr(context, "execution_policy", None)
            agents = getattr(policy, "agents", None)
            budget = getattr(policy, "budget", None)
            verification = getattr(policy, "verification", None)
            policy_detail = {
                "resolved_policy": getattr(context, "policy_id", ""),
                "max_steps": getattr(budget, "max_steps", None) if budget else None,
                "timeout_seconds": getattr(budget, "timeout_seconds", None) if budget else None,
                "max_tool_calls": getattr(budget, "max_tool_calls", None) if budget else None,
                "enabled_agents": (
                    list(getattr(agents, "enabled_agents", []) or []) if agents else []
                ),
            }
            if verification is not None:
                policy_detail["verification"] = {
                    "critic_required": getattr(verification, "critic_required", None),
                    "evidence_required": getattr(verification, "evidence_required", None),
                    "verifier_required": getattr(verification, "verifier_required", None),
                    "sandbox_required": getattr(verification, "sandbox_required", None),
                }
        return EvaluationExecutionResult(
            findings=findings,
            success=error is None and len(findings) >= 0,
            latency_ms=latency_ms,
            agent_steps=agent_steps,
            tool_calls=tool_calls,
            recovery_attempts=rec_attempts,
            recovery_successes=rec_successes,
            policy_denials=0,
            circuit_breaker_trips=0,
            policy_id=getattr(context, "policy_id", "") if context else "",
            policy_version=getattr(context, "runtime_policy_version", 0) if context else 0,
            deployment_lane=getattr(context, "deployment_lane", "") if context else "",
            decision_trace_created=trace is not None,
            replay_snapshot_created=len(snapshots) > 0,
            trace_event_count=len(events),
            resolved_policy=policy_detail,
            error=error,
            collaboration=dict(collaboration or {}),
            architecture=str((collaboration or {}).get("architecture") or ""),
            called_agents=list((collaboration or {}).get("called_agents") or []),
            graph_revision=int((collaboration or {}).get("graph_revision") or 0),
            graph_shapes=list((collaboration or {}).get("graph_shapes") or []),
            loop_steps_by_agent=dict(
                (collaboration or {}).get("loop_steps_by_agent") or {}),
            parallel_batches=list((collaboration or {}).get("parallel_batches") or []),
            replan_count=int((collaboration or {}).get("replan_count") or 0),
            replan_targets=list((collaboration or {}).get("replan_targets") or []),
            feature_flags=dict(
                (collaboration or {}).get("feature_flags_snapshot") or {}),
            skill_invocations=dict(
                (collaboration or {}).get("skill_invocations") or {}),
        )

    def close(self) -> None:
        if self.service is not None:
            try:
                self.service.close()
            except Exception:  # noqa: BLE001
                pass
            self.service = None


class CurrentHarnessEvaluationAdapter(_HarnessAdapter):
    name = "current_harness"

    def __init__(self, db_path: str):
        super().__init__(db_path, extra_reviewers=[])


class EvolvedHarnessEvaluationAdapter(_HarnessAdapter):
    name = "evolved_candidate"

    def __init__(
        self, db_path: str, evolved_reviewers: List[Reviewer],
        candidate_id: str = "",
    ):
        reviewer = evolved_reviewers[0] if evolved_reviewers else None
        if not candidate_id and reviewer is not None:
            artifact = getattr(reviewer, "artifact", {}) or {}
            if artifact.get("name"):
                candidate_id = "eval-v2-%s" % artifact["name"]
        super().__init__(
            db_path, extra_reviewers=evolved_reviewers,
            candidate_id=candidate_id)


__all__ = [
    "EvaluationExecutionResult",
    "SingleAgentEvaluationAdapter",
    "LegacyMultiAgentEvaluationAdapter",
    "CurrentHarnessEvaluationAdapter",
    "EvolvedHarnessEvaluationAdapter",
    "build_evaluation_service",
    "build_evaluation_leader",
]
