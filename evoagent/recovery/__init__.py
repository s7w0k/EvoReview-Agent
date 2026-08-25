"""Failure taxonomy, classification and recovery orchestration."""
from .classifier import FailureClassifier, classify_failure
from .compensation import CompensationHandler
from .executor import RecoveryExecutor, RecoveryNotSupported
from .failures import FailureEvent, FailureType, RecoveryAction, RETRYABLE_FOR_BACKOFF
from .manager import RecoveryBudget, RecoveryManager, RecoveryOutcome
from .no_progress import NoProgressDetector
from .planner import RecoveryPlanner

__all__ = [
    "CompensationHandler",
    "FailureClassifier",
    "FailureEvent",
    "FailureType",
    "NoProgressDetector",
    "RecoveryAction",
    "RecoveryBudget",
    "RecoveryExecutor",
    "RecoveryManager",
    "RecoveryNotSupported",
    "RecoveryOutcome",
    "RecoveryPlanner",
    "RETRYABLE_FOR_BACKOFF",
    "classify_failure",
]