"""Minimal stdlib HTTP server exposing an :class:`AgentServiceHost` over
JSON-RPC (Phase 12 / test helper).

The FastAPI ``services/*/app.py`` apps are the production front-end; this
dependency-free server is used by unit/contract/failure-injection tests so they
do not require FastAPI.  It implements the same route contract:
``GET /health``, ``GET /a2a/agent-card``, ``POST /a2a``.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from .models import AgentCard
from .service import AgentServiceHost


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # noqa: A003 - silence noisy test logs
        pass

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        host: "AgentServer" = self.server.host  # type: ignore[attr-defined]
        if self.path.rstrip("/") in {"/health", "/health/" }:
            self._json(200, {"status": "healthy", "agent_id": host.card.agent_id})
        elif self.path.rstrip("/") in {"/a2a/agent-card", "/a2a/agent-card/"}:
            self._json(200, host.card.to_dict())
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        host: "AgentServer" = self.server.host  # type: ignore[attr-defined]
        if self.path.rstrip("/") != "/a2a":
            self._json(404, {"error": "not found"})
            return
        plan = host.fail_on.get("http") or {}
        mode = str(plan.get("mode", ""))
        if mode == "status-code":
            self._json(int(plan.get("status", 500)), {"error": "injected status"})
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        if mode == "malformed-http":
            self._raw(b"this-is-not-json{")
            return
        response = host.handle(body)
        self._json(200, response)

    def _raw(self, payload: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class AgentServer:
    """Threaded stdlib server around one :class:`AgentServiceHost`."""

    def __init__(self, host: AgentServiceHost, port: int = 0, bind: str = "127.0.0.1"):
        self.host = host
        self._httpd = ThreadingHTTPServer((bind, port), _Handler)
        self._httpd.host = host  # type: ignore[attr-defined]
        self.port = self._httpd.server_address[1]
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "AgentServer":
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._httpd.server_close()

    @property
    def endpoint(self) -> str:
        return "http://127.0.0.1:%d" % self.port

    def card(self) -> AgentCard:
        return self.host.card

    def call(self, body: bytes):
        return self.host.handle(body)

    @property
    def fail_on(self) -> dict:
        return self.host.fail_on


__all__ = ["AgentServer", "_Handler"]