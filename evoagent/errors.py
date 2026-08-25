"""Standardised model (LLM provider) exceptions.

The spec's plan section 7.3 asks us to stop collapsing every model failure into a
bare ``RuntimeError`` and instead raise type-specific errors so the failure
classifier and recovery planner can route them deterministically.  All of these
are subclasses of :class:`ModelError` (itself a ``RuntimeError``) so existing
``except Exception`` callers keep working.
"""
from typing import Optional


class ModelError(RuntimeError):
    """Base class for all standardised model-provider failures."""

    def __init__(self, message: str, *, status_code: Optional[int] = None,
                 provider: str = "", detail: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.provider = provider
        self.detail = detail


class ModelTimeout(ModelError):
    """The provider did not respond within the deadline (HTTP 408 / socket timeout)."""


class ModelRateLimit(ModelError):
    """The provider returned a rate-limit response (usually HTTP 429)."""


class ModelContextOverflow(ModelError):
    """The request exceeded the provider's context window / token limit."""


class ModelInvalidOutput(ModelError):
    """The provider returned malformed, non-JSON or unparseable output."""


class ModelUnavailable(ModelError):
    """The provider could not be reached or returned a 4xx/5xx availability error."""


__all__ = [
    "ModelContextOverflow",
    "ModelError",
    "ModelInvalidOutput",
    "ModelRateLimit",
    "ModelTimeout",
    "ModelUnavailable",
]