import datetime
import hashlib
import logging
import threading
import time
import uuid
from dataclasses import replace
from typing import Any, Dict, Optional

from .agents import MultiAgentCoordinator
from .auth import AuthManager
from .config import Settings
from .context_manager import ContextManager
from .evolution import EvolutionEngine
from .evolution_controller import EvolutionController, EvolutionJobError
from .experience import RULE_CANDIDATE, build_experience
from .fixer import SafeFixer
from .github import GitHubAppAuthenticator, GitHubClient
from .harness import ReviewHarness
from .metrics import metrics
from .memory import MemoryManager
from .models import TaskState, TraceEvent
from .observability import AlertManager, Observability
from .postgres_store import create_store
from .report import to_markdown
from .reviewer import (
    OpenAICompatibleReviewer, ReliabilityRuleReviewer, SecurityRuleReviewer,
)
from .diff_parser import parse_unified_diff
from .skills import SkillRegistry
from .skill_evolution import DeclarativeSkillReviewer, SkillEvolutionEngine
from .store import utc_now
from .task_queue import PermanentTaskError, TaskQueue
from . import skill_lifecycle
from .rollout import ReleaseManager
from .skill_curator import SkillCurator
from .semantic_reviewer import build_semantic_reviewer
from .confidence import parse_buckets
from .a2a.factory import build_remote_reviewers_typed
from .loop_agents.reviewer import build_six_agent_reviewer
from .policy import PolicyResolver, RiskProfiler
from .policy.codec import policy_from_dict, policy_to_dict
from .policy.defaults import default_policy
from .policy_evolution.candidate import CandidateOperation, PolicyCandidateGenerator
from .policy_evolution.deployment import PolicyDeploymentManager
from .recovery import RecoveryBudget, RecoveryManager
from .decision_trace import DecisionTrace
from .execution import ReviewExecutionContext
from .outcome_evolution import OutcomeStore
from .outcome_evolution.outcome import (
    Outcome,
    OutcomeAttribution,
    OutcomeKind,
    RuntimeMetrics,
)
from .storage.control_plane import create_control_plane_store
from .storage.repositories.decision_trace import PersistedDecisionTraceRepository
from .storage.repositories.deployment import DeploymentRepository
from .storage.repositories.lineage import LineageRepository
from .storage.repositories.outcome import OutcomeRepository
from .storage.repositories.policy_exposure import PolicyExposureRepository
from .storage.repositories.replay import ReplayRepository as PersistedReplayRepository
from .storage.repositories.runtime_policy import PersistedRuntimePolicyRepository
from .verifier import RepairVerifier
from .chat import (
    CHAT_INSIGHT_CATEGORIES,
    CHAT_SESSION_ACTIVE,
    ChatBusyError,
    ChatContextBuilder,
    ChatModelClient,
    ChatModelError,
    ChatModelNotConfigured,
    INSIGHT_NOTE_MAX,
    decode_model_output,
    insight_source_key,
    normalize_text,
    report_fingerprint,
)


logger = logging.getLogger(__name__)


