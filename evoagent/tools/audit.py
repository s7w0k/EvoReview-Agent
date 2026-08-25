"""Append-only audit records for every tool invocation.

Each entry captures who called which tool with which (hashed) arguments, whether
it was authorized, and the timing / status of the call so the harness can later
prove *every* tool call was governed.
"""
import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AuditEntry:
    task_id: str
    agent_id: str
    tool_name: str
    arguments_hash: str
    authorized: bool
    side_effect: bool = False
    deny_reason: str = ""
    started_at: float = 0.0
    finished_at: Optional[float] = None
    latency_ms: Optional[float] = None
    status: str = "authorized"
    observation_hash: str = ""
    invocation_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["started_at"] = round(value["started_at"], 4)
        value["finished_at"] = round(value["finished_at"], 4) if value["finished_at"] else None
        value["latency_ms"] = round(value["latency_ms"], 3) if value["latency_ms"] else None
        return value


def hash_args(arguments: Dict[str, Any]) -> str:
    canonical = json.dumps(arguments or {}, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def hash_observation(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ToolAuditLogger:
    """In-memory append-only log; storage adapters can persist drained entries."""

    def __init__(self, sink=None):
        self._entries: List[AuditEntry] = []
        self._sink = sink

    def start(
        self, task_id: str, agent_id: str, tool_name: str, arguments: Dict[str, Any],
        authorized: bool = True, side_effect: bool = False, deny_reason: str = "",
        invocation_id: Optional[str] = None,
    ) -> AuditEntry:
        entry = AuditEntry(
            task_id=task_id, agent_id=agent_id, tool_name=tool_name,
            arguments_hash=hash_args(arguments), authorized=authorized,
            side_effect=side_effect, deny_reason=deny_reason,
            started_at=time.monotonic(),
            invocation_id=invocation_id or uuid.uuid4().hex,
        )
        self._entries.append(entry)
        return entry

    def finish(self, entry: AuditEntry, status: str, observation: Any = None) -> None:
        entry.status = status
        entry.finished_at = time.monotonic()
        entry.latency_ms = (entry.finished_at - entry.started_at) * 1000.0
        if observation is not None:
            entry.observation_hash = hash_observation(observation)

    def entries(self) -> List[AuditEntry]:
        return list(self._entries)

    def drain(self) -> List[AuditEntry]:
        with_lock = getattr(self, "_lock", None)
        if with_lock:
            with with_lock:
                values = list(self._entries)
                self._entries.clear()
        else:
            values = list(self._entries)
            self._entries.clear()
        if self._sink is not None:
            self._sink(values)
        return values