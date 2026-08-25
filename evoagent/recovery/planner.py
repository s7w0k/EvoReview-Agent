"""Choose a recovery action for a classified failure.

The planner is deterministic: different failure types take different recovery
paths (the plan's section 6.4).  It reads the attempt count and execution policy
retry budget and honours side-effect safety.
"""
from typing import Any, Dict, Optional

from ..policy.models import ExecutionPolicy
from .failures import (
    FailureType,
    RecoveryAction,
    RETRYABLE_FOR_BACKOFF,
)


class RecoveryPlanner:
    """Map a failure to the next recovery action."""

    def plan(
        self,
        failure_type: FailureType,
        execution_policy: Optional[ExecutionPolicy],
        runtime_state: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> RecoveryAction:
        context = context or {}
        attempt = int(runtime_state.get("attempt", 0))
        canned = self._canned_steps(failure_type, context)
        if canned is None:
            return RecoveryAction.ABORT
        index = min(max(0, attempt - 1), len(canned) - 1)
        return canned[index]

    @staticmethod
    def _canned_steps(failure_type: FailureType, context: Dict[str, Any]) -> Optional[list]:
        if failure_type in RETRYABLE_FOR_BACKOFF:
            if context.get("side_effect_unknown"):
                return [RecoveryAction.HUMAN_REVIEW]
            return [
                RecoveryAction.RETRY_WITH_BACKOFF,
                RecoveryAction.SWITCH_MODEL,
                RecoveryAction.ABORT,
            ]

        if failure_type == FailureType.MODEL_CONTEXT_OVERFLOW:
            return [RecoveryAction.COMPRESS_CONTEXT, RecoveryAction.RETRY, RecoveryAction.ABORT]

        if failure_type == FailureType.MODEL_INVALID_OUTPUT:
            return [RecoveryAction.RETRY, RecoveryAction.SWITCH_MODEL, RecoveryAction.ABORT]

        if failure_type == FailureType.TOOL_INVALID_ARGUMENT:
            return [RecoveryAction.SWITCH_TOOL]

        if failure_type == FailureType.TOOL_PERMISSION_DENIED:
            if context.get("is_side_effect"):
                return [RecoveryAction.HUMAN_REVIEW]
            return [RecoveryAction.SKIP]

        if failure_type == FailureType.TOOL_SIDE_EFFECT_UNKNOWN:
            return [RecoveryAction.HUMAN_REVIEW, RecoveryAction.COMPENSATE, RecoveryAction.ABORT]

        if failure_type == FailureType.AGENT_NO_PROGRESS:
            return [RecoveryAction.REPLAN, RecoveryAction.FALLBACK_AGENT, RecoveryAction.ABORT]

        if failure_type == FailureType.AGENT_HALLUCINATION:
            return [RecoveryAction.REPLAN, RecoveryAction.ABORT]

        if failure_type == FailureType.AGENT_INVALID_STATE:
            return [RecoveryAction.REPLAN, RecoveryAction.ABORT]

        if failure_type in {FailureType.CHECKPOINT_FAILURE, FailureType.STORAGE_FAILURE}:
            return [RecoveryAction.RETRY, RecoveryAction.ABORT]

        if failure_type == FailureType.BUDGET_EXCEEDED:
            # Never retry budget exhaustion; stop gracefully.
            return [RecoveryAction.ABORT]

        if failure_type == FailureType.POLICY_VIOLATION:
            return [RecoveryAction.HUMAN_REVIEW]

        return None

    def is_terminal(self, action: RecoveryAction) -> bool:
        return action in {RecoveryAction.ABORT, RecoveryAction.HUMAN_REVIEW, RecoveryAction.SKIP}