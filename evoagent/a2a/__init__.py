"""EvoReview-Agent A2A remote transport layer.

A standardised HTTP/JSON-RPC A2A layer that lets Specialist reviewers
(Security, Reliability) run as independent services while the in-process
``CollaborationBus`` and metadata lives untouched.

Modules
-------
- ``models``: the wire-level domain model (AgentCard / A2ATask / TaskStatus /
  A2AArtifact / A2AMessage) plus JSON (de)serialisation.
- ``errors``: the A2A error taxonomy used by retry / circuit breaker / fallback.
- ``protocol``: JSON-RPC 2.0 framing and the internal-to-wire adapters
  (``AgentMessage <-> A2AMessage``, ``Finding <-> Artifact``, schema validation).
- ``transport``: the pluggable ``A2ATransport`` interface (+ resilient wrapper).
- ``inprocess_transport`` / ``http_transport``: the two concrete transports.
- ``resilience``: RetryPolicy, CircuitBreaker, FallbackChain.
- ``registry``: AgentRegistry + capability / health routing.
- ``adapters``: ``RemoteReviewerAdapter`` that satisfies the existing
  ``Reviewer`` interface so ``MultiAgentCoordinator`` needs no deep change.
- ``service``: a dependency-light JSON-RPC service host + task lifecycle store
  reused by the FastAPI services and by stdlib test servers.
- ``governance``: token auth, execution policy checks and artifact sanitisation.
- ``telemetry``: A2A metrics merged into the existing metrics registry.
- ``evaluation``: Evaluation Harness V3 Local-vs-Remote comparison + failure
  injection.
"""

from .models import A2AArtifact, A2AMessage, A2ATask, AgentCard, TaskStatus
from .errors import (
    A2AConnectionError,
    A2AProtocolError,
    A2ARemoteExecutionError,
    A2ASchemaError,
    A2ATimeoutError,
    A2AUnauthorizedError,
    A2ACircuitOpenError,
)
from .transport import A2ATransport
from .client import RemoteAgentClient
from .adapters import RemoteReviewerAdapter

__all__ = [
    "A2AArtifact",
    "A2AMessage",
    "A2ATask",
    "AgentCard",
    "TaskStatus",
    "A2AConnectionError",
    "A2AProtocolError",
    "A2ARemoteExecutionError",
    "A2ASchemaError",
    "A2ATimeoutError",
    "A2AUnauthorizedError",
    "A2ACircuitOpenError",
    "A2ATransport",
    "RemoteAgentClient",
    "RemoteReviewerAdapter",
]