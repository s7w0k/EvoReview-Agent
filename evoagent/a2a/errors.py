"""A2A error taxonomy (Phase 7 + Phase 8).

The classification drives retry eligibility, circuit breaker tripping and
fallback selection: only *transient* errors are retried, while contract /
authorisation violations fail fast.
"""


class A2AError(Exception):
    """Base class for every A2A error."""

    #: whether the underlying condition is transient and worth a retry.
    retryable = False

    def __init__(self, message: str, *, source_agent: str = "", target_agent: str = ""):
        super().__init__(message)
        self.message = message
        self.source_agent = source_agent
        self.target_agent = target_agent


class A2AConnectionError(A2AError):
    """Network-level failure (connection reset / endpoint unreachable)."""

    retryable = True


class A2ATimeoutError(A2AConnectionError):
    """The remote agent did not answer within the deadline."""

    retryable = True


class A2AUnavailableError(A2AConnectionError):
    """Remote reported temporary unavailability (UB / 503)."""

    retryable = True


class A2AProtocolError(A2AError):
    """Remote answered but the JSON-RPC frame or version is unsupported."""


class A2ASchemaError(A2AError):
    """A request/response field violates the A2A schema."""


class A2ARemoteExecutionError(A2AError):
    """Remote executed the task and reported a domain-level failure."""


class A2AUnauthorizedError(A2AError):
    """Credential is missing / rejected, or the caller is denied."""


class A2ACircuitOpenError(A2AError):
    """The circuit breaker is open; the call was refused before transport."""


# A2A epoch -- the exact task lifecycle recorded by the remote host.
__all__ = [
    "A2AError",
    "A2AConnectionError",
    "A2ATimeoutError",
    "A2AUnavailableError",
    "A2AProtocolError",
    "A2ASchemaError",
    "A2ARemoteExecutionError",
    "A2AUnauthorizedError",
    "A2ACircuitOpenError",
]