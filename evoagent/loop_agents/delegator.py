"""Coordinator-side A2A delegation hub (plan §6, §8, §17).

Maintains one :class:`RemoteAgentClient` per remote card and the rich artifacts
they produce, so the Coordinator's ``delegate_agent`` governed tool only ever
handles a compact observation while the full findings stay here for final
aggregation.  Supports in-process and HTTP transports transparently.
"""
import uuid
from typing import Any, Dict, List, Optional

from ..a2a.client import RemoteAgentClient
from ..a2a.models import A2ATask
from ..a2a.registry import AgentRegistry

from .models import TASK_TYPES


def _uuid() -> str:
    return uuid.uuid4().hex


class Delegator:
    """Owns the agent clients + produced artifacts for one coordinator run."""

    def __init__(self, registry: Optional[AgentRegistry] = None):
        self.registry = registry or AgentRegistry()
        self.clients: Dict[str, RemoteAgentClient] = {}
        self.artifacts: Dict[str, Dict[str, Any]] = {}
        self.diff: str = ""

    def add_agent(self, agent_id: str, card: dict, transport, task_type: str):
        self.registry.register(dict(card))
        client = RemoteAgentClient(card, transport, task_type=task_type)
        self.clients[agent_id] = client

    def available(self, agent_ids: List[str]) -> List[str]:
        return [agent_id for agent_id in agent_ids if agent_id in self.clients]

    def discover(self) -> List[Dict[str, Any]]:
        return [client.card for client in self.clients.values()]

    def artifacts_of(self, agent_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if agent_id is None:
            return list(self.artifacts.values())
        return [
            item for item in self.artifacts.values()
            if item.get("agent_id") == agent_id
        ]

    def cancel(self, agent_id: str, task_id: str) -> bool:
        if agent_id not in self.clients:
            return False
        return self.clients[agent_id].cancel(task_id)

    def has(self, agent_id: str) -> bool:
        return agent_id in self.clients

    def delegate(
        self, agent_id: str, task_type: str, objective: str, *,
        findings: Optional[List[Dict[str, Any]]] = None,
        diff: Optional[str] = None, context_refs: Optional[List[str]] = None,
        correlation_id: str = "",
    ) -> Dict[str, Any]:
        """Run a task on a remote agent and store its artifact for aggregation."""
        if agent_id not in self.clients:
            return {"task_id": "", "status": "failed", "agent_id": agent_id,
                    "error": "unknown or unavailable agent: %s" % agent_id}
        client = self.clients[agent_id]
        input_data: Dict[str, Any] = {"objective": objective}
        if diff is not None:
            input_data["diff"] = diff
        elif self.diff:
            input_data["diff"] = self.diff
        if findings is not None:
            input_data["findings"] = findings
        if context_refs:
            input_data["context_refs"] = list(context_refs)

        task = A2ATask(
            task_id=_uuid(), assignment_id=_uuid(), sender="coordinator",
            recipient=agent_id, task_type=task_type, input=input_data,
            context={"task_type": task_type}, correlation_id=correlation_id or _uuid(),
        )
        try:
            content = client.run_to_artifact(task, artifact_type=TASK_TYPES.get(task_type))
            status = "completed"
            error = ""
        except Exception as exc:  # noqa: BLE001 - surface as a failed delegation
            content = {}
            status = "failed"
            error = str(exc)[:800]
        record = {
            "task_id": task.task_id, "agent_id": agent_id, "task_type": task_type,
            "status": status, "error": error, "content": content,
            "artifact_type": TASK_TYPES.get(task_type, "raw"),
        }
        self.artifacts[task.task_id] = record
        return {
            "task_id": task.task_id, "status": status, "agent_id": agent_id,
            "task_type": task_type, "error": error,
            "artifact_type": record["artifact_type"],
            "result_keys": sorted(content.keys()),
        }

    # -- aggregation helpers -------------------------------------------------
    def specialist_findings(self, task_types: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Merge findings from the specialist artifacts (security / reliability)."""
        kinds = task_types or ["review.security", "review.reliability"]
        merged: Dict[str, Dict[str, Any]] = {}
        for record in self.artifacts.values():
            if record.get("task_type") not in kinds:
                continue
            for finding in (record.get("content") or {}).get("findings") or []:
                key = "%s:%s:%s" % (
                    finding.get("rule_id"), finding.get("path"), finding.get("line"))
                merged[key] = finding
        return list(merged.values())


__all__ = ["Delegator"]