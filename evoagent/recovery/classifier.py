"""Map an exception / context onto a ``FailureType``.

Classification is deterministic and based on the exception class, message tokens
and optional caller-provided context (e.g. the tool name and whether a
non-idempotent side-effect tool was in flight).
"""
import re
from typing import Any, Dict, Optional

from .failures import FailureType


def _lower(message: str) -> str:
    return (message or "").lower()


class FailureClassifier:
    """Rule-based failure classification."""

    def classify(
        self,
        exc: Optional[BaseException] = None,
        message: str = "",
        context: Optional[Dict[str, Any]] = None,
    ) -> FailureType:
        context = context or {}
        text = " ".join(filter(None, [message, str(exc) if exc else ""]))
        lowered = _lower(text)
        exc_type = type(exc).__name__.lower() if exc else ""

        # Explicit budget / policy bookkeeping.
        if isinstance(exc, MemoryError):
            return FailureType.MODEL_CONTEXT_OVERFLOW
        if "budget" in lowered or "tool-call budget" in lowered:
            return FailureType.BUDGET_EXCEEDED
        if "permission" in lowered or "denied" in lowered or "approval declined" in lowered:
            return FailureType.TOOL_PERMISSION_DENIED

        if exc_type in {"timeouterror", "timeoutexpired"} or "timed out" in lowered or \
                "timeout" in lowered:
            node = str(context.get("node", ""))
            return FailureType.MODEL_TIMEOUT if node.startswith("model") else FailureType.TOOL_TIMEOUT

        if "rate limit" in lowered or "429" in lowered:
            return FailureType.MODEL_RATE_LIMIT

        if context.get("side_effect_unknown"):
            return FailureType.TOOL_SIDE_EFFECT_UNKNOWN

        if lower_node := str(context.get("node", "")):
            if "tool" in lower_node:
                if "invalid argument" in lowered or "missing required" in lowered:
                    return FailureType.TOOL_INVALID_ARGUMENT
                if "unavailable" in lowered or "connection" in lowered:
                    return FailureType.TOOL_UNAVAILABLE
                return FailureType.TOOL_UNAVAILABLE

        if "checkpoint" in lowered or exc_type == "checkpointexception":
            return FailureType.CHECKPOINT_FAILURE
        if "storage" in lowered or "database" in lowered:
            return FailureType.STORAGE_FAILURE

        if "invalid" in lowered or "json" in lowered or "parse" in lowered:
            return FailureType.MODEL_INVALID_OUTPUT

        if "no progress" in lowered or "no-progress" in lowered:
            return FailureType.AGENT_NO_PROGRESS
        if "hallucinat" in lowered:
            return FailureType.AGENT_HALLUCINATION
        if "state" in lowered and "invalid" in lowered:
            return FailureType.AGENT_INVALID_STATE

        return FailureType.UNKNOWN


classifier = FailureClassifier()


def classify_failure(
    exc: Optional[BaseException] = None,
    message: str = "",
    context: Optional[Dict[str, Any]] = None,
) -> FailureType:
    return classifier.classify(exc, message, context)