"""A2A protocol framing and internal-to-wire adapters (Phase 1/2).

- ``build_request`` / ``is_valid_response``: JSON-RPC 2.0 framing.
- ``message_from_bus`` / ``message_to_bus``: ``AgentMessage <-> A2AMessage``.
- ``artifact_from_findings`` / ``findings_from_artifact``: ``Finding <-> A2AArtifact``.
- ``validate_*``: lightweight schema guards for the wire boundaries.

The adapters deliberately keep the internal objects (*not*) the network schema:
the coordinator keeps sending ``AgentMessage`` on the in-process
``CollaborationBus``; only at the transport edge do we translate.
"""
import json
import uuid
from typing import Any, Dict, List, Optional

from ..models import Finding, Severity

from .errors import A2AProtocolError, A2ASchemaError
from .models import A2AArtifact, A2AMessage, PROTOCOL_VERSION, utcnow

_METHODS = {
    "agent.discover",
    "task.submit",
    "task.get",
    "task.cancel",
    "artifact.list",
}

#: Envelope shape required by every JSON-RPC request.
_REQUIRED_REQUEST_FIELDS = {"jsonrpc", "method", "params"}


def new_id() -> str:
    return uuid.uuid4().hex


def build_request(
    method: str, params: Dict[str, Any], request_id: Optional[str] = None,
) -> Dict[str, Any]:
    if method not in _METHODS:
        raise A2ASchemaError("unsupported A2A method: %s" % method)
    return {
        "jsonrpc": "2.0",
        "id": request_id or new_id(),
        "method": method,
        "params": dict(params or {}),
    }


def is_valid_response(response: Dict[str, Any]) -> bool:
    """A JSON-RPC 2.0 response is valid if it carries exactly (id + result)
    or (id + error)."""
    return isinstance(response, dict) and "id" in response and (
        "result" in response or "error" in response
    )


def response_error(payload: Dict[str, Any]) -> Dict[str, Any]:
    return dict(payload.get("error") or {})


def server_error_response(request_id: Any, message: str, code: int = -32603) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": str(message)},
    }


def success_response(request_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def validate_task_fields(task: Dict[str, Any]) -> None:
    if not isinstance(task, dict):
        raise A2ASchemaError("`task` must be an object")
    if not task.get("task_id"):
        raise A2ASchemaError("`task.task_id` is required")
    if not task.get("recipient"):
        raise A2ASchemaError("`task.recipient` is required")
    if not isinstance(task.get("input"), dict):
        raise A2ASchemaError("`task.input` must be an object")


SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3}
_SEVERITY_NAMES = {item.value for item in Severity}


def artifact_from_findings(
    task_id: str, producer: str, findings: List[Finding],
    metadata: Optional[Dict[str, Any]] = None,
) -> A2AArtifact:
    """Wrap an ordered list of :class:`Finding` into a single A2AArtifact.

    The artifact ``content`` carries *findings* as a pure-list of dicts so the
    wire object is decoupled from the internal :class:`Finding` dataclass.
    """
    items: List[Dict[str, Any]] = []
    for finding in findings:
        items.append({
            "rule_id": finding.rule_id,
            "severity": (
                finding.severity.value if isinstance(finding.severity, Severity)
                else str(finding.severity)
            ),
            "title": finding.title,
            "explanation": finding.explanation,
            "path": finding.path,
            "line": int(finding.line),
            "evidence": finding.evidence,
            "fix": finding.fix,
            "test": finding.test,
            "confidence": float(finding.confidence),
        })
    sort_key = lambda item: (  # noqa: E731
        SEVERITY_ORDER.get(Severity(item["severity"]), 9), item["path"], item["line"]
    )
    items.sort(key=sort_key)
    return A2AArtifact(
        artifact_id="art-%s" % uuid.uuid4().hex[:12],
        task_id=task_id,
        artifact_type="review-findings",
        producer=producer,
        content={"findings": items},
        metadata=dict(metadata or {}),
    )


def findings_from_artifact(artifact: A2AArtifact) -> List[Finding]:
    raw_items = (artifact.content or {}).get("findings", [])
    findings: List[Finding] = []
    for raw in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(raw, dict):
            continue
        severity_raw = str(raw.get("severity", "medium")).lower()
        severity = Severity(severity_raw) if severity_raw in _SEVERITY_NAMES else Severity.MEDIUM
        try:
            line = int(raw.get("line", 0))
        except (TypeError, ValueError):
            line = 0
        try:
            confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.8))))
        except (TypeError, ValueError):
            confidence = 0.8
        findings.append(Finding(
            rule_id=str(raw.get("rule_id", "A2A-REMOTE"))[:80],
            severity=severity,
            title=str(raw.get("title", "Remote finding"))[:200],
            explanation=str(raw.get("explanation", ""))[:2000],
            path=str(raw.get("path", "unknown")),
            line=line,
            evidence=str(raw.get("evidence", ""))[:240],
            fix=str(raw.get("fix", ""))[:2000],
            test=str(raw.get("test", ""))[:2000],
            confidence=confidence,
        ))
    return findings


def message_from_bus(
    task_id: str, sender: str, recipient: str, message_type: str,
    payload: Dict[str, Any], correlation_id: str = "",
) -> A2AMessage:
    return A2AMessage(
        message_id="msg-%s" % uuid.uuid4().hex[:12],
        task_id=task_id,
        sender=sender,
        recipient=recipient,
        message_type=message_type,
        payload=dict(payload or {}),
        correlation_id=correlation_id,
        timestamp=utcnow(),
    )


def message_to_bus(message: A2AMessage) -> Dict[str, Any]:
    """Translate a wire ``A2AMessage`` back to the CollaborationBus shape.

    The internal bus message is ``{sender, recipient, kind, content,
    correlation_id}``; we map ``message_type -> kind`` and ``payload -> content``
    plus an extra ``a2a`` envelope so trace/replay can rebuild the remote span.
    """
    return {
        "sender": message.sender,
        "recipient": message.recipient,
        "kind": message.message_type,
        "content": dict(message.payload or {}),
        "correlation_id": message.correlation_id,
        "a2a": {
            "message_id": message.message_id,
            "task_id": message.task_id,
            "timestamp": message.timestamp,
            "protocol_version": PROTOCOL_VERSION,
        },
    }


def loads_request(body: bytes) -> Dict[str, Any]:
    try:
        value = json.loads(body.decode("utf-8") if isinstance(body, bytes) else body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise A2AProtocolError("malformed JSON-RPC request: %s" % exc) from exc
    if not isinstance(value, dict):
        raise A2AProtocolError("JSON-RPC request must be an object")
    if not _REQUIRED_REQUEST_FIELDS.issubset(value):
        missing = _REQUIRED_REQUEST_FIELDS - set(value)
        raise A2ASchemaError("missing JSON-RPC fields: %s" % sorted(missing))
    if value.get("jsonrpc") != "2.0":
        raise A2AProtocolError("unsupported JSON-RPC version: %r" % value.get("jsonrpc"))
    return value