class ReviewService:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.validate_evolution()
        self._close_lock = threading.Lock()
        self._closed = False
        self.llm_config = settings.resolved_llm()
        # Work Package 3: report-chat transport and context builder.  These are
        # independent of the review pipeline and only used when the dark
        # switch is enabled and an LLM is configured.
        self.chat_client = None
        self.chat_context_builder = ChatContextBuilder(
            context_tokens=settings.chat_context_tokens,
        )
        # Work Package 6: per-session concurrency guard for chat requests.
        self._chat_inflight = set()
        self._chat_inflight_lock = threading.Lock()
        if self.llm_config:
            self.chat_client = ChatModelClient(
                str(self.llm_config["base_url"]),
                str(self.llm_config["api_key"]),
                str(self.llm_config["model"]),
                provider=str(self.llm_config["provider"]),
                headers=dict(self.llm_config.get("headers") or {}),
                timeout=self.settings.chat_timeout_seconds,
            )
        skill_lifecycle.set_enabled(settings.skill_lifecycle_enabled)
        self.store = create_store(settings.database_url, settings.db_path)
        # Work Package 5: reconcile chat confirmations interrupted by a crash.
        self._reconcile_chat_state()
        self.context_manager = ContextManager(
            settings.context_max_tokens, settings.context_reserved_tokens
        )
        self.memory = MemoryManager(
            self.store, settings.memory_enabled, settings.memory_recall_limit,
            settings.memory_working_ttl_seconds,
        )
        self.observability = Observability(settings.otel_service_name, settings.otel_endpoint)
        # Closed-loop runtime-policy plumbing (plan section 5.2).
        self.risk_profiler = RiskProfiler()
        self.policy_resolver = PolicyResolver()
        # Durable control-plane store + repositories (convergence plan 2/3/4/5,
        # hardening plan Phase 4-7: backend selected by CONTROL_PLANE_BACKEND).
        control_store = create_control_plane_store(settings)
        self.control_store = control_store
        self.policy_repository = PersistedRuntimePolicyRepository(control_store)
        self.policy_deployment_repository = DeploymentRepository(control_store)
        self.policy_exposure_repository = PolicyExposureRepository(control_store)
        self.policy_deployment_manager = PolicyDeploymentManager(
            repo=self.policy_deployment_repository,
            exposure_repo=self.policy_exposure_repository,
        )
        # Bootstrapped baseline policies survive restarts and are registered so a
        # brand-new scope resolves to the right baseline (plan section 4.2).
        self._bootstrap_baselines()
        self.policy_deployment_manager.restore_active_deployments(
            policy_loader=self._load_control_policy)
        # Production-outcome mirror: in-memory store + durable log (section 8.3).
        self.outcome_store = OutcomeStore()
        self.outcome_repository = OutcomeRepository(control_store)
        self.recovery_manager = RecoveryManager(
            budget=RecoveryBudget(
                max_recovery_attempts=settings.recovery_max_attempts,
                max_replans=settings.recovery_max_replans,
                max_model_switches=settings.recovery_max_model_switches,
            ),
        )
        self.trace_repository = PersistedDecisionTraceRepository(control_store)
        self.replay_repository = PersistedReplayRepository(control_store)
        self.lineage_repository = LineageRepository(control_store)
        self.runtime_policy_version: Optional[int] = None
        self.registry = SkillRegistry(
            settings.skills_dir, settings.skill_sandbox, settings.skill_timeout_seconds,
            settings.skill_memory_mb, settings.skill_signing_key,
            settings.skill_container_image,
        )
        # A2A production integration (plan Phase 4): when endpoints are
        # configured the specialists run as Remote Agents with a local fallback;
        # otherwise they stay purely local and behavior is unchanged.
        a2a_specialists = self._build_a2a_reviewers()
        security_reviewer = a2a_specialists.get("security-agent") or SecurityRuleReviewer()
        reliability_reviewer = a2a_specialists.get("reliability-agent") or ReliabilityRuleReviewer()
        self.registry.register(
            "security-review", security_reviewer,
            "1.0.0", "Security, injection and secret detection",
        )
        self.registry.register(
            "reliability-review", reliability_reviewer,
            "1.0.0", "Reliability and observability review",
        )
        semantic = build_semantic_reviewer(settings.static_analyzer)
        if semantic is not None:
            self.registry.register(
                "semantic-review", semantic,
                "1.0.0", "AST / optional external static analysis layer",
            )
        if self.llm_config:
            active = self.store.get_active_skill_version("llm-review")
            self.registry.register(
                "llm-review",
                self._build_llm_reviewer(active["prompt"] if active else ""),
                "1.0.0", "Context-aware AI code review via %s" % self.llm_config["provider"],
            )
        self.registry.reload()
        self.reviewer = self._build_leader(self.registry.reviewers())
        self.harness = self._build_harness(self.reviewer)
        self.github = GitHubClient(settings.github_token)
        self.fixer = SafeFixer(RepairVerifier(
            settings.repair_test_command, settings.repair_verify_timeout_seconds
        ), ast_fixer_enabled=settings.ast_fixer_enabled,
            max_fix_files=settings.ast_fix_max_files,
            max_fix_lines=settings.ast_fix_max_lines)
        self.auth = AuthManager(
            self.store, settings.auth_secret, settings.session_ttl_seconds,
            settings.bootstrap_admin_username, settings.bootstrap_admin_password,
            settings.default_tenant_id,
        )
        self.releases = ReleaseManager(self.store)
        self.curator = SkillCurator(
            settings.curator_min_samples, settings.curator_stale_days
        )
        self.alerts = AlertManager(
            self.store, settings.alert_failure_rate, settings.alert_min_samples
        )
        self.evolution = EvolutionEngine(
            self.store,
            reviewer_factory=self._build_llm_reviewer if self.llm_config else None,
            min_cases=settings.eval_min_cases,
            max_cases=settings.eval_max_cases,
            min_improvement=settings.eval_min_improvement,
            min_holdout_cases=settings.eval_min_holdout_cases,
            max_metric_regression=settings.eval_max_metric_regression,
            eval_source=settings.eval_source,
            # Work Package 9: feedback trust guards on the prompt path.
            min_confirmers=settings.feedback_min_confirmers,
            trust_enabled=settings.feedback_trust_enabled,
            trust_min_ratio=settings.feedback_trust_min_accepted_ratio,
            quality_gates_enabled=settings.evolution_quality_gates_enabled,
            production_profile=settings.evolution_production_profile,
        )
        self.skill_evolution = SkillEvolutionEngine(
            self.store,
            min_cases=settings.eval_min_cases,
            max_cases=settings.eval_max_cases,
            min_improvement=settings.eval_min_improvement,
            min_holdout_cases=settings.eval_min_holdout_cases,
            max_metric_regression=settings.eval_max_metric_regression,
            experience_mode=settings.experience_mode,
            marginal_gate=settings.skill_marginal_gate,
            min_unique_tp=settings.skill_min_unique_tp,
            max_new_fp=settings.skill_max_new_fp,
            eval_source=settings.eval_source,
            # Work Package 9: feedback trust and overfitting protection.
            min_confirmers=settings.feedback_min_confirmers,
            trust_enabled=settings.feedback_trust_enabled,
            trust_min_ratio=settings.feedback_trust_min_accepted_ratio,
            compare_history=settings.evolution_compare_history,
            cooldown_minutes=settings.evolution_cooldown_minutes,
            holdout_rotation=settings.holdout_rotation,
            quality_gates_enabled=settings.evolution_quality_gates_enabled,
            production_profile=settings.evolution_production_profile,
        )
        self.queue = TaskQueue(
            self._process_queued, settings.async_workers, settings.redis_url,
            settings.queue_max_attempts, settings.queue_lease_seconds,
            self._on_dead_letter,
        )
        # Closed-loop WP1: durable evolution controller.  Inert by default;
        # delegates to the existing engines and never copies evaluation logic.
        self.evolution_controller = EvolutionController(
            self.store, settings, self.evolution, self.skill_evolution,
        )
        self.evolution_controller.start_scanner()

    def _build_a2a_reviewers(self) -> dict:
        """Build remote A2A specialist reviewers from ``EVOAGENT_A2A_ENDPOINTS``.

        Returns ``{agent_id: Reviewer}``.  Off by default: with no endpoints it
        returns ``{}`` and leaves ``self.agent_registry`` as ``None`` so the
        coordinator keeps the pre-A2A behavior exactly.  When configured, each
        discovered Remote Agent is given its own local domain reviewer as the
        fallback, and the registry is attached so unhealthy Remote Agents are
        dropped from routing.
        """
        endpoints = [
            item.strip()
            for item in self.settings.a2a_endpoints.split(",")
            if item.strip()
        ]
        if not endpoints:
            self.agent_registry = None
            return {}
        reviewers, registry = build_remote_reviewers_typed(
            endpoints,
            token=self.settings.a2a_token,
            timeout_seconds=self.settings.a2a_timeout_seconds,
            local_fallbacks={
                "security-agent": SecurityRuleReviewer(),
                "reliability-agent": ReliabilityRuleReviewer(),
            },
        )
        self.agent_registry = registry
        return {reviewer.agent_id: reviewer for reviewer in reviewers}

    def _build_llm_reviewer(self, prompt: str = "") -> OpenAICompatibleReviewer:
        if not self.llm_config:
            raise RuntimeError("no LLM provider is configured")
        return OpenAICompatibleReviewer(
            str(self.llm_config["base_url"]),
            str(self.llm_config["api_key"]),
            str(self.llm_config["model"]),
            self.settings.timeout_seconds,
            system_prompt=prompt,
            provider=str(self.llm_config["provider"]),
            extra_headers=dict(self.llm_config.get("headers") or {}),
        )

    def _build_coordinator(self, reviewers: list, execution_policy=None) -> MultiAgentCoordinator:
        return MultiAgentCoordinator(
            reviewers, max_workers=self.settings.agent_max_workers, store=self.store,
            agent_retries=self.settings.agent_retries,
            collaboration_rounds=self.settings.collaboration_rounds,
            context_manager=self.context_manager, memory_manager=self.memory,
            agent_loop_max_steps=self.settings.agent_loop_max_steps,
            agent_loop_timeout_seconds=self.settings.agent_loop_timeout_seconds,
            execution_policy=execution_policy,
            agent_registry=self.agent_registry,
        )

    def _build_leader(self, reviewers: list, execution_policy=None):
        """Select the top-level reviewer (plan §11, §20).

        ``legacy`` keeps the staged :class:`MultiAgentCoordinator` behaviour
        unchanged; ``six-agent`` runs the loop-based Coordinator over the five
        specialist loop agents.  The regressed specialist catalog is still
        registered above, but the six-agent pipeline drives its Coordinator
        through A2A delegation rather than the staged workflow.
        """
        if self.settings.agent_architecture in ("six-agent", "six-agent-v1", "six-agent-v2"):
            coordinator_kwargs = {}
            if execution_policy is not None:
                coordinator_kwargs["execution_policy"] = execution_policy
            return build_six_agent_reviewer(
                "inprocess", coordinator_kwargs=coordinator_kwargs,
                architecture=self.settings.agent_architecture)
        return self._build_coordinator(reviewers, execution_policy=execution_policy)

    def _build_harness(self, reviewer, execution_policy=None,
                       context=None) -> ReviewHarness:
        return ReviewHarness(
            self.store, reviewer, self.settings.max_steps, self.settings.timeout_seconds,
            observability=self.observability,
            finding_clustering=self.settings.finding_clustering,
            confidence_enhance=self.settings.confidence_enhance,
            confidence_buckets=parse_buckets(self.settings.confidence_buckets),
            execution_policy=execution_policy, execution_context=context,
            recovery_manager=self.recovery_manager,
            trace_logger=self.trace_repository,
            replay_repository=self.replay_repository,
        )

    def _resolve_execution_context(
        self, task_id: str, repository: str, pull_request: Optional[int],
        diff: str, tenant_id: str,
    ) -> ReviewExecutionContext:
        """Profile risk, route through the deployment manager, freeze the result.

        The real production path is ``RiskProfiler -> PolicyDeploymentManager
        (baseline / candidate stable lane) -> Safety Floor -> Context`` (plan
        section 4.3).  A task with no live deployment resolves to the registered
        baseline; a live canary routes the task to the candidate lane by a stable
        hash, and a promoted/rolled-back deployment picks the correct policy.
        """
        parsed = parse_unified_diff(diff)
        risk = self.risk_profiler.profile(parsed)
        task_hint = {"task_id": task_id, "tenant_id": tenant_id}

        # Guarantee a baseline exists for this risk level (read for restart
        # restore, otherwise bootstrap and persist once).
        baseline = self.policy_repository.active_baseline_policy(risk.level)
        if baseline is None:
            base = self.policy_resolver.resolve(task_hint, risk_profile=risk)
            baseline = replace(
                base, policy_id=f"baseline-{risk.level}", policy_version=1)
            self.policy_repository.save_policy(
                baseline.policy_id, baseline.policy_version,
                policy_to_dict(baseline), risk_level=risk.level,
                status="ACTIVE", tenant_id=tenant_id)
        self.policy_deployment_manager.register_policy(baseline)

        # Route through the deployment manager (plan 4.3 step 3).
        decision = self.policy_deployment_manager.route(
            tenant_id, repository, risk.level, task_id)
        policy = decision.policy
        # Safety floor final enforcement (plan 4.3 step 4).
        policy = self.policy_resolver.enforce_safety_floor(policy, risk)

        context = ReviewExecutionContext(
            task_id=task_id, tenant_id=tenant_id, repository=repository,
            pull_request=pull_request, parsed_diff=parsed, risk_profile=risk,
            execution_policy=policy,
            prompt_version=None,
            skill_versions={},
            runtime_policy_version=policy.policy_version,
            model_name=str(self.llm_config.get("model")) if self.llm_config else None,
            deployment_id=decision.deployment_id,
            deployment_lane=decision.lane,
            baseline_policy_id=decision.baseline_policy_id,
            baseline_policy_version=decision.baseline_version,
            candidate_policy_id=decision.candidate_policy_id,
            candidate_policy_version=decision.candidate_version,
            traffic_share=decision.traffic_share,
        )
        # Plan 4.5: persist the per-task routing exposure (idempotent by
        # ``task_id:deployment_id``, so a retried task never double-counts).
        self._persist_task_policy(task_id, decision)
        return context

    def _persist_task_policy(self, task_id: str, decision) -> None:
        try:
            self.policy_exposure_repository.add({
                "task_id": task_id,
                "deployment_id": decision.deployment_id,
                "lane": decision.lane,
                "baseline_version": decision.baseline_version or 0,
                "candidate_version": decision.candidate_version or 0,
                "traffic_share": decision.traffic_share or 0.0,
                "policy_id": decision.policy.policy_id,
                "policy_version": decision.policy.policy_version,
            })
        except Exception:  # noqa: BLE001 - routing attribution must not break reviews
            pass

    def _bootstrap_baselines(self) -> None:
        """Ensure the four ``baseline-{level}`` policies exist and are registered."""
        for level in ("low", "medium", "high", "critical"):
            self.policy_deployment_manager.register_policy(
                self._bootstrapped_baseline(level))

    def _bootstrapped_baseline(self, level: str):
        baseline = None
        row = self.policy_repository.record(f"baseline-{level}")
        if row is not None and row.get("content"):
            try:
                baseline = policy_from_dict(row["content"])
            except Exception:  # noqa: BLE001 - corrupt row falls back to default
                baseline = None
        if baseline is None:
            baseline = replace(
                default_policy(level),
                policy_id=f"baseline-{level}", policy_version=1)
            self.policy_repository.save_policy(
                baseline.policy_id, baseline.policy_version,
                policy_to_dict(baseline), risk_level=level,
                status="ACTIVE", tenant_id="default")
        return baseline

    def _load_control_policy(self, policy_id: str):
        """Reload an ``ExecutionPolicy`` from the durable control plane (restart)."""
        row = self.policy_repository.record(policy_id)
        if row is None or not row.get("content"):
            return None
        try:
            return policy_from_dict(row["content"])
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------
    # Service-level deployment / runtime-policy control plane (plan 6).
    # ------------------------------------------------------------------

    def list_runtime_policies(self, tenant_id: str = "default") -> list:
        rows = self.policy_repository.all()
        return [r for r in rows if r.get("tenant_id", "default") == tenant_id]

    def get_runtime_policy(self, policy_id: str) -> dict:
        row = self.policy_repository.record(policy_id)
        if not row:
            raise ValueError("runtime policy not found")
        return row

    def list_policy_deployments(self, tenant_id: str = "default") -> list:
        rows = self.policy_deployment_repository.all()
        return [r for r in rows if r.get("tenant_id", "default") == tenant_id]

    def get_policy_deployment(self, deployment_id: str) -> dict:
        row = self.policy_deployment_repository.record(deployment_id)
        if not row:
            raise ValueError("policy deployment not found")
        return row

    def propose_policy_candidate(
        self,
        *,
        tenant_id: str = "default",
        repository: str = "",
        risk_level: str = "high",
        hypothesis_id: Optional[str] = None,
        operations: Optional[list] = None,
    ) -> dict:
        """Generate + persist a runtime policy candidate for an operator to review."""
        baseline = self.policy_repository.active_baseline_policy(risk_level)
        if baseline is None:
            baseline = self._bootstrapped_baseline(risk_level)
        generator = PolicyCandidateGenerator()
        candidates = generator.generate(
            baseline,
            operations=operations or [CandidateOperation.RAISE_MAX_STEPS],
            hypothesis_id=hypothesis_id)
        candidate = candidates[0]
        self.policy_repository.save_policy(
            candidate.policy.policy_id, candidate.policy.policy_version,
            policy_to_dict(candidate.policy), risk_level=risk_level,
            status="CANDIDATE", tenant_id=tenant_id)
        return {
            "candidate_id": candidate.candidate_id,
            "hypothesis_id": hypothesis_id,
            "policy": candidate.policy.to_dict(),
        }

    def create_policy_deployment(
        self,
        candidate,
        *,
        tenant_id: str = "default",
        repository: str = "",
        risk_level: str = "high",
        hypothesis_id: Optional[str] = None,
    ):
        """Persist the candidate policy and open a DRAFT deployment (plan 6)."""
        self.policy_repository.save_policy(
            candidate.policy_id, candidate.policy_version,
            policy_to_dict(candidate), risk_level=risk_level,
            status="CANDIDATE", tenant_id=tenant_id)
        deployment = self.policy_deployment_manager.create(
            candidate, self.policy_deployment_manager._policies[
                f"baseline-{risk_level}"],
            tenant_id=tenant_id, repository=repository,
            risk_level=risk_level, hypothesis_id=hypothesis_id)
        # Record the durable lineage chain (plan section 9.5) so the
        # ``/v1/evolution/{candidate_id}/lineage`` endpoint has evidence even
        # across restarts.
        candidate_id = getattr(candidate, "candidate_id",
                               getattr(candidate, "policy_id", "unknown"))
        self.lineage_repository.save_lineage(
            candidate_id, {
                "candidate_id": candidate_id,
                "hypothesis_id": hypothesis_id,
                "baseline": f"baseline-{risk_level}",
                "deployment_id": deployment.get("deployment_id") if isinstance(
                    deployment, dict) else getattr(deployment, "deployment_id", ""),
                "stages": ["EXPERIENCE", "HYPOTHESIS", "CANDIDATE",
                           "EVALUATION", "DEPLOYMENT"],
            })
        return deployment

    def deployment_replay_pass(self, deployment_id: str):
        return self.policy_deployment_manager.replay_pass(deployment_id)

    def deployment_shadow(self, deployment_id: str):
        return self.policy_deployment_manager.shadow(deployment_id)

    def deployment_canary(self, deployment_id: str):
        deployment = self.policy_deployment_manager.start_canary(deployment_id)
        self.lineage_repository.add_node(
            "canary:" + deployment_id,
            {"deployment_id": deployment_id, "stage": "DEPLOYMENT",
             "lane": "canary", "candidate_id":
                 self._deployment_candidate_id(deployment_id)})
        return deployment

    def deployment_advance(
        self,
        deployment_id: str,
        *,
        min_sample_ok: bool = True,
        min_duration_ok: bool = True,
        hard_safety_pass: bool = True,
    ):
        return self.policy_deployment_manager.advance_stage(
            deployment_id, min_sample_ok=min_sample_ok,
            min_duration_ok=min_duration_ok,
            hard_safety_pass=hard_safety_pass)

    def deployment_promote(self, deployment_id: str):
        deployment = self.policy_deployment_manager.promote(deployment_id)
        self.lineage_repository.add_node(
            "promote:" + deployment_id,
            {"deployment_id": deployment_id, "stage": "DEPLOYMENT",
             "lane": "promoted", "candidate_id":
                 self._deployment_candidate_id(deployment_id)})
        return deployment

    def deployment_rollback(
        self, deployment_id: str, reason: str = "automatic rollback"
    ):
        deployment = self.policy_deployment_manager.rollback(deployment_id, reason)
        self.lineage_repository.add_node(
            "rollback:" + deployment_id,
            {"deployment_id": deployment_id, "stage": "OUTCOME",
             "lane": "rolled-back", "candidate_id":
                 self._deployment_candidate_id(deployment_id), "reason": reason})
        return deployment

    def _deployment_candidate_id(self, deployment_id: str) -> str:
        row = self.policy_deployment_repository.record(deployment_id) or {}
        return str(row.get("policy_id", "unknown"))

    # ------------------------------------------------------------------
    # Observability / evidence export (plan section 14).
    # ------------------------------------------------------------------

    def task_decision_trace(self, task_id: str) -> dict:
        """Return the durable ordered decision trace for one task."""
        trace = self.trace_repository.trace(task_id)
        if trace is None:
            raise ValueError("no decision trace recorded for task")
        return trace.to_dict()

    def task_replay(self, task_id: str) -> dict:
        """Return durable replay snapshots (+ observations and runs) for a task."""
        snapshots = self.replay_repository.snapshots_for_task(task_id)
        if not snapshots:
            raise ValueError("no replay snapshots recorded for task")
        grouped = []
        for snapshot in snapshots:
            snap_id = snapshot.get("snapshot_id")
            observations = self.replay_repository.store.get(
                "replay_tool_observations", snap_id) or []
            runs = self.replay_repository.runs_for_snapshot(snap_id)
            grouped.append({
                "snapshot": snapshot, "observations": observations, "runs": runs,
            })
        return {"task_id": task_id, "snapshots": grouped}

    def deployment_metrics(self, deployment_id: str) -> dict:
        """Aggregate durable metrics for one policy deployment."""
        row = self.policy_deployment_repository.record(deployment_id)
        if not row:
            raise ValueError("policy deployment not found")
        exposures = self.policy_exposure_repository.for_deployment(deployment_id)
        return {
            "deployment_id": deployment_id,
            "deployment": row,
            "exposure_count": len(exposures),
            "exposures": exposures,
        }

    def evolution_lineage(self, candidate_id: str) -> dict:
        """Return the durable evolution lineage chain for one candidate."""
        record = self.lineage_repository.record(candidate_id)
        nodes = [record] if record else []
        deployment_id = (record or {}).get("deployment_id", "")
        related = {"promote": [], "canary": [], "rollback": []}
        if deployment_id:
            for lane in ("promote", "canary", "rollback"):
                row = self.lineage_repository.node(lane + ":" + deployment_id)
                if row:
                    related[lane].append(row)
        if not nodes and not any(related.values()):
            raise ValueError("evolution lineage not found for candidate")
        return {"candidate_id": candidate_id, "lineage": nodes, "events": related}

    def candidate_reviewers(self, tenant_id: str, deployment=None) -> list:
        """Return every shadow/canary candidate reviewer (prompt + rule skill).

        Each entry is ``{"kind", "name", "version", "reviewer"}``.  The prompt
        candidate comes from the ``llm-review`` deployment; rule-skill candidates
        are the artifact versions already in ``shadow`` or ``canary``.
        """
        candidates = []
        if self.llm_config:
            dep = deployment or self.store.get_deployment(tenant_id, "llm-review")
            if dep and dep.get("candidate_version") is not None:
                versions = self.store.list_skill_versions("llm-review")
                prompt = next(
                    (item for item in versions
                     if int(item["version"]) == int(dep["candidate_version"])), None
                )
                if prompt:
                    candidates.append({
                        "kind": "prompt", "name": "llm-review",
                        "version": int(prompt["version"]),
                        "reviewer": self._build_llm_reviewer(prompt["prompt"]),
                    })
        for version in self.store.list_skill_artifact_versions_for_tenant(tenant_id):
            if version.get("status") not in {"shadow", "canary"}:
                continue
            candidates.append({
                "kind": "rule_skill", "name": version["skill_name"],
                "version": int(version["version"]),
                "reviewer": DeclarativeSkillReviewer(
                    version["artifact"], int(version["version"])),
            })
        return candidates

    def _candidate_reviewer(self, tenant_id: str):
        for candidate in self.candidate_reviewers(tenant_id):
            if candidate["kind"] == "prompt":
                return candidate["reviewer"]
        return None

    def _run_review(
        self, task_id: str, repository: str, pull_request: Optional[int],
        diff: str, tenant_id: str,
    ):
        task = self.store.get(task_id, tenant_id) or {}
        deployment = self.store.get_deployment(tenant_id, "llm-review")
        evolved = self._active_evolved_reviewers(tenant_id)
        report = None
        context = self._resolve_execution_context(
            task_id, repository, pull_request, diff, tenant_id,
        )
        if (
            (task.get("input") or {}).get("release_lane") == "canary"
            or (deployment and deployment.get("status") == "promoted")
        ):
            candidate = self._candidate_reviewer(tenant_id)
            if candidate:
                canary_reviewer = self._build_leader([
                    item for item in self.registry.reviewers()
                    if not isinstance(item, OpenAICompatibleReviewer)
                ] + evolved + [candidate],
                    execution_policy=context.execution_policy)
                harness = self._build_harness(
                    canary_reviewer, context.execution_policy, context)
                report = harness.run(task_id, repository, pull_request, diff, tenant_id)
        if report is None and evolved:
            tenant_reviewer = self._build_leader(
                self.registry.reviewers() + evolved,
                execution_policy=context.execution_policy,
            )
            harness = self._build_harness(
                tenant_reviewer, context.execution_policy, context)
            report = harness.run(task_id, repository, pull_request, diff, tenant_id)
        if report is None:
            default_reviewer = self._build_leader(
                self.registry.reviewers(),
                execution_policy=context.execution_policy,
            )
            harness = self._build_harness(
                default_reviewer, context.execution_policy, context)
            report = harness.run(task_id, repository, pull_request, diff, tenant_id)
        self._record_skill_usage(tenant_id, evolved, report)
        self._record_finding_distribution(tenant_id, repository, report)
        self._record_production_outcome(context, report)
        return report

    def _record_production_outcome(self, context, report) -> None:
        """Persist an attributed production outcome after a review (plan 8.3).

        Uses the deployment attribution stamped into the execution context so the
        outcome is attributable to the exact policy / lane that produced it.
        Best-effort: a failed outcome write never breaks the review path.
        """
        try:
            trace = getattr(report, "trace", None) or []
            outcome = Outcome(
                task_id=context.task_id,
                kind=OutcomeKind.TASK_SUCCESS,
                tenant_id=context.tenant_id,
                repository=context.repository,
                risk_level=context.risk_level,
                attribution=OutcomeAttribution(
                    runtime_policy_version=str(context.runtime_policy_version),
                    deployment_lane=context.deployment_lane or "baseline",
                    candidate_id=context.candidate_policy_id or "",
                ),
                metrics=RuntimeMetrics(tool_calls=len(trace)),
            )
            self.outcome_store.record(outcome)
            self.outcome_repository.save_outcome(
                outcome.outcome_id, outcome.to_dict())
        except Exception:  # noqa: BLE001 - outcomes must never fail a review
            logger.warning("production outcome recording failed", exc_info=True)

    def _record_finding_distribution(
        self, tenant_id: str, repository: str, report,
    ) -> None:
        """Work Package 10: finding distribution by tenant x repo x rule x model."""
        try:
            model = str(self.llm_config.get("model", "local")) if self.llm_config else "local"
            for finding in (report.findings or []):
                metrics.record_finding(tenant_id, repository, finding.rule_id, model)
        except Exception:
            # Observability must never break the review path.
            return

    def _record_skill_usage(
        self, tenant_id: str, evolved_reviewers: list, report,
    ) -> None:
        """Best-effort attribution of executions/findings to evolved skill@version.

        Only findings carrying an explicit source_skill are counted; a write
        failure must never break the review report.
        """
        try:
            proposed = {}
            for finding in (report.findings or []):
                source = getattr(finding, "source_skill", None)
                if source:
                    proposed[source] = proposed.get(source, 0) + 1
            for reviewer in evolved_reviewers:
                name = getattr(reviewer, "name", "")
                skill_name, separator, version = name.rpartition("@")
                if not separator:
                    continue
                try:
                    version_int = int(version)
                except (TypeError, ValueError):
                    continue
                self.store.record_skill_usage(
                    tenant_id, skill_name, version_int,
                    executions=1, findings_proposed=proposed.get(name, 0),
                )
        except Exception as exc:  # noqa: BLE001 - metrics must not fail reviews
            logger.warning("skill usage recording failed: %s", exc)

    def _record_skill_feedback_usage(
        self, tenant_id: str, category: str, finding: Optional[dict],
    ) -> None:
        """Best-effort feedback counters for explicitly attributed findings."""
        try:
            source = (finding or {}).get("source_skill")
            if not source or "@" not in source:
                return
            skill_name, separator, version = source.rpartition("@")
            if not separator:
                return
            try:
                version_int = int(version)
            except (TypeError, ValueError):
                return
            approved = int(category == "accepted")
            false_positive = int(category == "false_positive")
            if not (approved or false_positive):
                return
            self.store.record_skill_usage(
                tenant_id, skill_name, version_int,
                findings_approved=approved, false_positive_feedback=false_positive,
            )
        except Exception as exc:  # noqa: BLE001 - metrics must not fail feedback
            logger.warning("skill feedback usage recording failed: %s", exc)

    def _run_shadow(
        self, task_id: str, tenant_id: str, diff: str, primary_report,
    ) -> None:
        task = self.store.get(task_id, tenant_id) or {}
        if not (task.get("input") or {}).get("shadow"):
            return
        candidates = self.candidate_reviewers(tenant_id)
        if not candidates:
            self.store.audit(
                tenant_id, "system", "shadow.skipped", task_id,
                {"reason": "no shadow candidate reviewer is available"},
            )
            return
        lane = (task.get("input") or {}).get("release_lane", "stable")
        primary = {
            "risk": primary_report.risk,
            "finding_keys": sorted(
                "%s:%s:%s" % (item.path, item.line, item.rule_id)
                for item in primary_report.findings
            ),
        }
        for candidate in candidates:
            name = candidate["name"]
            try:
                parsed = parse_unified_diff(diff)
                started = time.monotonic()
                findings = candidate["reviewer"].review(diff, parsed)
                latency_ms = round((time.monotonic() - started) * 1000, 3)
                candidate_keys = sorted(
                    "%s:%s:%s" % (item.path, item.line, item.rule_id)
                    for item in findings
                )
                candidate_result = {
                    "finding_keys": candidate_keys,
                    "kind": candidate["kind"], "name": name,
                    "version": candidate["version"],
                }
                rollout = self.releases.observe_shadow(
                    tenant_id, name, task_id, lane, primary, candidate_result,
                    candidate_version=candidate["version"], latency_ms=latency_ms,
                )
                self.store.audit(
                    tenant_id, "system", "shadow.completed", task_id,
                    {"candidate": name, "version": candidate["version"],
                     "findings": len(findings), "candidate_output_used": False,
                     "rollout_status": (rollout or {}).get("status")},
                )
                metrics.inc("shadow_reviews_total")
            except Exception as exc:
                self.releases.observe_shadow(
                    tenant_id, name, task_id, lane, primary, None, True,
                    candidate_version=candidate["version"],
                )
                self.store.audit(
                    tenant_id, "system", "shadow.failed", task_id,
                    {"candidate": name, "error": str(exc)[:500]},
                )
                metrics.inc("shadow_reviews_failed_total")

    def evaluate_shadow_gate(
        self, tenant_id: str, candidate_name: str, candidate_kind: str,
        candidate_version: int, job_id: str = "", thresholds: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """Evaluate and persist the shadow -> canary gate for one candidate."""
        from . import evolution_gates
        observations = self.store.list_release_observations(
            tenant_id, candidate_name, limit=500)
        result = evolution_gates.shadow_gate(observations, thresholds=thresholds)
        self.store.save_gate_result({
            "tenant_id": tenant_id, "job_id": job_id,
            "candidate_kind": candidate_kind, "candidate_name": candidate_name,
            "candidate_version": int(candidate_version), "stage": "shadow",
            "gate_name": "shadow_to_canary", "passed": result["passed"],
            "threshold": thresholds or {}, "evidence": result["checks"],
        })
        return result

    def reload_skills(self) -> list:
        if self.llm_config:
            active = self.store.get_active_skill_version("llm-review")
            self.registry.register(
                "llm-review",
                self._build_llm_reviewer(active["prompt"] if active else ""),
                "1.0.0", "Context-aware AI code review via %s" % self.llm_config["provider"],
            )
        self.registry.reload()
        skills = self.registry.list()
        self.reviewer = self._build_leader(self.registry.reviewers())
        self.harness = self._build_harness(self.reviewer)
        return skills

    def _active_evolved_reviewers(self, tenant_id: str) -> list:
        return [
            DeclarativeSkillReviewer(version["artifact"], int(version["version"]))
            for version in self.store.list_active_skill_artifacts(tenant_id)
        ]

    def curator_recommendations(self, tenant_id: str = "default") -> list:
        """Read-only curator recommendations; empty when the curator is off."""
        if not self.settings.curator_enabled:
            return []
        return self.curator.recommend(self.store, tenant_id)

    def list_skills(self, tenant_id: str) -> list:
        values = self.registry.list()
        values.extend({
            "name": version["skill_name"], "version": str(version["version"]),
            "description": version["artifact"].get(
                "description", "Replay-gated evolved skill"
            ),
            "source": "evolved-db", "sandboxed": True, "permissions": [],
            "artifact_sha256": version["artifact_sha256"],
        } for version in self.store.list_active_skill_artifacts(tenant_id))
        return values

    def _validate_review(self, repository: str, diff: str) -> None:
        if not repository or len(repository) > 250:
            raise ValueError("repository is required and must be at most 250 characters")
        size = len(diff.encode("utf-8"))
        if size == 0:
            raise ValueError("diff is required")
        if size > self.settings.max_diff_bytes:
            raise ValueError("diff exceeds maximum size of %d bytes" % self.settings.max_diff_bytes)

    def _create_task(
        self, repository: str, diff: str, pull_request: Optional[int], source: str,
        tenant_id: str = "default",
    ) -> str:
        task_id = str(uuid.uuid4())
        encoded = diff.encode("utf-8")
        assignment = self.releases.assignment(tenant_id, "llm-review", task_id)
        self.store.create(task_id, repository, pull_request, {
            "source": source, "diff_bytes": len(encoded), "diff_sha256": hashlib.sha256(encoded).hexdigest(),
            "release_lane": assignment["lane"], "shadow": assignment["shadow"],
        }, tenant_id)
        self.store.save_task_payload(task_id, diff)
        return task_id

    def _create_deferred_task(
        self, repository: str, pull_request: Optional[int], source: str,
        tenant_id: str, payload: Dict[str, Any],
    ) -> str:
        task_id = str(uuid.uuid4())
        assignment = self.releases.assignment(tenant_id, "llm-review", task_id)
        self.store.create(task_id, repository, pull_request, {
            "source": source, "diff_pending": True,
            "release_lane": assignment["lane"], "shadow": assignment["shadow"],
            **payload,
        }, tenant_id)
        return task_id

    def create_review(
        self, repository: str, diff: str, pull_request: Optional[int] = None,
        source: str = "api", tenant_id: str = "default",
    ) -> Dict[str, Any]:
        self._validate_review(repository, diff)
        self._authorize_repository(tenant_id, repository)
        task_id = self._create_task(repository, diff, pull_request, source, tenant_id)
        try:
            with self.observability.span(
                "review", task_id, task_id=task_id, tenant_id=tenant_id,
                repository=repository,
            ), metrics.timer("review_duration"):
                report = self._run_review(
                    task_id, repository, pull_request, diff, tenant_id
                )
            self._run_shadow(task_id, tenant_id, diff, report)
            metrics.inc("reviews_total")
            lane = (self.store.get(task_id, tenant_id).get("input") or {}).get(
                "release_lane", "stable"
            )
            self.releases.observe(tenant_id, "llm-review", False, lane)
            return {"task_id": task_id, "state": "SUCCESS", "report": report.to_dict()}
        except Exception:
            task = self.store.get(task_id, tenant_id) or {}
            lane = (task.get("input") or {}).get("release_lane", "stable")
            self.releases.observe(tenant_id, "llm-review", True, lane)
            self.alerts.evaluate(tenant_id)
            raise

    def enqueue_review(
        self, repository: str, diff: str, pull_request: Optional[int] = None,
        source: str = "api", github_issue_url: str = "", installation_id: Optional[int] = None,
        tenant_id: str = "default",
    ) -> Dict[str, Any]:
        self._validate_review(repository, diff)
        self._authorize_repository(tenant_id, repository)
        task_id = self._create_task(repository, diff, pull_request, source, tenant_id)
        self.queue.submit({
            "task_id": task_id, "repository": repository, "pull_request": pull_request,
            "github_issue_url": github_issue_url, "installation_id": installation_id,
            "tenant_id": tenant_id,
        }, message_id=task_id)
        metrics.inc("reviews_enqueued_total")
        return {"task_id": task_id, "state": "PENDING", "queue": self.queue.backend}

    def _process_queued(self, payload: Dict[str, Any]) -> None:
        task_id = payload["task_id"]
        task = self.store.get(task_id)
        if not task:
            raise PermanentTaskError("task record no longer exists")
        tenant_id = payload.get("tenant_id") or task.get("tenant_id") or "default"
        diff = self.store.get_task_payload(task_id)
        if diff is None and payload.get("diff_url"):
            client = (
                self.github_client_for_installation(payload.get("installation_id"))
                if payload.get("installation_id") else self.github
            )
            client.ensure_repository_access(payload["repository"])
            diff = client.fetch_diff(payload["diff_url"])
            self._validate_review(payload["repository"], diff)
            encoded = diff.encode("utf-8")
            self.store.save_task_payload(task_id, diff)
            self.store.update_task_input(task_id, {
                "diff_pending": False, "diff_bytes": len(encoded),
                "diff_sha256": hashlib.sha256(encoded).hexdigest(),
            })
        if diff is None:
            raise PermanentTaskError("task payload no longer exists")
        try:
            with self.observability.span(
                "review.async", task_id, task_id=task_id, tenant_id=tenant_id,
            ), metrics.timer("review_duration"):
                report = self._run_review(
                    task_id, payload["repository"], payload.get("pull_request"), diff,
                    tenant_id,
                )
            self._run_shadow(task_id, tenant_id, diff, report)
            metrics.inc("reviews_total")
            lane = (task.get("input") or {}).get("release_lane", "stable")
            self.releases.observe(tenant_id, "llm-review", False, lane)
            if payload.get("github_issue_url") and self.settings.auto_post_review:
                client = self.github_client_for_installation(payload.get("installation_id"))
                client.upsert_comment(
                    payload["github_issue_url"], to_markdown(report.to_dict()),
                    "<!-- evoagent-review:%s -->" % task_id,
                )
        except Exception:
            metrics.inc("reviews_failed_total")
            lane = (task.get("input") or {}).get("release_lane", "stable")
            self.releases.observe(tenant_id, "llm-review", True, lane)
            self.alerts.evaluate(tenant_id)
            raise

    def _on_dead_letter(self, payload: Dict[str, Any], error: str) -> None:
        task_id = payload.get("task_id", "")
        tenant_id = payload.get("tenant_id", "default")
        task = self.store.get(task_id, tenant_id) if task_id else None
        if task and task.get("state") not in {
            TaskState.SUCCESS.value, TaskState.FAILED.value, TaskState.CANCELLED.value,
        }:
            step = max(
                [int(item.get("step", 0)) for item in task.get("trace", [])] or [0]
            ) + 1
            self.store.fail(
                task_id, error,
                TraceEvent(
                    step, TaskState.FAILED,
                    "Task entered the dead-letter queue: %s" % error, utc_now(),
                ),
            )
        self.store.create_alert(
            tenant_id, "dlq:%s" % (task_id or "unknown"), "critical",
            "Task %s entered the dead-letter queue: %s" % (task_id, error),
        )
        metrics.inc("dead_letters_total")
        # Work Package 10: queue health alarms (backlog and dead-letter depth).
        try:
            self.alerts.evaluate_queue(self.queue, tenant_id)
        except Exception:
            pass

    def handle_github_pull_request(
        self, payload: Dict[str, Any], delivery_id: str,
        payload_sha256: str, tenant_id: str = "",
    ) -> Dict[str, Any]:
        installation_id = (payload.get("installation") or {}).get("id")
        tenant_id = tenant_id or (
            self.store.installation_tenant(installation_id) if installation_id else None
        ) or self.settings.default_tenant_id
        if not self.store.claim_webhook(
            delivery_id, tenant_id, "pull_request", payload_sha256
        ):
            existing = self.store.get_webhook(delivery_id) or {}
            return {
                "duplicate": True, "task_id": existing.get("task_id"),
                "state": "PENDING" if existing.get("task_id") else "ACCEPTED",
            }
        action = payload.get("action")
        if action not in {"opened", "reopened", "synchronize"}:
            self.store.complete_webhook(delivery_id, None)
            return {"ignored": True, "reason": "unsupported pull_request action: %s" % action}
        pull = payload.get("pull_request") or {}
        repository = (payload.get("repository") or {}).get("full_name", "")
        number = payload.get("number")
        diff_url = pull.get("diff_url")
        if not repository or not isinstance(number, int) or not diff_url:
            raise ValueError("invalid GitHub pull_request payload")
        self._authorize_repository(tenant_id, repository)
        task_id = self._create_deferred_task(
            repository, number, "github-webhook", tenant_id,
            {"diff_url": diff_url},
        )
        self.queue.submit({
            "task_id": task_id, "repository": repository, "pull_request": number,
            "github_issue_url": pull.get("issue_url", ""),
            "installation_id": installation_id, "tenant_id": tenant_id,
            "diff_url": diff_url,
        }, message_id=task_id)
        metrics.inc("reviews_enqueued_total")
        result = {"task_id": task_id, "state": "PENDING", "queue": self.queue.backend}
        self.store.complete_webhook(delivery_id, result["task_id"])
        result["will_post_to_github"] = self.settings.auto_post_review
        return result

    def github_client_for_installation(self, installation_id: Optional[int] = None) -> GitHubClient:
        if installation_id is None:
            return self.github
        if not self.settings.github_app_id or not self.settings.github_private_key_path:
            raise ValueError("GitHub App credentials are not configured")
        token = GitHubAppAuthenticator(
            self.settings.github_app_id, self.settings.github_private_key_path
        ).installation_token(installation_id)
        return GitHubClient(token)

    def create_fix(
        self, task_id: str, installation_id: Optional[int] = None,
        tenant_id: Optional[str] = None,
    ) -> dict:
        task = self.store.get(task_id, tenant_id)
        if not task or not task.get("report"):
            raise ValueError("completed task not found")
        if task.get("pull_request") is None:
            raise ValueError("fix commits require a GitHub pull request task")
        actual_tenant = task.get("tenant_id") or tenant_id or "default"
        if not self.store.repository_allowed(actual_tenant, task["repository"], True):
            raise PermissionError("automatic repair is not enabled for this repository")
        result = self.fixer.create_fix_commits(
            self.github_client_for_installation(installation_id),
            task["repository"], task["pull_request"], task["report"],
        )
        metrics.inc("fix_runs_total")
        return result

    def record_feedback(
        self, task_id: str, category: str, finding: Optional[dict], note: str,
        tenant_id: Optional[str] = None, feedbacker: Optional[str] = None,
        source_key: Optional[str] = None, source_metadata: Optional[dict] = None,
    ) -> dict:
        task = self.store.get(task_id, tenant_id)
        if not task:
            raise ValueError("task not found")
        if task.get("state") != "SUCCESS" or not task.get("report"):
            raise ValueError("feedback requires a completed review task")
        if category not in {"false_positive", "missed_issue", "bad_fix", "accepted"}:
            raise ValueError("unsupported feedback category")
        actual_tenant = task.get("tenant_id") or tenant_id or "default"
        payload = {"finding": finding, "note": note[:2000]}
        if feedbacker:
            # Work Package 9: identity for the trust weighting guard.
            payload["feedbacker"] = str(feedbacker)[:120]
        if source_metadata:
            # Work Package 1: optional provenance for chat-confirmed feedback.
            payload["source_metadata"] = dict(source_metadata)
        self.store.record_failure_case(task_id, category, payload, source_key=source_key)
        # Work Package 10: per-rule false-positive rate from feedback.
        rule_id = str((finding or {}).get("rule_id", "")).strip()
        if rule_id:
            metrics.record_rule_feedback(rule_id, category == "false_positive")
        self.memory.remember_feedback(
            actual_tenant, task["repository"], task_id, category, finding, note[:2000],
        )
        metrics.inc("feedback_total")
        result = {"recorded": True, "category": category}
        # Per-version usage counters, best-effort, only for attributed findings.
        self._record_skill_feedback_usage(actual_tenant, category, finding)
        # Experience bypass: write is best-effort and must never fail the main path.
        if self.settings.experience_mode in {"shadow", "enforce"}:
            experience = self._write_experience(
                actual_tenant, task["repository"], task_id, category, finding,
            )
            if experience is not None:
                result["experience"] = experience
        return result

    # ------------------------------------------------------------------
    # Work Package 3: report-chat service methods.
    # See CHAT_ANALYSIS_IMPLEMENTATION_PLAN.md WP3.  These methods are gated
    # by the dark switches in Settings; the API layer returns 409 when the
    # feature is disabled.  Plain Q&A never writes into the evolution chain:
    # candidate conclusions are only persisted as draft insights that WP5
    # turns into feedback after explicit user confirmation.
    # ------------------------------------------------------------------

    def _require_chat_enabled(self) -> None:
        if not self.settings.chat_enabled:
            raise ValueError("chat feature is disabled")

    def _chat_task(self, task_id: str, tenant_id: str) -> dict:
        task = self.store.get(task_id, tenant_id)
        if not task or task.get("state") != "SUCCESS" or not task.get("report"):
            raise ValueError("chat requires a completed review task with a report")
        return task

    def _chat_added_lines(self, task_id: str) -> list:
        diff = self.store.get_task_payload(task_id)
        if not diff:
            return []
        return [
            {"path": item.path, "line": item.line, "content": item.content}
            for item in parse_unified_diff(diff).added_lines
        ]

    def create_chat_session(self, task_id: str, title: str, principal) -> dict:
        self._require_chat_enabled()
        task = self._chat_task(task_id, principal.tenant_id)
        actual_tenant = task.get("tenant_id") or principal.tenant_id or "default"
        session = self.store.create_chat_session(
            actual_tenant, task_id, task["repository"],
            str(title or "Report analysis")[:200],
            getattr(principal, "user_id", "") or getattr(principal, "username", ""),
            report_fingerprint(task["report"]),
        )
        metrics.inc("chat_sessions_total")
        return session

    def list_task_chat_sessions(self, task_id: str, principal) -> list:
        self._require_chat_enabled()
        return self.store.list_task_chat_sessions(task_id, principal.tenant_id)

    def get_chat_session(self, session_id: str, principal) -> dict:
        self._require_chat_enabled()
        session = self.store.get_chat_session(session_id, principal.tenant_id)
        if not session:
            raise ValueError("chat session not found")
        session["messages"] = self.store.list_chat_messages(session_id, principal.tenant_id)
        # Work Package 5: candidates must stay visible across reloads so the
        # user can confirm or reject them.
        session["insights"] = self.store.list_chat_insights(session_id, principal.tenant_id)
        return session

    def send_chat_message(
        self, session_id: str, content: str, client_request_id: str, principal,
    ) -> dict:
        self._require_chat_enabled()
        if not content or not str(content).strip():
            raise ValueError("message content is required")
        content = str(content)[: self.settings.chat_max_message_chars]
        session = self.store.get_chat_session(session_id, principal.tenant_id)
        if not session:
            raise ValueError("chat session not found")
        task_id = session["task_id"]
        task = self.store.get(task_id, principal.tenant_id)
        if not task or not task.get("report"):
            raise ValueError("chat requires a completed review task with a report")
        actual_tenant = task.get("tenant_id") or principal.tenant_id or "default"

        # 1) Version constraint: if the report changed since the session was
        # created, mark the session stale and refuse to answer on the old one.
        current_fp = report_fingerprint(task["report"])
        if session.get("report_fingerprint") != current_fp:
            self.store.update_chat_session_status(
                session_id, actual_tenant, "stale")
            metrics.record_chat_stale_session()
            raise ValueError("chat session is stale: the task report has changed")

        # 2) Round budget (WP6 6.2): reject before any write when exhausted.
        # Each answered turn is a completed message row, so the budget is the
        # count of completed rows; failed/pending turns never consume it.
        completed_rounds = len([
            m for m in self.store.list_chat_messages(session_id, actual_tenant)
            if m.get("status") == "completed"
        ])
        if completed_rounds >= self.settings.chat_max_rounds:
            metrics.inc("chat_rejections_rounds_total")
            raise ValueError("chat session round limit reached")

        # 3) Per-session concurrency guard (WP6 6.2): one in-flight request.
        with self._chat_inflight_lock:
            if session_id in self._chat_inflight:
                metrics.inc("chat_rejections_concurrent_total")
                raise ChatBusyError(
                    "another chat request is in progress for this session")
            self._chat_inflight.add(session_id)

        request_id = str(client_request_id or "")[:128]

        # 4) Idempotent pending user message (returns the existing row on replay).
        user_message = self.store.append_chat_message(
            actual_tenant, session_id, "user", content, [],
            client_request_id=request_id if request_id else None,
            status="pending",
        )
        user_message_id = user_message["id"]
        try:
            if user_message.get("status") == "completed":
                # A replay of an already-answered request: return the stored
                # answer without calling the model again.
                return self._chat_reply_payload(session_id, user_message, actual_tenant)
            return self._run_chat_turn(
                session_id, task_id, task, actual_tenant, content,
                user_message, request_id,
            )
        finally:
            with self._chat_inflight_lock:
                self._chat_inflight.discard(session_id)

    def _run_chat_turn(
        self, session_id, task_id, task, tenant_id, content,
        user_message, request_id,
    ) -> dict:
        """Model call + validation + persistence for one chat turn (WP6)."""
        user_message_id = user_message["id"]
        findings = task["report"].get("findings", [])
        added_lines = self._chat_added_lines(task_id)
        trace = task.get("trace", [])
        memories = self.memory.recall(tenant_id, task["repository"], content)
        history = [
            {"role": "assistant", "content": item.get("content", "")}
            for item in self.store.list_chat_messages(session_id, tenant_id)
            if item.get("status") == "completed"
        ]
        context = self.chat_context_builder.build(
            repository=task["repository"],
            risk=str(task["report"].get("risk", "unknown")),
            report=task["report"],
            findings=findings,
            added_lines=added_lines,
            trace=trace,
            memories=memories,
            question=content,
            history=history,
        )

        # Model call (no DB lock held during the request).
        if not self.chat_client:
            self.store.fail_chat_message(user_message_id, tenant_id,
                                        "chat model is not configured")
            raise ValueError("chat model is not configured")
        system = (
            "You are a careful reviewer explaining an EvoAgent PR review report. "
            "Answer the user's question in Chinese or English (match the user). "
            "Only cite content that is present in the provided context. "
            "Return JSON with keys: answer (string), citations (list of "
            "{type,ref[,path,line]}), insights (list of {category,confidence,"
            "finding_ref,note}) when insights are enabled."
        )
        prompt_version = "%s/%s" % (self.llm_config["provider"], self.llm_config["model"])
        provider = str(self.llm_config["provider"])
        model = str(self.llm_config["model"])
        # Work Package 7: request/correlation id for tracing (ids only, never
        # message content, in logs).
        correlation_id = uuid.uuid4().hex[:12]
        started = time.monotonic()
        try:
            raw = self.chat_client.complete(system, history + [
                {"role": "user", "content": context["text"]},
            ])
            elapsed = time.monotonic() - started
        except ChatModelNotConfigured:
            self.store.fail_chat_message(user_message_id, tenant_id,
                                        "chat model is not configured")
            raise ValueError("chat model is not configured")
        except ChatModelError as exc:
            elapsed = time.monotonic() - started
            self.store.fail_chat_message(user_message_id, tenant_id, str(exc))
            metrics.inc("chat_messages_failed_total")
            metrics.inc("chat_rejections_%s_total" % exc.reason)
            metrics.record_chat_message("failed")
            metrics.record_chat_failure(exc.reason)
            metrics.record_chat_request(provider, model, "failed", elapsed)
            logger.debug("chat turn failed session=%s message=%s correlation=%s "
                         "provider=%s model=%s reason=%s elapsed=%.3f",
                         session_id, user_message_id, correlation_id,
                         provider, model, exc.reason, elapsed)
            raise exc

        # Server-side validation of Answer / Citations / Insights (hard caps).
        try:
            decoded = decode_model_output(
                raw, context["references"], findings,
                insights_enabled=self.settings.chat_insights_enabled,
                max_citations=self.settings.chat_max_citations,
                max_insights=self.settings.chat_max_insights,
            )
        except ChatModelError as exc:
            self.store.fail_chat_message(user_message_id, tenant_id, str(exc))
            metrics.inc("chat_messages_failed_total")
            metrics.inc("chat_rejections_invalid_output_total")
            metrics.record_chat_message("failed")
            metrics.record_chat_failure("invalid_output")
            metrics.record_chat_request(provider, model, "failed", elapsed)
            raise exc
        answer = decoded["answer"]
        citations = decoded["citations"]
        insights = decoded["insights"]
        metrics.record_chat_invalid_citations(decoded.get("invalid_citation_count", 0))

        # Work Package 7: token usage reported by the model (0 when absent).
        usage = getattr(self.chat_client, "last_usage", None) or {}
        input_tokens = int(usage.get("input_tokens", 0))
        output_tokens = int(usage.get("output_tokens", 0))

        # Persist assistant message + snapshot + draft insights.
        self.store.complete_chat_message(
            user_message_id, tenant_id, answer, citations,
            provider=provider,
            model=model,
            prompt_version=prompt_version,
            input_tokens=input_tokens, output_tokens=output_tokens,
        )
        self.store.save_chat_context_snapshot(
            tenant_id, session_id, user_message_id,
            context["report_fingerprint"], context["context_fingerprint"],
            context["references"], context["truncation"],
        )
        saved_insights = []
        for insight in insights:
            saved = self.store.create_chat_insight(
                tenant_id, session_id, user_message_id, task_id,
                insight["category"], insight["finding"],
                insight["note"], insight["confidence"], {"source": "model"},
                status="draft",
            )
            saved_insights.append(saved)
            metrics.record_chat_insight(insight["category"], "draft")
        self.store.update_chat_session_status(session_id, tenant_id, "active")
        metrics.inc("chat_messages_total")
        metrics.record_chat_message("completed")
        metrics.record_chat_request(
            provider, model, "completed", elapsed,
            input_tokens=input_tokens, output_tokens=output_tokens,
        )
        logger.debug("chat turn completed session=%s message=%s correlation=%s "
                     "provider=%s model=%s elapsed=%.3f tokens=%d/%d",
                     session_id, user_message_id, correlation_id, provider, model,
                     elapsed, input_tokens, output_tokens)
        return self._chat_reply_payload(session_id, user_message, tenant_id,
                                        insights=saved_insights)

    def _chat_reply_payload(self, session_id, user_message, tenant_id,
                            insights=None):
        session = self.store.get_chat_session(session_id, tenant_id)
        messages = self.store.list_chat_messages(session_id, tenant_id)
        return {
            "session": session,
            "messages": messages,
            "user_message_id": user_message["id"],
            "insights": insights if insights is not None
            else self.store.list_chat_insights(session_id, tenant_id),
        }

    def reject_chat_insight(self, insight_id: str, principal) -> dict:
        self._require_chat_enabled()
        insight = self.store.get_chat_insight(insight_id, principal.tenant_id)
        if not insight:
            raise ValueError("chat insight not found")
        if insight["status"] != "draft":
            raise ValueError("only draft insights can be rejected")
        updated = self.store.update_chat_insight_status(
            insight_id, principal.tenant_id, "rejected")
        metrics.record_chat_insight(insight["category"], "rejected")
        return {"rejected": True, "insight": updated}

    # ------------------------------------------------------------------
    # Work Package 5: candidate confirmation and controlled sedimentation.
    # Only confirmed chat insights may call record_feedback(); every confirm
    # is idempotent via failure_cases.source_key and never bypasses the
    # existing evolution gates (Experience corroboration, Validation, Holdout).
    # ------------------------------------------------------------------

    def _require_chat_feedback_enabled(self) -> None:
        self._require_chat_enabled()
        if not self.settings.chat_feedback_enabled:
            raise ValueError("chat feedback is disabled")

    @staticmethod
    def _finding_key(finding: dict) -> str:
        """Stable identity for conflict detection across insights."""
        f = finding or {}
        return "|".join((str(f.get("rule_id", "")), str(f.get("path", "")),
                         str(f.get("line", ""))))

    def _validate_chat_insight(
        self, insight: dict, task: dict, added_lines: list,
    ) -> dict:
        """Category-level validation (WP5 5.1) with conflict warnings (5.5).

        Returns ``{"valid": bool, "issues": [...], "warnings": [...]}``.
        """
        category = insight["category"]
        finding = insight.get("finding") or {}
        note = str(insight.get("note", "")).strip()
        issues: list = []
        warnings: list = []
        report_findings = (task.get("report") or {}).get("findings", []) or []
        if category == "false_positive":
            # Must reference a finding that really exists in the current report;
            # the server-side copy is what gets persisted at confirm time.
            if not finding.get("rule_id") or not any(
                self._finding_key(f) == self._finding_key(finding) for f in report_findings
            ):
                issues.append("误报必须关联报告中真实存在的 Finding")
            if not note:
                issues.append("误报需要说明为什么不成立或缺少何种上下文")
        elif category == "missed_issue":
            rule_id = str(finding.get("rule_id", "")).strip()
            path = str(finding.get("path", "")).strip()
            try:
                line = int(finding.get("line", 0))
            except (TypeError, ValueError):
                line = 0
            if not (rule_id and path and line > 0):
                issues.append("漏报需要合法 rule_id、文件路径和行号，可在编辑中补充")
            elif not any(
                str(a["path"]) == path and int(a["line"]) == line for a in added_lines
            ):
                issues.append("漏报位置必须映射到 Diff 的新增行")
        elif category == "bad_fix":
            if not note:
                issues.append("坏修复需要说明破坏行为、兼容性问题或验证失败")
        elif category == "accepted":
            # Finding-level acceptance; only affects stats/evidence, not weights.
            pass
        # 5.5 Conflict detection: same finding cannot be both accepted and
        # false_positive.  We surface a warning; we never auto-override.
        for other in self.store.list_chat_insights(insight["session_id"], insight["tenant_id"]):
            if other["id"] == insight["id"]:
                continue
            if other["status"] not in {"draft", "confirmed"}:
                continue
            if other["finding"] and self._finding_key(other["finding"]) == self._finding_key(finding):
                if {other["category"], category} == {"accepted", "false_positive"}:
                    warnings.append(
                        "同一 Finding 同时存在已接受与误报结论，请人工确认不要自动覆盖"
                    )
        return {"valid": not issues, "issues": issues, "warnings": warnings}

    def _resolve_insight_finding(self, insight: dict, task: dict) -> Optional[dict]:
        """Use the server-side copy of a report finding at confirm time."""
        finding = insight.get("finding") or {}
        if not finding:
            return None
        report_findings = (task.get("report") or {}).get("findings", []) or []
        for report_finding in report_findings:
            if self._finding_key(report_finding) == self._finding_key(finding):
                return dict(report_finding)
        return dict(finding)

    def _chat_insight_validation(self, insight: dict, task: dict) -> dict:
        """Compute (and persist for draft insights) the validation record."""
        added_lines = self._chat_added_lines(insight["task_id"])
        validation = self._validate_chat_insight(insight, task, added_lines)
        if insight["status"] == "draft":
            self.store.update_chat_insight(
                insight["id"], insight["tenant_id"], insight["category"],
                insight.get("finding") or {}, insight.get("note", ""), validation,
            )
        return validation

    def edit_chat_insight(
        self, insight_id: str, category, finding, note, principal,
    ) -> dict:
        """PATCH a draft insight; re-runs server-side validation each time."""
        self._require_chat_enabled()
        insight = self.store.get_chat_insight(insight_id, principal.tenant_id)
        if not insight:
            raise ValueError("chat insight not found")
        if insight["status"] != "draft":
            raise ValueError("only draft insights can be edited")
        new_category = str(category) if category is not None else insight["category"]
        if new_category not in CHAT_INSIGHT_CATEGORIES:
            raise ValueError("unsupported insight category")
        new_finding = dict(finding or {}) if finding is not None else dict(insight.get("finding") or {})
        new_note = normalize_text(
            note if note is not None else insight.get("note", ""), INSIGHT_NOTE_MAX)
        task = self.store.get(insight["task_id"], insight["tenant_id"])
        if not task or not task.get("report"):
            raise ValueError("chat requires a completed review task with a report")
        candidate = {
            "id": insight["id"], "tenant_id": insight["tenant_id"],
            "session_id": insight["session_id"], "category": new_category,
            "finding": new_finding, "note": new_note,
        }
        added_lines = self._chat_added_lines(insight["task_id"])
        validation = self._validate_chat_insight(candidate, task, added_lines)
        updated = self.store.update_chat_insight(
            insight_id, insight["tenant_id"], new_category, new_finding, new_note, validation,
        )
        metrics.inc("chat_insights_total")
        return updated

    def confirm_chat_insight(self, insight_id: str, principal) -> dict:
        """Confirm a draft insight into the existing feedback chain (idempotent)."""
        self._require_chat_feedback_enabled()
        tenant_id = principal.tenant_id
        insight = self.store.get_chat_insight(insight_id, tenant_id)
        if not insight:
            raise ValueError("chat insight not found")
        if insight["status"] == "confirmed":
            # Replay / double click: return the stored result without side effects.
            return {"confirmed": True, "insight": insight,
                    "feedback": {"recorded": True,
                                 "failure_case_id": insight.get("feedback_case_id")}}
        if insight["status"] != "draft":
            raise ValueError("only draft insights can be confirmed")
        session = self.store.get_chat_session(insight["session_id"], tenant_id)
        if not session:
            raise ValueError("chat session not found")
        task = self.store.get(insight["task_id"], tenant_id)
        if not task or not task.get("report"):
            raise ValueError("chat requires a completed review task with a report")
        # Version constraint: a changed report makes the old session stale.
        current_fp = report_fingerprint(task["report"])
        if session.get("report_fingerprint") != current_fp:
            self.store.update_chat_session_status(
                insight["session_id"], tenant_id, "stale")
            metrics.record_chat_stale_session()
            raise ValueError("chat session is stale: the task report has changed")
        # Category-level validation before any write.
        added_lines = self._chat_added_lines(insight["task_id"])
        validation = self._validate_chat_insight(insight, task, added_lines)
        if not validation["valid"]:
            raise ValueError("insight validation failed: " + "; ".join(validation["issues"]))
        # Atomic claim draft -> confirming (single winner).
        if not self.store.claim_chat_insight(
            insight_id, tenant_id, str(principal.user_id or principal.username)
        ):
            fresh = self.store.get_chat_insight(insight_id, tenant_id)
            if fresh.get("status") == "confirmed":
                return {"confirmed": True, "insight": fresh,
                        "feedback": {"recorded": True,
                                     "failure_case_id": fresh.get("feedback_case_id")}}
            raise ValueError("insight is not available for confirmation")
        try:
            finding = self._resolve_insight_finding(insight, task)
            result = self.record_feedback(
                insight["task_id"], insight["category"], finding,
                insight.get("note", ""), tenant_id,
                feedbacker=str(principal.user_id or principal.username),
                source_key=insight_source_key(insight_id),
                source_metadata={
                    "session_id": insight["session_id"],
                    "message_id": insight["source_message_id"],
                    "insight_id": insight_id,
                    "report_fingerprint": current_fp,
                },
            )
            # record_feedback() keeps its historical return shape; the failure
            # case id is recovered through the idempotent source key instead.
            feedback_case_id = self.store.get_failure_case_by_source_key(
                insight["task_id"], insight_source_key(insight_id))
            updated = self.store.update_chat_insight_status(
                insight_id, tenant_id, "confirmed",
                confirmed_by=str(principal.user_id or principal.username),
                feedback_case_id=feedback_case_id,
            )
            metrics.inc("chat_feedback_total")
            metrics.record_chat_insight(insight["category"], "confirmed")
            metrics.record_chat_feedback(insight["category"])
            return {"confirmed": True, "insight": updated, "feedback": result}
        except Exception:
            # Recoverable failure: release the claim so the user can retry.
            self.store.update_chat_insight_status(insight_id, tenant_id, "draft")
            raise

    def _reconcile_chat_state(self) -> None:
        """WP5 5.3 + WP6 6.4: recover state interrupted by a crash.

        - ``confirming`` insights: mark confirmed when the failure case exists
          (feedback write committed), otherwise restore to draft.
        - ``pending`` messages: mark failed so the user can retry; they are
          never silently dropped.
        """
        if not self.settings.chat_feedback_enabled and not self.settings.chat_enabled:
            return
        try:
            for insight in self.store.list_chat_insights_by_status("confirming"):
                case_id = self.store.get_failure_case_by_source_key(
                    insight["task_id"], insight_source_key(insight["id"]))
                if case_id is not None:
                    self.store.update_chat_insight_status(
                        insight["id"], insight["tenant_id"], "confirmed",
                        confirmed_by=insight.get("confirmed_by"),
                        feedback_case_id=case_id,
                    )
                    logger.info("reconciled confirming insight %s -> confirmed", insight["id"])
                else:
                    self.store.update_chat_insight_status(
                        insight["id"], insight["tenant_id"], "draft")
                    logger.info("reconciled confirming insight %s -> draft", insight["id"])
            for message in self.store.list_chat_messages_by_status("pending"):
                self.store.fail_chat_message(
                    message["id"], message["tenant_id"],
                    "request timed out (recovered at startup)")
                logger.info("recovered pending chat message %s -> failed", message["id"])
        except Exception:  # noqa: BLE001 - chat must never block service startup
            logger.warning("chat reconciliation failed", exc_info=True)

    def archive_chat_session(self, session_id: str, principal) -> dict:
        """Archive a session (WP6 6.3): no physical deletion, audit stays."""
        self._require_chat_enabled()
        session = self.store.get_chat_session(session_id, principal.tenant_id)
        if not session:
            raise ValueError("chat session not found")
        if session.get("status") == "archived":
            return session
        updated = self.store.update_chat_session_status(
            session_id, principal.tenant_id, "archived")
        metrics.inc("chat_sessions_archived_total")
        return updated

    def purge_chat_history(self, principal) -> dict:
        """Run the retention policy (WP6 6.3): remove message bodies of old
        sessions that never formed confirmed feedback, per tenant.

        Sessions with confirmed insights keep their message content as the
        traceable source; feedback/experience/evolution rows are untouched.
        Returns the number of messages removed.
        """
        self._require_chat_enabled()
        if self.settings.chat_retention_days <= 0:
            return {"purged": 0, "note": "retention is disabled (EVOAGENT_CHAT_RETENTION_DAYS=0)"}
        cutoff = (datetime.datetime.now(datetime.timezone.utc)
                  - datetime.timedelta(days=self.settings.chat_retention_days)).isoformat()
        purged = self.store.purge_chat_history(cutoff, principal.tenant_id)
        metrics.inc("chat_history_purged_total", purged)
        return {"purged": purged,
                "note": "message bodies older than %d days removed" % self.settings.chat_retention_days}

    def _write_experience(
        self, tenant_id: str, repository: str, task_id: str, category: str,
        finding: Optional[dict],
    ) -> Optional[dict]:
        try:
            diff = self.store.get_task_payload(task_id)
            added_lines = parse_unified_diff(diff).added_lines if diff else []
            exp = build_experience(
                tenant_id, repository, task_id, category, finding, added_lines,
            )
            if exp is None:
                return None
            saved = self.store.record_experience(
                exp["tenant_id"], exp["repository"], exp["task_id"], exp["source_type"],
                exp["category"], exp["experience_type"], exp["fingerprint"], exp["payload"],
                exp["evidence"], exp["confidence"], exp["status"],
            )
            if exp["experience_type"] == RULE_CANDIDATE:
                self._maybe_corroborate(tenant_id, exp["fingerprint"])
            metrics.inc("feedback_experience_total")
            return {
                "id": saved["id"], "experience_type": exp["experience_type"],
                "status": exp["status"], "fingerprint": exp["fingerprint"],
            }
        except Exception as exc:  # noqa: BLE001 - bypass must not break feedback
            metrics.inc("feedback_experience_errors")
            logger.warning("experience bypass failed for task %s: %s", task_id, exc)
            return None

    def _maybe_corroborate(self, tenant_id: str, fingerprint: str) -> None:
        observed = self.store.list_observed_experiences_by_fingerprint(tenant_id, fingerprint)
        distinct_tasks = len({item["task_id"] for item in observed})
        # Work Package 9: EVOAGENT_FEEDBACK_MIN_CONFIRMERS raises the distinct
        # task bar above the legacy evolution_min_distinct_tasks floor.
        required_tasks = max(
            self.settings.evolution_min_distinct_tasks,
            self.settings.feedback_min_confirmers,
        )
        if (
            len(observed) >= self.settings.evolution_min_evidence
            and distinct_tasks >= required_tasks
        ):
            self.store.corroborate_experiences(tenant_id, fingerprint)
            metrics.inc("experience_corroborated")

    def resume_task(self, task_id: str, tenant_id: Optional[str] = None) -> dict:
        task = self.store.get(task_id, tenant_id)
        if not task:
            raise ValueError("task not found")
        if task["state"] == "SUCCESS":
            return {"task_id": task_id, "state": "SUCCESS", "report": task["report"]}
        diff = self.store.get_task_payload(task_id)
        if diff is None:
            raise ValueError("task payload is no longer available")
        self.queue.submit({
            "task_id": task_id, "repository": task["repository"],
            "pull_request": task.get("pull_request"),
            "tenant_id": task.get("tenant_id", "default"),
        }, message_id=task_id)
        return {"task_id": task_id, "state": "PENDING", "resumed": True}

    def cancel_task(self, task_id: str, tenant_id: Optional[str] = None) -> bool:
        return self.store.request_cancel(task_id, tenant_id)

    def _authorize_repository(self, tenant_id: str, repository: str) -> None:
        if not self.store.repository_allowed(tenant_id, repository):
            raise PermissionError("repository is not authorized for this tenant")

    def close(self) -> None:
        """Release owned resources idempotently.

        Only closes resources this service created and holds.  The existing
        ``service.queue.close()`` call remains valid; repeated calls to this
        method are safe.
        """
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        self.queue.close()
        self.evolution_controller.close()
        self.observability.close()
