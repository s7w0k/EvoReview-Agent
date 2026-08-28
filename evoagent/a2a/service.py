"""Reusable A2A JSON-RPC service host (Phase 4/5/9).

This module owns the remote Agent behaviour once, without FastAPI, so it can be
served by a stdlib HTTP server (tests, local ``docker``) or by the FastAPI apps
under ``services/*/app.py``.  It keeps a task lifecycle store
(``PENDING -> RUNNING -> COMPLETED|FAILED|CANCELLED``) and produces a single
``review-findings`` :class:`A2AArtifact`.
"""
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..diff_parser import parse_unified_diff
from ..reviewer import Reviewer

from .errors import (
    A2AProtocolError,
    A2ARemoteExecutionError,
    A2ASchemaError,
    A2AUnauthorizedError,
)
from .governance import ArtifactSanitizer, verify_token
from .models import A2ATask, AgentCard, TaskStatus
from .protocol import (
    artifact_from_findings,
    loads_request,
    success_response,
    server_error_response,
    validate_task_fields,
)


@dataclass
class TaskRecord:
    task_id: str
    recipient: str
    status: TaskStatus
    error: str = ""
    artifacts: List[Dict[str, Any]] = None  # type: ignore[assignment]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "recipient": self.recipient,
            "status": self.status.value,
            "error": self.error,
            "artifacts": list(self.artifacts or []),
        }


class TaskStore:
    """In-memory lifecycle store for a single remote agent process."""

    def __init__(self):
        self._lock = threading.Lock()
        self._tasks: Dict[str, TaskRecord] = {}

    def put(self, record: TaskRecord) -> None:
        with self._lock:
            self._tasks[record.task_id] = record

    def get(self, task_id: str) -> Optional[TaskRecord]:
        with self._lock:
            return self._tasks.get(task_id)

    def all(self) -> List[TaskRecord]:
        with self._lock:
            return sorted(self._tasks.values(), key=lambda item: item.task_id)


class AgentServiceHost:
    """Adapts a :class:`Reviewer` into an A2A remote Agent."""

    def __init__(
        self, reviewer: Reviewer, card: Any, *,
        token: str = "", sanitizer: Optional[ArtifactSanitizer] = None,
        store: Optional[TaskStore] = None, delay_seconds: float = 0.0,
        fail_on: Dict[str, Any] = None,
    ):
        self.reviewer = reviewer
        self.card = self._coerce_card(card)
        self.token = token
        self.sanitizer = sanitizer or ArtifactSanitizer()
        self.store = store or TaskStore()
        self.delay_seconds = max(0.0, delay_seconds)  # failure injection: slow-agent
        self.fail_on = dict(fail_on or {})

    @staticmethod
    def _coerce_card(card: Any) -> "AgentCard":
        if isinstance(card, AgentCard):
            return card
        return AgentCard.from_dict(dict(card or {}))

    # -- lifecycle ---------------------------------------------------------
    def submit(self, task: Dict[str, Any]) -> Dict[str, Any]:
        validate_task_fields(task)
        self._inject("submit")
        obj = A2ATask.from_dict(task)
        self.store.put(TaskRecord(obj.task_id, obj.recipient, TaskStatus.RUNNING))
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        try:
            findings = self._run(obj)
        except Exception as exc:  # noqa: BLE001 - report as remote execution error
            record = TaskRecord(obj.task_id, obj.recipient, TaskStatus.FAILED, str(exc)[:1000])
            self.store.put(record)
            raise A2ARemoteExecutionError(
                "remote agent %s failed: %s" % (self.card.agent_id, exc),
                target_agent=self.card.agent_id,
            )
        artifact = artifact_from_findings(
            obj.task_id, self.card.agent_id, findings,
            {"protocol_version": self.card.protocol_version},
        )
        safe = self.sanitizer.sanitize(artifact)
        record = TaskRecord(
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
        return list(record.artifacts or [])

    # -- internals ---------------------------------------------------------
    def _run(self, task: A2ATask) -> Any:
        input_data = task.input or {}
        diff = str(input_data.get("diff", "") or "")
        if not diff:
            raise A2ASchemaError("task.input.diff is required")
        parsed = parse_unified_diff(diff)
        return self.reviewer.review(diff, parsed)

    def _inject(self, phase: str) -> None:
        plan = self.fail_on.get(phase)
        if not plan:
            return
        mode = str(plan.get("mode", ""))
        if mode == "status-code":
            return  # handled at the HTTP layer
        if mode == "raise":
            raise RuntimeError(str(plan.get("message", "injected failure")))
        if mode == "malformed-jsonrpc":
            raise A2AProtocolError("malformed jsonrpc injected")

    # -- JSON-RPC dispatch -------------------------------------------------
    def handle(self, body: bytes) -> Dict[str, Any]:
        request = loads_request(body)
        params = request.get("params") or {}
        method = str(request.get("method", ""))
        if not verify_token(self.token, str(params.get("token", "") or "")):
            return server_error_response(
                request.get("id"), "unauthorized: invalid or missing service token", -32001,
            )
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


__all__ = ["AgentServiceHost", "TaskStore", "TaskRecord"]