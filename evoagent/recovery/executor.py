"""Execute a recovery action for a failure event."""
from typing import Any, Dict, Optional

from ..policy.models import ExecutionPolicy
from .compensation import CompensationHandler
from .failures import FailureEvent, RecoveryAction


class RecoveryNotSupported(RuntimeError):
    """A recovery action has no executable implementation."""


def _backoff_seconds(policy: Optional[ExecutionPolicy], attempt: int) -> float:
    base = policy.retry.backoff_seconds if policy is not None else 1.0
    exponential = (
        policy.retry.exponential_backoff if policy is not None else False
    )
    if exponential:
        return base * (2 ** max(0, attempt - 1))
    return base


class RecoveryExecutor:
    """Dispatch a recovery action, returning the resulting runtime state update."""

    def __init__(self, compensation: Optional[CompensationHandler] = None):
        self.compensation = compensation or CompensationHandler()

    def execute(
        self,
        event: FailureEvent,
        execution_policy: Optional[ExecutionPolicy],
        runtime_state: Dict[str, Any],
        model_switch: Optional[Any] = None,
    ) -> Dict[str, Any]:
        action = event.recovery_action or RecoveryAction.ABORT
        if action == RecoveryAction.RETRY_WITH_BACKOFF:
            return {
                "recovery": "retry",
                "backoff_seconds": _backoff_seconds(execution_policy, event.attempt),
            }
        if action == RecoveryAction.RETRY:
            return {"recovery": "retry", "backoff_seconds": 0.0}
        if action == RecoveryAction.SWITCH_MODEL:
            return {"recovery": "switch_model", "model": model_switch}
        if action == RecoveryAction.COMPRESS_CONTEXT:
            return {
                "recovery": "compress_context",
                "evidence": "context_window",
                "retain_last": runtime_state.get("context_reserve", 30),
            }
        if action == RecoveryAction.FALLBACK_AGENT:
            fallback = execution_policy.agents.fallback_agents \
                if execution_policy is not None else []
            return {"recovery": "fallback_agent", "agent": fallback[0] if fallback else None}
        if action == RecoveryAction.SKIP:
            return {"recovery": "skip"}
        if action == RecoveryAction.REPLAN:
            return {"recovery": "replan"}
        if action == RecoveryAction.COMPENSATE:
            return self._compensate(event)
        if action == RecoveryAction.HUMAN_REVIEW:
            return {"recovery": "human_review", "task_id": event.task_id}
        if action == RecoveryAction.ABORT:
            return {"recovery": "abort"}
        if action == RecoveryAction.SWITCH_TOOL:
            return {"recovery": "switch_tool", "tool": event.context.get("tool")}
        raise RecoveryNotSupported(action.value)

    def _compensate(self, event: FailureEvent) -> Dict[str, Any]:
        tool = event.context.get("tool") or ""
        if not self.compensation.compensates(tool):
            return {"recovery": "compensate", "compensated": False, "tool": tool}
        self.compensation.compensate(tool, event.context.get("arguments") or {})
        return {"recovery": "compensate", "compensated": True, "tool": tool}