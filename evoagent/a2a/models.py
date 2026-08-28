"""A2A wire-level domain model (Phase 1).

The remote protocol deliberately does *not* leak internal ``AgentMessage`` or
``Finding`` objects.  The A2A layer owns its own, versioned schema so the
transport boundary has a stable contract.  Every object here is pure data and
JSON (de)serialisable without FastAPI.
"""
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List

PROTOCOL_VERSION = "v1"


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _uuid() -> str:
    return uuid.uuid4().hex


@dataclass
class AgentCard:
    agent_id: str
    name: str
    endpoint: str
    protocol_version: str
    capabilities: List[str] = field(default_factory=list)
    domains: List[str] = field(default_factory=list)
    supported_task_types: List[str] = field(default_factory=list)
    version: str = "1.0.0"
    health_status: str = "healthy"
    deployment: str = ""  # "local" | "http"
    last_seen: str = ""

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["protocol_version"] = self.protocol_version
        return value

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "AgentCard":
        return cls(
            agent_id=str(value["agent_id"]),
            name=str(value.get("name", value["agent_id"])),
            endpoint=str(value.get("endpoint", "")),
            protocol_version=str(
                value.get("protocol_version", PROTOCOL_VERSION)
            ),
            capabilities=[str(item) for item in value.get("capabilities", [])],
            domains=[str(item) for item in value.get("domains", [])],
            supported_task_types=[
                str(item) for item in value.get("supported_task_types", [])
            ],
            version=str(value.get("version", "1.0.0")),
            health_status=str(value.get("health_status", "healthy")),
            deployment=str(value.get("deployment", "")),
            last_seen=str(value.get("last_seen", "")),
        )

    def __post_init__(self) -> None:
        if not self.protocol_version:
            self.protocol_version = PROTOCOL_VERSION
        if not self.version:
            self.version = "1.0.0"


@dataclass
class A2ATask:
    task_id: str
    assignment_id: str
    sender: str
    recipient: str
    task_type: str
    input: Dict[str, Any]
    context: Dict[str, Any] = field(default_factory=dict)
    correlation_id: str = ""
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "A2ATask":
        return cls(
            task_id=str(value["task_id"]),
            assignment_id=str(value.get("assignment_id", "")),
            sender=str(value.get("sender", "")),
            recipient=str(value.get("recipient", "")),
            task_type=str(value.get("task_type", "review-assignment")),
            input=dict(value.get("input") or {}),
            context=dict(value.get("context") or {}),
            correlation_id=str(value.get("correlation_id", "")),
            created_at=str(value.get("created_at", "")),
        )


@dataclass
class A2AArtifact:
    artifact_id: str
    task_id: str
    artifact_type: str
    producer: str
    content: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "A2AArtifact":
        return cls(
            artifact_id=str(value.get("artifact_id", "")),
            task_id=str(value.get("task_id", "")),
            artifact_type=str(value.get("artifact_type", "raw")),
            producer=str(value.get("producer", "")),
            content=dict(value.get("content") or {}),
            metadata=dict(value.get("metadata") or {}),
        )


@dataclass
class A2AMessage:
    message_id: str
    task_id: str
    sender: str
    recipient: str
    message_type: str
    payload: Dict[str, Any]
    correlation_id: str = ""
    timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "A2AMessage":
        return cls(
            message_id=str(value.get("message_id", "")),
            task_id=str(value.get("task_id", "")),
            sender=str(value.get("sender", "")),
            recipient=str(value.get("recipient", "")),
            message_type=str(value.get("message_type", "message")),
            payload=dict(value.get("payload") or {}),
            correlation_id=str(value.get("correlation_id", "")),
            timestamp=str(value.get("timestamp", "")),
        )


@dataclass
class A2AResult:
    """A completed remote task result: status + any produced artifacts."""

    task_id: str
    status: TaskStatus
    artifacts: List[A2AArtifact] = field(default_factory=list)
    error: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        value["artifacts"] = [item.to_dict() for item in self.artifacts]
        return value

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "A2AResult":
        return cls(
            task_id=str(value["task_id"]),
            status=TaskStatus(str(value.get("status", TaskStatus.FAILED.value))),
            artifacts=[A2AArtifact.from_dict(item) for item in value.get("artifacts", [])],
            error=str(value.get("error", "")),
            metadata=dict(value.get("metadata") or {}),
        )


__all__ = [
    "PROTOCOL_VERSION",
    "TaskStatus",
    "utcnow",
    "_uuid",
    "AgentCard",
    "A2ATask",
    "A2AArtifact",
    "A2AMessage",
    "A2AResult",
]