"""Coordinated recovery: classify -> plan -> execute -> persist.

``RecoveryManager.handle`` is the single entry point a runtime node calls when
an exception escapes the happy path.  It wraps the existing classifier /
planner / executor into one call that also tracks the attempt budget and returns
a structured outcome, so the harness can decide whether to retry, replan,
fall back or abort.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from ..policy.models import ExecutionPolicy
from .classifier import FailureClassifier
from .executor import RecoveryExecutor
from .failures import FailureEvent, RecoveryAction
from .planner import RecoveryPlanner


@dataclass
class RecoveryBudget:
    """Bounded recovery so a pathological task cannot loop forever."""

    max_recovery_attempts: int = 3
    max_replans: int = 2
    max_model_switches: int = 2

    def exhausted(self, state: Dict[str, Any]) -> bool:
        used = state.get("recovery_counts") or {}
        return int(used.get("attempts", 0)) >= self.max_recovery_attempts


@dataclass
class RecoveryOutcome:
    """What the runtime should do next after a failure."""

    event: FailureEvent
    action: Optional[RecoveryAction] = None
    updates: Dict[str, Any] = field(default_factory=dict)
    recovery_counts: Dict[str, int] = field(default_factory=dict)

    @property
    def recoverable(self) -> bool:
        return bool(self.action) and self.action not in {
            RecoveryAction.ABORT, RecoveryAction.HUMAN_REVIEW,
        }

    @property
    def should_retry(self) -> bool:
        return self.action in {RecoveryAction.RETRY, RecoveryAction.RETRY_WITH_BACKOFF}

    @property
    def should_replan(self) -> bool:
        return self.action == RecoveryAction.REPLAN

    @property
    def should_fallback(self) -> bool:
        return self.action == RecoveryAction.FALLBACK_AGENT

    @property
    def should_abort(self) -> bool:
        return self.action == RecoveryAction.ABORT or self.action is None


class RecoveryManager:
    """Orchestrates classification -> planning -> execution for one failure."""

    def __init__(
        self,
        classifier: Optional[FailureClassifier] = None,
        planner: Optional[RecoveryPlanner] = None,
        executor: Optional[RecoveryExecutor] = None,
        policy: Optional[ExecutionPolicy] = None,
        budget: Optional[RecoveryBudget] = None,
    ):
        self.classifier = classifier or FailureClassifier()
        self.planner = planner or RecoveryPlanner()
        self.executor = executor or RecoveryExecutor()
        self.policy = policy
        self.budget = budget or RecoveryBudget()
        self._events: list = []

    def handle(
        self,
        exc: BaseException,
        context: Dict[str, Any],
        runtime_state: Dict[str, Any],
        node: str,
        agent_id: str = "",
        tool_context: Optional[Dict[str, Any]] = None,
    ) -> RecoveryOutcome:
        """Classify, plan and execute recovery for ``exc`` on ``node``."""
        policy = self.policy
        runtime_state["recovery_counts"] = runtime_state.get("recovery_counts") or {
            "attempts": 0, "replans": 0, "model_switches": 0,
        }
        failure_type = self.classifier.classify(exc, context=context or {})
        merged_context = dict(context or {})
        if tool_context:
            merged_context.update(tool_context)

        if self.budget.exhausted(runtime_state):
            action = RecoveryAction.ABORT
        else:
            action = self.planner.plan(failure_type, policy, runtime_state, merged_context)

        event = FailureEvent(
            task_id=str(runtime_state.get("task_id", "")),
            agent_id=agent_id,
            node=node,
            failure_type=failure_type,
            message=str(exc)[:2000],
            recoverable=action not in {RecoveryAction.ABORT},
            recovery_action=action,
            attempt=int(runtime_state["recovery_counts"]["attempts"]) + 1,
            context=dict(merged_context),
        )
        updates = self.executor.execute(event, policy, runtime_state)

        counts = dict(runtime_state["recovery_counts"])
        counts["attempts"] = min(self.budget.max_recovery_attempts, counts["attempts"] + 1)
        if action == RecoveryAction.REPLAN:
            counts["replans"] = min(self.budget.max_replans, counts["replans"] + 1)
        if action == RecoveryAction.SWITCH_MODEL:
            counts["model_switches"] = min(
                self.budget.max_model_switches, counts["model_switches"] + 1)
        runtime_state["recovery_counts"] = counts

        event.resolved = action != RecoveryAction.ABORT
        self._events.append(event)
        return RecoveryOutcome(
            event=event, action=action, updates=updates, recovery_counts=counts,
        )

    def events(self) -> list:
        return list(self._events)