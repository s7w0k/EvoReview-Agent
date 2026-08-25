"""Failure taxonomy, recovery actions and failure event records."""
import time
import enum
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class FailureType(str, enum.Enum):
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    MODEL_RATE_LIMIT = "MODEL_RATE_LIMIT"
    MODEL_INVALID_OUTPUT = "MODEL_INVALID_OUTPUT"
    MODEL_CONTEXT_OVERFLOW = "MODEL_CONTEXT_OVERFLOW"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"

    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"
    TOOL_INVALID_ARGUMENT = "TOOL_INVALID_ARGUMENT"
    TOOL_PERMISSION_DENIED = "TOOL_PERMISSION_DENIED"
    TOOL_SIDE_EFFECT_UNKNOWN = "TOOL_SIDE_EFFECT_UNKNOWN"

    AGENT_INVALID_STATE = "AGENT_INVALID_STATE"
    AGENT_NO_PROGRESS = "AGENT_NO_PROGRESS"
    AGENT_HALLUCINATION = "AGENT_HALLUCINATION"

    CHECKPOINT_FAILURE = "CHECKPOINT_FAILURE"
    STORAGE_FAILURE = "STORAGE_FAILURE"

    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    POLICY_VIOLATION = "POLICY_VIOLATION"

    UNKNOWN = "UNKNOWN"


class RecoveryAction(str, enum.Enum):
    RETRY = "RETRY"
    RETRY_WITH_BACKOFF = "RETRY_WITH_BACKOFF"
    SWITCH_MODEL = "SWITCH_MODEL"
    SWITCH_TOOL = "SWITCH_TOOL"
    COMPRESS_CONTEXT = "COMPRESS_CONTEXT"
    REPLAN = "REPLAN"
    FALLBACK_AGENT = "FALLBACK_AGENT"
    SKIP = "SKIP"
    COMPENSATE = "COMPENSATE"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    ABORT = "ABORT"


RETRYABLE_FOR_BACKOFF = {
    FailureType.MODEL_TIMEOUT,
    FailureType.MODEL_RATE_LIMIT,
    FailureType.MODEL_UNAVAILABLE,
    FailureType.TOOL_TIMEOUT,
    FailureType.TOOL_UNAVAILABLE,
}


@dataclass
class FailureEvent:
    failure_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    task_id: str = ""
    agent_id: str = ""
    node: str = ""
    failure_type: FailureType = FailureType.UNKNOWN
    message: str = ""
    recoverable: bool = True
    recovery_action: Optional[RecoveryAction] = None
    attempt: int = 0
    resolved: bool = False
    created_at: float = field(default_factory=time.time)
    context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        value = {
            key: item for key, item in vars(self).items()
            if key not in {"failure_type", "recovery_action"}
        }
        value["failure_type"] = self.failure_type.value
        value["recovery_action"] = (
            self.recovery_action.value if self.recovery_action else None
        )
        return value