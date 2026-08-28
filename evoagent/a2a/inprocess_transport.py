"""In-process A2A transport (Phase 2).

Runs the remote Agent behaviour directly against an :class:`AgentServiceHost`
inside the same process -- no network.  Used as a fallback and to prove the
A2A transport boundary is decoupled from business logic: the same :class:`A2ATask`
yields the same :class:`A2AArtifact` structure as the HTTP transport.
"""
from typing import List

from .models import A2ATask, AgentCard
from .service import AgentServiceHost
from .transport import A2ATransport


class InProcessA2ATransport(A2ATransport):
    name = "inprocess"

    def __init__(self, host: AgentServiceHost):
        self.host = host

    def discover(self, endpoint: str) -> dict:
        return self.host.card.to_dict()

    def submit_task(self, card: AgentCard, task: A2ATask) -> dict:
        return self.host.submit(task.to_dict())

    def get_task(self, card: AgentCard, task_id: str) -> dict:
        return self.host.get_task(task_id)

    def cancel_task(self, card: AgentCard, task_id: str) -> dict:
        return self.host.cancel(task_id)

    def get_artifacts(self, card: AgentCard, task_id: str) -> List[dict]:
        return self.host.artifacts(task_id)


__all__ = ["InProcessA2ATransport"]