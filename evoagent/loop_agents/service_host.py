"""A2A host that serves a :class:`BaseLoopAgent` (plan §19).

Mirrors the :class:`AgentServiceHost` contract -- ``card``, ``submit``,
``get_task``, ``cancel``, ``artifacts``, ``handle``, ``fail_on`` -- so the same
host can be driven by the in-process transport, the stdlib HTTP server or
FastAPI, without any review-specific code.  The produced artifact kind is taken
from the task type via :data:`TASK_TYPES`.
"""
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..a2a.errors import (
    A2AProtocolError,
    A2ARemoteExecutionError,
    A2ASchemaError,
    A2AUnauthorizedError,
)
from ..a2a.governance import ArtifactSanitizer, verify_token
from ..a2a.models import A2AArtifact, A2ATask, AgentCard, TaskStatus
from ..a2a.protocol import (
    loads_request,
    server_error_response,
    success_response,
    validate_task_fields,
)

from .base import BaseLoopAgent
from .models import TASK_TYPES


def _uuid() -> str:
    return uuid.uuid4().hex


@dataclass
class LoopTaskRecord:
    task_id: str
    recipient: str
    status: TaskStatus
    error: str = ""
    artifacts: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "recipient": self.recipient,
            "status": self.status.value,
            "error": self.error,
            "artifacts": list(self.artifacts),
        }


