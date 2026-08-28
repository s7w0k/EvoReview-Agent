"""Generic Remote-Agent A2A client (plan §17).

``RemoteReviewerAdapter`` is review-specific: it always parses the transport's
output back into :class:`Finding` objects.  A six-agent coordinator needs a
transport-agnostic client so Critic / Verifier / Fix can be delegated through
the exact same A2A substrate while their *different* artifacts (critique-report,
verification-report, fix-patch, ...) are returned raw.  This client fills that
gap and shares the resilient transport, so retry / circuit-breaker / timeout /
backup and metrics all keep working.
"""
from typing import Any, Dict, List, Optional

from .errors import A2AError
from .models import A2AArtifact, A2AResult, A2ATask, AgentCard, TaskStatus
from .resilience import CircuitBreaker, RetryPolicy
from .transport import A2ATransport, ResilientTransport


class RemoteAgentClient:
    """A generic client over one Remote Agent card + resilient transport."""

    def __init__(
        self, card: dict, transport: A2ATransport, *,
        task_type: str = "review.security",
        retry: Optional[RetryPolicy] = None, breaker: Optional[CircuitBreaker] = None,
        metrics=None,
    ):
        self.card = dict(card)
        self.agent_id = str(card.get("agent_id", card.get("name", "remote")))
        self.name = str(card.get("name", self.agent_id))
        self.task_type = task_type
        self.transport = ResilientTransport(
            transport, retry=retry, breaker=breaker, metrics=metrics,
        )

    def _card(self) -> AgentCard:
        return AgentCard.from_dict(self.card)

    # -- low-level protocol --------------------------------------------------
    def submit(self, task: A2ATask) -> str:
        record = self.transport.submit_task(self._card(), task)
        return str(record.get("task_id") or task.task_id)

    def get(self, task_id: str) -> A2AResult:
        record = self.transport.get_task(self._card(), task_id)
        return A2AResult.from_dict(record)

    def artifacts(self, task_id: str) -> List[A2AArtifact]:
        raw = self.transport.get_artifacts(self._card(), task_id)
        return [A2AArtifact.from_dict(item) for item in raw]

    def cancel(self, task_id: str) -> bool:
        record = self.transport.cancel_task(self._card(), task_id)
        return str(record.get("status", "")).lower() == TaskStatus.CANCELLED.value.lower()

    # -- high-level convenience ---------------------------------------------
    def run(self, task: A2ATask, poll_steps: int = 60, delay: float = 0.02) -> A2AResult:
        """Submit a task and poll to a terminal status, returning its artifacts."""
        self.submit(task)
        current = TaskStatus.PENDING
        for _ in range(max(1, poll_steps)):
            result = self.get(task.task_id)
            current = result.status
            if current in (TaskStatus.COMPLETED, TaskStatus.FAILED,
                           TaskStatus.CANCELLED, TaskStatus.TIMED_OUT):
                if current == TaskStatus.COMPLETED:
                    result.artifacts = self.artifacts(task.task_id)
                return result
            if delay:
                import time
                time.sleep(delay)
        return A2AResult(task.task_id, TaskStatus.TIMED_OUT)

    def run_to_artifact(self, task: A2ATask, artifact_type: Optional[str] = None) -> Dict[str, Any]:
        """Submit a task and return the merged content of its artifacts."""
        result = self.run(task)
        if result.status != TaskStatus.COMPLETED:
            raise A2AError(
                "remote %s task %s ended %s: %s" % (
                    self.agent_id, task.task_id, result.status.value, result.error,
                )
            )
        content: Dict[str, Any] = {}
        metadata: List[Dict[str, Any]] = []
        for artifact in result.artifacts:
            if artifact_type and artifact.artifact_type != artifact_type:
                continue
            content.update(artifact.content)
            metadata.append(dict(artifact.metadata or {}))
        # Runtime orchestration needs the real remote loop step count and stop
        # reason.  Preserve transport metadata beside (not inside) findings.
        content["_a2a_metadata"] = metadata
        return content


__all__ = ["RemoteAgentClient"]
