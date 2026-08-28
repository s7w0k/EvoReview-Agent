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
from evoagent.reviewer import LocalRuleReviewer, OpenAICompatibleReviewer, Reviewer
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
    ))


class _HarnessAdapter(BaseEvaluationAdapter):
    """Base for the Current and Evolved adapters that go through the real Harness.

    Reuses the exact :class:`ReviewService` production path (identical policy
    resolution route + ``ReviewHarness`` build) so attributed artifacts
    (DecisionTrace / ReplaySnapshot / Outcome / Exposure) are produced per case.
    """

    def __init__(self, db_path: str, extra_reviewers: Optional[List[Reviewer]] = None):
        self.db_path = db_path
        self.extra_reviewers = list(extra_reviewers or [])
        self.service: Optional[ReviewService] = None

    def _ensure_service(self) -> ReviewService:
        if self.service is None:
            self.service = build_evaluation_service(self.db_path)
        return self.service

    def _lineup(self, svc: ReviewService) -> List[Reviewer]:
        reviewers = [
            item for item in svc.registry.reviewers()
            if not isinstance(item, OpenAICompatibleReviewer)
        ]
        return reviewers + list(self.extra_reviewers)

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
        try:
            context = svc._resolve_execution_context(
                task_id, repository, pull_request, diff, EVAL_TENANT)
            coordinator = svc._build_coordinator(
                self._lineup(svc), execution_policy=context.execution_policy)
            harness = svc._build_harness(
                coordinator, context.execution_policy, context)
            report = harness.run(task_id, repository, pull_request, diff, EVAL_TENANT)
            findings = list(report.findings)
        except Exception as exc:  # noqa: BLE001 - capture unmet execution success
            error = str(exc)[:1000]
        latency_ms = (time.monotonic() - started) * 1000.0
        return self._telemetry(svc, task_id, findings, latency_ms, context, error)

    def _telemetry(self, svc: ReviewService, task_id: str, findings: List[Finding],
                   latency_ms: float, context, error: Optional[str]) -> EvaluationExecutionResult:
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
        try:
            agent_steps = len(trace.agent_actions()) if trace is not None else 0
        except Exception:  # noqa: BLE001
            agent_steps = 0
        try:
            tool_calls = len(trace.tool_path()) if trace is not None else 0
        except Exception:  # noqa: BLE001
            tool_calls = 0
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

    def __init__(self, db_path: str, evolved_reviewers: List[Reviewer]):
        super().__init__(db_path, extra_reviewers=evolved_reviewers)


__all__ = [
    "EvaluationExecutionResult",
    "SingleAgentEvaluationAdapter",
    "LegacyMultiAgentEvaluationAdapter",
    "CurrentHarnessEvaluationAdapter",
    "EvolvedHarnessEvaluationAdapter",
    "build_evaluation_service",
]