class LoopTaskStore:
    """In-memory lifecycle store for one loop-agent process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: Dict[str, LoopTaskRecord] = {}

    def put(self, record: LoopTaskRecord) -> None:
        with self._lock:
            self._tasks[record.task_id] = record

    def get(self, task_id: str) -> Optional[LoopTaskRecord]:
        with self._lock:
            return self._tasks.get(task_id)


class LoopAgentHost:
    """Adapts one :class:`BaseLoopAgent` into an A2A remote agent."""

    def __init__(
        self, agent: BaseLoopAgent, card: Optional[dict] = None, *,
        token: str = "", store: Optional[LoopTaskStore] = None,
        sanitizer: Optional[ArtifactSanitizer] = None,
        delay_seconds: float = 0.0, fail_on: Optional[Dict[str, Any]] = None,
    ):
        self.agent = agent
        self.token = token
        self.sanitizer = sanitizer or ArtifactSanitizer()
        self.store = store or LoopTaskStore()
        self.delay_seconds = max(0.0, delay_seconds)
        self.fail_on: Dict[str, Any] = dict(fail_on or {})
        task_type = getattr(agent, "task_type", "review.security")
        self.card = self._coerce_card(
            card or {
                "agent_id": getattr(agent, "agent_id", "loop-agent"),
                "name": getattr(agent, "agent_id", "loop-agent"),
                "endpoint": "loop://localhost",
                "protocol_version": "v1",
                "capabilities": list(getattr(agent, "capabilities", ())),
                "domains": [],
                "supported_task_types": [task_type],
                "deployment": "local",
                "health_status": "healthy",
            }
        )

    @staticmethod
    def _coerce_card(card: Any) -> "AgentCard":
        if isinstance(card, AgentCard):
            return card
        return AgentCard.from_dict(dict(card or {}))

    # -- lifecycle ----------------------------------------------------------
    def submit(self, task: Dict[str, Any]) -> Dict[str, Any]:
        validate_task_fields(task)
        self._inject("submit")
        obj = A2ATask.from_dict(task)
        self.store.put(LoopTaskRecord(obj.task_id, obj.recipient, TaskStatus.RUNNING))
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        artifact_type = TASK_TYPES.get(obj.task_type, "raw")
        try:
            outcome = self.agent.run({
                "task_id": obj.task_id,
                "task_type": obj.task_type,
                "objective": str((obj.input or {}).get("objective", "")),
                "input": obj.input or {},
                "context": obj.context or {},
                "correlation_id": obj.correlation_id,
            })
        except Exception as exc:  # noqa: BLE001 - surface as remote execution error
            record = LoopTaskRecord(
                obj.task_id, obj.recipient, TaskStatus.FAILED, str(exc)[:1000])
            self.store.put(record)
            raise A2ARemoteExecutionError(
                "remote agent %s failed: %s" % (self.card.agent_id, exc),
                target_agent=self.card.agent_id,
            )
        content = dict(outcome.get("artifact") or {})
        artifact = A2AArtifact(
            artifact_id=_uuid(), task_id=obj.task_id, artifact_type=artifact_type,
            producer=self.card.agent_id, content=content,
            metadata={
                "task_type": obj.task_type,
                "stop_reason": outcome.get("stop_reason", "final"),
                "steps": outcome.get("steps", 0),
                "plan_versions": len(outcome.get("plan") or []),
                "decision_count": len(outcome.get("decisions") or []),
                "correlation_id": obj.correlation_id,
            },
        )
        safe = self.sanitizer.sanitize(artifact)
        record = LoopTaskRecord(
            obj.task_id, obj.recipient, TaskStatus.COMPLETED,
            artifacts=[safe.to_dict()],
        )
        self.store.put(record)
        return record.to_dict()

    def get_task(self, task_id: str) -> Dict[str, Any]:
        record = self.store.get(task_id)
        if record is None:
            raise A2AProtocolError("unknown task: %s" % task_id)
        return record.to_dict()

    def cancel(self, task_id: str) -> Dict[str, Any]:
        record = self.store.get(task_id)
        if record is None:
            raise A2AProtocolError("unknown task: %s" % task_id)
        if record.status == TaskStatus.RUNNING:
            record.status = TaskStatus.CANCELLED
            record.error = "cancelled by coordinator"
            self.store.put(record)
        return record.to_dict()

    def artifacts(self, task_id: str) -> List[Dict[str, Any]]:
        record = self.store.get(task_id)
        if record is None:
            raise A2AProtocolError("unknown task: %s" % task_id)
        if record.status != TaskStatus.COMPLETED:
            return []
        return list(record.artifacts)

    def handle(self, body: bytes) -> Dict[str, Any]:
        request = loads_request(body)
        params = request.get("params") or {}
        method = str(request.get("method", ""))
        if not verify_token(self.token, str(params.get("token", "") or "")):
            return server_error_response(
                request.get("id"), "unauthorized: invalid or missing service token", -32001)
        try:
            if method == "agent.discover":
                result = self.card.to_dict()
            elif method == "task.submit":
                result = self.submit(params.get("task") or {})
            elif method == "task.get":
                result = self.get_task(str(params.get("task_id", "")))
            elif method == "task.cancel":
                result = self.cancel(str(params.get("task_id", "")))
            elif method == "artifact.list":
                result = {"artifacts": self.artifacts(str(params.get("task_id", "")))}
            else:
                raise A2AProtocolError("unsupported method: %s" % method)
        except A2AUnauthorizedError as exc:
            return server_error_response(request.get("id"), str(exc), -32001)
        except A2ASchemaError as exc:
            return server_error_response(request.get("id"), str(exc), -32602)
        except A2ARemoteExecutionError as exc:
            return server_error_response(request.get("id"), str(exc), -32003)
        except Exception as exc:  # noqa: BLE001
            return server_error_response(request.get("id"), str(exc), -32603)
        return success_response(request.get("id"), result)

    def _inject(self, phase: str) -> None:
        plan = self.fail_on.get(phase)
        if not plan:
            return
        mode = str(plan.get("mode", ""))
        if mode == "raise":
            raise RuntimeError(str(plan.get("message", "injected failure")))
        if mode == "malformed-jsonrpc":
            raise A2AProtocolError("malformed jsonrpc injected")


__all__ = ["LoopAgentHost", "LoopTaskStore", "LoopTaskRecord"]
