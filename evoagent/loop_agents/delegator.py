"""Coordinator-side A2A delegation hub (plan §6, §8, §17).

Maintains one :class:`RemoteAgentClient` per remote card and the rich artifacts
they produce, so the Coordinator's ``delegate_agent`` governed tool only ever
handles a compact observation while the full findings stay here for final
aggregation.  Supports in-process and HTTP transports transparently.
"""
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from ..a2a.client import RemoteAgentClient
from ..a2a.models import A2ATask
from ..a2a.registry import AgentRegistry

from .models import TASK_TYPES


def _uuid() -> str:
    return uuid.uuid4().hex


class DelegationHandle:
    """Non-blocking handle to an in-flight delegation (plan §1.2)."""

    __slots__ = ("task_id", "agent_id", "task_type", "correlation_id")

    def __init__(self, task_id: str, agent_id: str, task_type: str,
                 correlation_id: str = ""):
        self.task_id = task_id
        self.agent_id = agent_id
        self.task_type = task_type
        self.correlation_id = correlation_id or _uuid()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id, "agent_id": self.agent_id,
            "task_type": self.task_type, "correlation_id": self.correlation_id,
        }


class Delegator:
    """Owns the agent clients + produced artifacts for one coordinator run.

    Supports both the blocking :meth:`delegate` (for compatibility) and a
    first-class parallel API (:meth:`submit` / :meth:`poll` / :meth:`collect`,
    :meth:`submit_batch` / :meth:`collect_batch`) that overlaps independent
    agent calls via a thread pool (plan §1.3 / §1.4).
    """

    def __init__(self, registry: Optional[AgentRegistry] = None,
                 max_parallel: int = 4):
        self.registry = registry or AgentRegistry()
        self.clients: Dict[str, RemoteAgentClient] = {}
        self.artifacts: Dict[str, Dict[str, Any]] = {}
        self.diff: str = ""
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, max_parallel), thread_name_prefix="deleg")
        self._futures: Dict[str, Future] = {}
        self._started: Dict[str, float] = {}
        self._latency: Dict[str, float] = {}

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

    # -- task payload construction ------------------------------------------
    def _build_input(self, objective, findings, diff, context_refs):
        input_data: Dict[str, Any] = {"objective": objective}
        if diff is not None:
            input_data["diff"] = diff
        elif self.diff:
            input_data["diff"] = self.diff
        if findings is not None:
            input_data["findings"] = findings
        if context_refs:
            input_data["context_refs"] = list(context_refs)
        return input_data

    def _run_task(self, handle: DelegationHandle, objective: str,
                  findings, diff, context_refs) -> Dict[str, Any]:
        """Execute one delegation (in a pool thread) and store its artifact."""
        agent_id = handle.agent_id
        client = self.clients.get(agent_id)
        if client is None:
            return {"task_id": handle.task_id, "status": "failed",
                    "agent_id": agent_id, "task_type": handle.task_type,
                    "error": "unknown or unavailable agent: %s" % agent_id}
        task = A2ATask(
            task_id=handle.task_id, assignment_id=_uuid(), sender="coordinator",
            recipient=agent_id, task_type=handle.task_type,
            input=self._build_input(objective, findings, diff, context_refs),
            context={"task_type": handle.task_type},
            correlation_id=handle.correlation_id,
        )
        try:
            content = client.run_to_artifact(
                task, artifact_type=TASK_TYPES.get(handle.task_type))
            status, error = "completed", ""
        except Exception as exc:  # noqa: BLE001
            content, status, error = {}, "failed", str(exc)[:800]
        record = {
            "task_id": handle.task_id, "agent_id": agent_id,
            "task_type": handle.task_type, "status": status, "error": error,
            "content": content,
            "artifact_type": TASK_TYPES.get(handle.task_type, "raw"),
        }
        self.artifacts[handle.task_id] = record
        return self._result_dict(record)

    @staticmethod
    def _result_dict(record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "task_id": record["task_id"], "status": record["status"],
            "agent_id": record["agent_id"], "task_type": record["task_type"],
            "error": record.get("error", ""),
            "artifact_type": record["artifact_type"],
            "result_keys": sorted((record.get("content") or {}).keys()),
        }

    # -- parallel API -------------------------------------------------------
    def submit(self, agent_id: str, task_type: str, objective: str, *,
               findings=None, diff=None, context_refs=None) -> DelegationHandle:
        handle = DelegationHandle(_uuid(), agent_id, task_type)
        self._started[handle.task_id] = time.monotonic()
        fut = self._pool.submit(
            self._run_task, handle, objective, findings, diff, context_refs)
        self._futures[handle.task_id] = fut
        return handle

    def poll(self, handle: DelegationHandle) -> Dict[str, Any]:
        fut = self._futures.get(handle.task_id)
        done = fut.done() if fut is not None else False
        return {"task_id": handle.task_id, "agent_id": handle.agent_id,
                "running": not done}

    def collect(self, handle: DelegationHandle) -> Dict[str, Any]:
        fut = self._futures.get(handle.task_id)
        started = self._started.get(handle.task_id)
        result = {"task_id": handle.task_id, "status": "failed",
                  "agent_id": handle.agent_id, "task_type": handle.task_type,
                  "error": "no such task"}
        if fut is not None:
            try:
                result = fut.result()
                if started is not None:
                    self._latency[handle.task_id] = \
                        (time.monotonic() - started) * 1000.0
            except Exception as exc:  # noqa: BLE001
                result["error"] = str(exc)[:800]
                if started is not None:
                    self._latency[handle.task_id] = \
                        (time.monotonic() - started) * 1000.0
        return result

    def submit_batch(self, tasks: List[Dict[str, Any]]) -> List[DelegationHandle]:
        handles = []
        for task in tasks:
            handles.append(self.submit(
                task["agent_id"], task["task_type"], task.get("objective", ""),
                findings=task.get("findings"), diff=task.get("diff"),
                context_refs=task.get("context_refs")))
        return handles

    def collect_batch(self, handles: List[DelegationHandle],
                      timeout: Optional[float] = None) -> Dict[str, Any]:
        started = time.monotonic()
        completed, failed = [], []
        for handle in handles:
            result = self.collect(handle)
            if result["status"] == "completed":
                completed.append(result)
            else:
                failed.append(result)
        latency_ms = (time.monotonic() - started) * 1000.0
        return {"completed": completed, "failed": failed,
                "latency_ms": round(latency_ms, 2)}

    # -- blocking API (compatibility) ---------------------------------------
    def delegate(
        self, agent_id: str, task_type: str, objective: str, *,
        findings: Optional[List[Dict[str, Any]]] = None,
        diff: Optional[str] = None, context_refs: Optional[List[str]] = None,
        correlation_id: str = "",
    ) -> Dict[str, Any]:
        """Run a task on a remote agent and store its artifact (blocking)."""
        if agent_id not in self.clients:
            return {"task_id": "", "status": "failed", "agent_id": agent_id,
                    "error": "unknown or unavailable agent: %s" % agent_id}
        handle = self.submit(
            agent_id, task_type, objective, findings=findings,
            diff=diff, context_refs=context_refs)
        if correlation_id:
            handle.correlation_id = correlation_id
        return self.collect(handle)

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


__all__ = ["Delegator", "DelegationHandle"]