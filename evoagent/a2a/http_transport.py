"""HTTP + JSON-RPC 2.0 A2A transport (Phase 2/4/9).

Uses only the stdlib (``urllib``) so it works in the unit-test environment.
Maps the JSON-RPC error taxonomy to the A2A errors:
- connection / 5xx / timeout are transient (retryable),
- malformed JSON-RPC / unsupported version maps to ``A2AProtocolError``,
- 401/403 map to ``A2AUnauthorizedError``.
"""
import json
import socket
import urllib.error
import urllib.request
from typing import Dict, List

from .errors import (
    A2AConnectionError,
    A2AProtocolError,
    A2ARemoteExecutionError,
    A2ASchemaError,
    A2ATimeoutError,
    A2AUnauthorizedError,
    A2AUnavailableError,
)
from .models import A2ATask, AgentCard
from .protocol import build_request, is_valid_response
from .transport import A2ATransport

_HTTP_ERROR_CODES = {
    400: A2AProtocolError,
    401: A2AUnauthorizedError,
    403: A2AUnauthorizedError,
    408: A2ATimeoutError,
    500: A2AUnavailableError,
    502: A2AUnavailableError,
    503: A2AUnavailableError,
    504: A2ATimeoutError,
}


class HttpJsonRpcA2ATransport(A2ATransport):
    name = "http-jsonrpc"

    def __init__(self, token: str = "", timeout_seconds: float = 10.0,
                 base_path: str = "/a2a"):
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.base_path = base_path

    def _url(self, card: AgentCard) -> str:
        base = (card.endpoint or "").rstrip("/")
        path = self.base_path if not self.base_path.startswith("/") else self.base_path
        return base + path

    def _call(self, card: AgentCard, method: str, params: Dict[str, object]) -> Dict[str, object]:
        request_payload = build_request(method, params)
        # The shared server host verifies the token from the JSON-RPC params.
        if self.token:
            request_payload["params"] = dict(params or {})
            request_payload["params"]["token"] = self.token
        body = json.dumps(request_payload).encode("utf-8")
        url = self._url(card)
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            status = exc.code
            error_cls = _HTTP_ERROR_CODES.get(status, A2AProtocolError)
            detail = exc.read(500).decode("utf-8", errors="replace")
            raise error_cls(
                "remote agent %s %s => HTTP %d: %s" % (card.agent_id, method, status, detail),
                target_agent=card.agent_id,
            ) from exc
        except socket.timeout as exc:
            raise A2ATimeoutError(
                "remote agent %s %s timed out after %.1fs" % (
                    card.agent_id, method, self.timeout_seconds),
                target_agent=card.agent_id,
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise A2AConnectionError(
                "remote agent %s unreachable (%s)" % (card.agent_id, exc),
                target_agent=card.agent_id,
            ) from exc
        try:
            response = json.loads(raw)
        except ValueError as exc:
            raise A2AProtocolError(
                "remote agent %s returned non-JSON" % card.agent_id,
                target_agent=card.agent_id,
            ) from exc
        if not is_valid_response(response):
            raise A2AProtocolError(
                "remote agent %s returned a malformed JSON-RPC response" % card.agent_id,
                target_agent=card.agent_id,
            )
        error = response.get("error")
        if error is not None:
            message = str(error.get("message", ""))
            code = error.get("code")
            cls = A2ARemoteExecutionError
            if code == -32001:
                cls = A2AUnauthorizedError
            elif code == -32602:
                cls = A2ASchemaError
            raise cls(message or "remote error", target_agent=card.agent_id)
        return response.get("result") or {}

    def discover(self, endpoint: str) -> dict:
        card = AgentCard(agent_id="__discover__", name="", endpoint=endpoint,
                         protocol_version="v1")
        result = self._call(card, "agent.discover", {})
        got_version = str(result.get("protocol_version", ""))
        if got_version and got_version != "v1":
            raise A2AProtocolError(
                "agent at %s advertises unsupported protocol version %r (expected v1)"
                % (endpoint, got_version),
                target_agent=str(result.get("agent_id", "")),
            )
        return result

    def submit_task(self, card: AgentCard, task: A2ATask) -> dict:
        return self._call(card, "task.submit", {"task": task.to_dict()})

    def get_task(self, card: AgentCard, task_id: str) -> dict:
        return self._call(card, "task.get", {"task_id": task_id})

    def cancel_task(self, card: AgentCard, task_id: str) -> dict:
        return self._call(card, "task.cancel", {"task_id": task_id})

    def get_artifacts(self, card: AgentCard, task_id: str) -> List[dict]:
        result = self._call(card, "artifact.list", {"task_id": task_id})
        return list(result.get("artifacts", []) or [])


__all__ = ["HttpJsonRpcA2ATransport"]