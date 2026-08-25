import hashlib
import json
import mimetypes
import os
import re
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict

from .config import Settings
from .auth import Principal
from .github import verify_signature
from .metrics import metrics
from .report import to_markdown
from .service import ReviewService
from .chat import ChatBusyError, ChatModelError


TASK = re.compile(r"^/v1/tasks/([0-9a-f-]+)$")
REPORT = re.compile(r"^/v1/tasks/([0-9a-f-]+)/report$")
FIX = re.compile(r"^/v1/tasks/([0-9a-f-]+)/fix$")
FEEDBACK = re.compile(r"^/v1/tasks/([0-9a-f-]+)/feedback$")
CHAT_SESSIONS = re.compile(r"^/v1/tasks/([0-9a-f-]+)/chat/sessions$")
CHAT_SESSION = re.compile(r"^/v1/chat/sessions/([0-9a-f-]+)$")
CHAT_MESSAGES = re.compile(r"^/v1/chat/sessions/([0-9a-f-]+)/messages$")
CHAT_ARCHIVE = re.compile(r"^/v1/chat/sessions/([0-9a-f-]+)/archive$")
CHAT_INSIGHT_REJECT = re.compile(r"^/v1/chat/insights/([0-9a-f-]+)/reject$")
CHAT_INSIGHT_EDIT = re.compile(r"^/v1/chat/insights/([0-9a-f-]+)$")
CHAT_INSIGHT_CONFIRM = re.compile(r"^/v1/chat/insights/([0-9a-f-]+)/confirm$")
EVOLUTION_JOBS = re.compile(r"^/v1/evolution/jobs$")
EVOLUTION_JOB = re.compile(r"^/v1/evolution/jobs/([0-9a-f-]+)$")
EVOLUTION_JOB_ACTION = re.compile(
    r"^/v1/evolution/jobs/([0-9a-f-]+)/(pause|resume|cancel|retry)$"
)
CANCEL = re.compile(r"^/v1/tasks/([0-9a-f-]+)/cancel$")
RESUME = re.compile(r"^/v1/tasks/([0-9a-f-]+)/resume$")
ROLLBACK = re.compile(r"^/v1/skills/([A-Za-z0-9_-]+)/versions/(\d+)/activate$")
SKILL_ARTIFACT_VERSIONS = re.compile(r"^/v1/skill-evolution/([a-z0-9_-]+)/versions$")
SKILL_ARTIFACT_ACTIVATE = re.compile(
    r"^/v1/skill-evolution/([a-z0-9_-]+)/versions/(\d+)/activate$"
)
SKILL_ARTIFACT_ARCHIVE = re.compile(
    r"^/v1/skill-evolution/([a-z0-9_-]+)/versions/(\d+)/archive$"
)
WEB_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "web"))


class ApiHandler(BaseHTTPRequestHandler):
    service: ReviewService
    settings: Settings
    server_version = "EvoAgent/0.3"

    def log_message(self, fmt: str, *args: Any) -> None:
        print("%s - %s" % (self.address_string(), fmt % args))

    def _headers(self, status: int, content_type: str, length: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.end_headers()

    def _principal(self, permission: str = "read") -> Principal:
        if not self.settings.auth_required:
            return Principal(
                "local", "local-development", self.settings.default_tenant_id, "admin"
            )
        principal = self.service.auth.authenticate(self.headers.get("Authorization", ""))
        self.service.auth.require(principal, (permission,))
        return principal

    def _authenticate_or_send(self, permission: str = "read"):
        try:
            return self._principal(permission)
        except PermissionError as exc:
            self._send_json(401, {"error": str(exc)})
            return None

    def _require_chat(self, permission: str = "review"):
        """Gate the report-chat feature (dark switch) and return a principal.

        Sends 409 when the feature is disabled and returns ``None``.
        """
        if not self.settings.chat_enabled:
            self._send_json(409, {"error": "chat feature is disabled"})
            return None
        return self._principal(permission)

    def _send_json(self, status: int, value: Dict[str, Any]) -> None:
        body = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def _send_text(self, status: int, text: str, content_type: str = "text/plain; charset=utf-8") -> None:
        body = text.encode("utf-8")
        self._headers(status, content_type, len(body))
        self.wfile.write(body)

    def _serve_file(self, filename: str) -> None:
        path = os.path.abspath(os.path.join(WEB_ROOT, filename))
        if not path.startswith(WEB_ROOT + os.sep) and path != WEB_ROOT:
            self._send_json(404, {"error": "not found"})
            return
        try:
            with open(path, "rb") as handle:
                body = handle.read()
        except OSError:
            self._send_json(404, {"error": "not found"})
            return
        content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        self._headers(200, content_type, len(body))
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("invalid Content-Length")
        limit = self.settings.max_diff_bytes + 256 * 1024
        if length <= 0 or length > limit:
            raise ValueError("request body is empty or too large")
        return self.rfile.read(length)

    @staticmethod
    def _read_json(body: bytes) -> Dict[str, Any]:
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("request body must be valid UTF-8 JSON")
        if not isinstance(value, dict):
            raise ValueError("JSON root must be an object")
        return value

    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query = urllib.parse.parse_qs(parsed_url.query)
        if path == "/":
            self._serve_file("index.html")
            return
        if path == "/assets/app.css":
            self._serve_file("app.css")
            return
        if path == "/assets/login.css":
            self._serve_file("login.css")
            return
        if path == "/assets/app.js":
            self._serve_file("app.js")
            return
        if path == "/health":
            self._send_json(200, {"status": "ok", "reviewer": self.service.reviewer.name,
                                  "runtime": self.service.harness.name,
                                  "queue": self.service.queue.backend,
                                  "llm_provider": self.service.llm_config.get("provider", "local"),
                                  "llm_model": self.service.llm_config.get("model", "")})
            return
        # Work Package 10: liveness and readiness split; /health stays unchanged.
        if path == "/health/live":
            self._send_json(200, {"status": "live"})
            return
        if path == "/health/ready":
            database_ok = self.service.store.ping()
            queue_ok = self.service.queue.ready()
            checks = {
                "database": database_ok,
                "queue": queue_ok,
                "github_token": bool(self.service.settings.github_token),
                "llm": bool(self.service.llm_config),
            }
            ready = database_ok and queue_ok
            self._send_json(
                200 if ready else 503,
                {"status": "ready" if ready else "not_ready", "checks": checks},
            )
            return
        principal = self._authenticate_or_send("read")
        if principal is None:
            return
        if path == "/metrics":
            self._send_text(200, metrics.prometheus(), "text/plain; version=0.0.4; charset=utf-8")
            return
        if path == "/api/dashboard":
            self._send_json(200, {"stats": self.service.store.dashboard_stats(principal.tenant_id),
                                  "tasks": self.service.store.list_tasks(10, principal.tenant_id),
                                  "queue": self.service.queue.backend,
                                  "orchestrator": self.service.reviewer.name,
                                  "llm": {
                                      "enabled": bool(self.service.llm_config),
                                      "provider": self.service.llm_config.get("provider", "local"),
                                      "model": self.service.llm_config.get("model", ""),
                                  },
                                  "chat": {
                                      "enabled": self.settings.chat_enabled,
                                      "insights_enabled": self.settings.chat_insights_enabled,
                                      "feedback_enabled": self.settings.chat_feedback_enabled,
                                      "model_configured": bool(getattr(self.service, "chat_client", None)),
                                  }})
            return
        if path == "/api/tasks":
            self._send_json(200, {"tasks": self.service.store.list_tasks(
                int(query.get("limit", [50])[0]), principal.tenant_id)})
            return
        if path == "/api/skills":
            self._send_json(200, {
                "skills": self.service.list_skills(principal.tenant_id),
                "llm": {
                    "enabled": bool(self.service.llm_config),
                    "provider": self.service.llm_config.get("provider", "local"),
                    "model": self.service.llm_config.get("model", ""),
                },
            })
            return
        if path == "/api/failures":
            if not principal.can("audit"):
                self._send_json(403, {"error": "permission denied"})
                return
            self._send_json(200, {"cases": self.service.store.list_failure_cases(
                False, 100, principal.tenant_id
            )})
            return
        if path == "/api/audit":
            if not principal.can("audit"):
                self._send_json(403, {"error": "permission denied"})
                return
            self._send_json(200, {"events": self.service.store.list_audit(
                principal.tenant_id, int(query.get("limit", [100])[0])
            )})
            return
        if path == "/api/alerts":
            self._send_json(200, {"alerts": self.service.store.list_alerts(principal.tenant_id)})
            return
        if path == "/api/deployments/llm-review":
            self._send_json(200, {"deployment": self.service.store.get_deployment(
                principal.tenant_id, "llm-review"
            )})
            return
        if path == "/api/queue/dead-letters":
            if not principal.can("manage"):
                self._send_json(403, {"error": "permission denied"})
                return
            self._send_json(200, {"messages": self.service.queue.dead_letters(
                int(query.get("limit", [100])[0])
            )})
            return
        if path == "/v1/evaluation/cases":
            split = query.get("split", ["validation"])[0]
            if split == "holdout":
                self._send_json(403, {"error": "holdout cases are not exposed through the API"})
                return
            self._send_json(200, {
                "cases": self.service.store.list_evaluation_cases(split, True, 100)
            })
            return
        if path == "/v1/evolution/runs":
            self._send_json(200, {
                "runs": self.service.store.list_evolution_runs(int(query.get("limit", [50])[0]))
            })
            return
        if path == "/v1/evolution/status":
            status = self.service.evolution.status()
            status["provider"] = self.service.llm_config.get("provider", "local")
            status["model"] = self.service.llm_config.get("model", "")
            self._send_json(200, status)
            return
        jobs_match = EVOLUTION_JOBS.match(path)
        if jobs_match:
            if not principal.can("evolve"):
                self._send_json(403, {"error": "permission denied"})
                return
            self._send_json(200, {"jobs": self.service.evolution_controller.list_jobs(
                principal.tenant_id, int(query.get("limit", [50])[0])
            )})
            return
        job_match = EVOLUTION_JOB.match(path)
        if job_match:
            if not principal.can("evolve"):
                self._send_json(403, {"error": "permission denied"})
                return
            job = self.service.evolution_controller.get_job(
                job_match.group(1), principal.tenant_id)
            if not job:
                self._send_json(404, {"error": "evolution job not found"})
                return
            self._send_json(200, job)
            return
        if path == "/v1/skill-evolution/status":
            if not principal.can("manage"):
                self._send_json(403, {"error": "permission denied"})
                return
            skill_name = query.get("skill_name", ["evolved-review"])[0]
            self._send_json(200, self.service.skill_evolution.status(
                skill_name, principal.tenant_id
            ))
            return
        if path == "/v1/skill-evolution/runs":
            if not principal.can("manage"):
                self._send_json(403, {"error": "permission denied"})
                return
            self._send_json(200, {"runs": self.service.store.list_skill_evolution_runs(
                int(query.get("limit", [50])[0]), principal.tenant_id
            )})
            return
        if path == "/v1/skill-curator/recommendations":
            if not principal.can("manage"):
                self._send_json(403, {"error": "permission denied"})
                return
            recommendations = self.service.curator_recommendations(principal.tenant_id)
            self._send_json(200, {
                "recommendations": recommendations,
                "note": "curator recommendations are advisory and never change skill state",
            })
            return
        match = SKILL_ARTIFACT_VERSIONS.match(path)
        if match:
            if not principal.can("manage"):
                self._send_json(403, {"error": "permission denied"})
                return
            self._send_json(200, {"versions": self.service.store.list_skill_artifact_versions(
                match.group(1), principal.tenant_id
            )})
            return
        if path == "/github/install":
            if not self.settings.github_app_slug:
                self._send_json(503, {"error": "EVOAGENT_GITHUB_APP_SLUG is not configured"})
                return
            self.send_response(302)
            self.send_header("Location", "https://github.com/apps/%s/installations/new" % self.settings.github_app_slug)
            self.end_headers()
            return
        if path == "/github/setup":
            try:
                installation_id = int(query.get("installation_id", [""])[0])
            except ValueError:
                self._send_json(400, {"error": "missing installation_id"})
                return
            self.service.store.save_installation(installation_id, query.get("account", ["github-app"])[0])
            self.send_response(302)
            self.send_header("Location", "/#github")
            self.end_headers()
            return
        report_match = REPORT.match(path)
        task_match = TASK.match(path)
        feedback_match = FEEDBACK.match(path)
        if feedback_match:
            if not principal.can("review"):
                self._send_json(403, {"error": "permission denied"})
                return
            task = self.service.store.get(feedback_match.group(1), principal.tenant_id)
            if not task:
                self._send_json(404, {"error": "task not found"})
                return
            self._send_json(200, {"cases": self.service.store.list_task_failure_cases(
                feedback_match.group(1), principal.tenant_id
            )})
            return
        chat_sessions_match = CHAT_SESSIONS.match(path)
        if chat_sessions_match:
            principal = self._require_chat()
            if principal is None:
                return
            self._send_json(200, {"sessions": self.service.list_task_chat_sessions(
                chat_sessions_match.group(1), principal
            )})
            return
        chat_session_match = CHAT_SESSION.match(path)
        if chat_session_match:
            principal = self._require_chat()
            if principal is None:
                return
            try:
                session = self.service.get_chat_session(chat_session_match.group(1), principal)
            except ValueError as exc:
                self._send_json(404, {"error": str(exc)})
                return
            self._send_json(200, session)
            return
        if report_match:
            task = self.service.store.get(report_match.group(1), principal.tenant_id)
            if not task or not task.get("report"):
                self._send_json(404, {"error": "task or report not found"})
                return
            self._send_text(200, to_markdown(task["report"]), "text/markdown; charset=utf-8")
            return
        if task_match:
            task = self.service.store.get(task_match.group(1), principal.tenant_id)
            if not task:
                self._send_json(404, {"error": "task not found"})
                return
            self._send_json(200, task)
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query = urllib.parse.parse_qs(parsed_url.query)
        try:
            body = self._read_body()
            if path == "/v1/auth/login":
                if not self.settings.auth_required:
                    self._send_json(409, {"error": "authentication is disabled"})
                    return
                payload = self._read_json(body)
                try:
                    result = self.service.auth.login(
                        str(payload.get("username", "")), str(payload.get("password", "")),
                        str(payload.get("tenant_id", "")),
                    )
                except PermissionError as exc:
                    self._send_json(401, {"error": str(exc)})
                    return
                self._send_json(200, result)
                return
            if path == "/v1/reviews":
                principal = self._principal("review")
                payload = self._read_json(body)
                pr = payload.get("pull_request")
                if pr is not None and not isinstance(pr, int):
                    raise ValueError("pull_request must be an integer")
                args = (str(payload.get("repository", "")), str(payload.get("diff", "")), pr)
                if query.get("async", ["false"])[0].lower() == "true":
                    result = self.service.enqueue_review(*args, tenant_id=principal.tenant_id)
                    self._send_json(202, result)
                else:
                    self._send_json(201, self.service.create_review(
                        *args, tenant_id=principal.tenant_id
                    ))
                self.service.store.audit(
                    principal.tenant_id, principal.username, "review.create",
                    str(payload.get("repository", "")), {"async": query.get("async", ["false"])[0]},
                )
                return
            if path == "/webhooks/github":
                if self.headers.get("X-GitHub-Event", "") != "pull_request":
                    self._send_json(202, {"ignored": True, "reason": "unsupported GitHub event"})
                    return
                if not self.settings.github_webhook_secret:
                    self._send_json(503, {"error": "GitHub webhook secret is not configured"})
                    return
                if not verify_signature(self.settings.github_webhook_secret, body,
                                        self.headers.get("X-Hub-Signature-256", "")):
                    self._send_json(401, {"error": "invalid webhook signature"})
                    return
                payload = self._read_json(body)
                updated_at = (payload.get("pull_request") or {}).get("updated_at")
                if updated_at:
                    try:
                        event_time = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                    except ValueError:
                        raise ValueError("invalid pull_request.updated_at")
                    age = abs((datetime.now(timezone.utc) - event_time).total_seconds())
                    if age > self.settings.webhook_max_age_seconds:
                        self._send_json(409, {"error": "webhook is outside the replay window"})
                        return
                delivery_id = self.headers.get("X-GitHub-Delivery", "")
                digest = hashlib.sha256(body).hexdigest()
                self._send_json(202, self.service.handle_github_pull_request(
                    payload, delivery_id, digest
                ))
                return
            match = FIX.match(path)
            if match:
                principal = self._principal("fix")
                payload = self._read_json(body)
                installation_id = payload.get("installation_id")
                if installation_id is not None and not isinstance(installation_id, int):
                    raise ValueError("installation_id must be an integer")
                result = self.service.create_fix(
                    match.group(1), installation_id, principal.tenant_id
                )
                self.service.store.audit(
                    principal.tenant_id, principal.username, "repair.create",
                    match.group(1), {"branch": result.get("branch")},
                )
                self._send_json(201, result)
                return
            match = FEEDBACK.match(path)
            if match:
                principal = self._principal("review")
                payload = self._read_json(body)
                result = self.service.record_feedback(
                    match.group(1), str(payload.get("category", "")), payload.get("finding"),
                    str(payload.get("note", "")), principal.tenant_id,
                    feedbacker=principal.user_id,
                )
                self.service.store.audit(
                    principal.tenant_id, principal.username, "feedback.record", match.group(1),
                    {"category": result["category"]},
                )
                self._send_json(201, result)
                return
            chat_sessions_match = CHAT_SESSIONS.match(path)
            if chat_sessions_match:
                principal = self._require_chat()
                if principal is None:
                    return
                payload = self._read_json(body)
                result = self.service.create_chat_session(
                    chat_sessions_match.group(1), str(payload.get("title", "")), principal
                )
                self.service.store.audit(
                    principal.tenant_id, principal.username, "chat.session.create",
                    result["id"], {"task_id": chat_sessions_match.group(1)},
                )
                self._send_json(201, result)
                return
            chat_messages_match = CHAT_MESSAGES.match(path)
            if chat_messages_match:
                principal = self._require_chat()
                if principal is None:
                    return
                if not self.service.chat_client:
                    self._send_json(409, {"error": "chat model is not configured"})
                    return
                payload = self._read_json(body)
                try:
                    result = self.service.send_chat_message(
                        chat_messages_match.group(1),
                        str(payload.get("content", "")),
                        str(payload.get("client_request_id", "")),
                        principal,
                    )
                except ChatModelError as exc:
                    self.service.store.audit(
                        principal.tenant_id, principal.username, "chat.message.failed",
                        chat_messages_match.group(1), {"reason": exc.reason},
                    )
                    self._send_json(503, {"error": "chat model request failed", "reason": exc.reason})
                    return
                self.service.store.audit(
                    principal.tenant_id, principal.username, "chat.message.send",
                    chat_messages_match.group(1),
                    {"messages": len(result.get("messages", [])),
                     "model": self.service.llm_config.get("model", "")},
                )
                self._send_json(201, result)
                return
            chat_insight_reject_match = CHAT_INSIGHT_REJECT.match(path)
            if chat_insight_reject_match:
                principal = self._require_chat()
                if principal is None:
                    return
                result = self.service.reject_chat_insight(
                    chat_insight_reject_match.group(1), principal
                )
                self.service.store.audit(
                    principal.tenant_id, principal.username, "chat.insight.reject",
                    chat_insight_reject_match.group(1),
                    {"status": result["insight"]["status"]},
                )
                self._send_json(200, result)
                return
            chat_insight_confirm_match = CHAT_INSIGHT_CONFIRM.match(path)
            if chat_insight_confirm_match:
                principal = self._require_chat()
                if principal is None:
                    return
                if not self.settings.chat_feedback_enabled:
                    self._send_json(409, {"error": "chat feedback is disabled"})
                    return
                result = self.service.confirm_chat_insight(
                    chat_insight_confirm_match.group(1), principal
                )
                self.service.store.audit(
                    principal.tenant_id, principal.username, "chat.insight.confirm",
                    chat_insight_confirm_match.group(1),
                    {"status": result["insight"]["status"],
                     "feedback_case_id": result["insight"].get("feedback_case_id")},
                )
                self._send_json(200, result)
                return
            chat_archive_match = CHAT_ARCHIVE.match(path)
            if chat_archive_match:
                principal = self._require_chat()
                if principal is None:
                    return
                result = self.service.archive_chat_session(
                    chat_archive_match.group(1), principal
                )
                self.service.store.audit(
                    principal.tenant_id, principal.username, "chat.session.archive",
                    chat_archive_match.group(1), {"status": result["status"]},
                )
                self._send_json(200, result)
                return
            if path == "/v1/chat/retention/purge":
                principal = self._require_chat("manage")
                if principal is None:
                    return
                result = self.service.purge_chat_history(principal)
                self.service.store.audit(
                    principal.tenant_id, principal.username, "chat.retention.purge",
                    "", {"purged": result["purged"]},
                )
                self._send_json(200, result)
                return
            match = CANCEL.match(path)
            if match:
                principal = self._principal("review")
                ok = self.service.cancel_task(match.group(1), principal.tenant_id)
                self.service.store.audit(
                    principal.tenant_id, principal.username, "task.cancel", match.group(1)
                )
                self._send_json(202 if ok else 404, {"cancel_requested": ok})
                return
            match = RESUME.match(path)
            if match:
                principal = self._principal("review")
                result = self.service.resume_task(match.group(1), principal.tenant_id)
                self.service.store.audit(
                    principal.tenant_id, principal.username, "task.resume", match.group(1)
                )
                self._send_json(202, result)
                return
            if path == "/v1/skills/reload":
                principal = self._principal("manage")
                self._send_json(200, {"skills": self.service.reload_skills(),
                                      "note": "New tasks now use the reloaded skill set."})
                return
            if path == "/v1/deployments/llm-review":
                principal = self._principal("manage")
                payload = self._read_json(body)
                result = self.service.releases.configure(
                    principal.tenant_id, "llm-review", payload
                )
                self.service.store.audit(
                    principal.tenant_id, principal.username, "deployment.configure",
                    "llm-review", payload,
                )
                self._send_json(201, result)
                return
            if path == "/v1/queue/dead-letters/replay":
                principal = self._principal("manage")
                payload = self._read_json(body)
                ok = self.service.queue.replay_dead_letter(
                    str(payload.get("message_id", ""))
                )
                self._send_json(202 if ok else 404, {"replayed": ok})
                return
            if path == "/v1/evaluation/cases":
                self._principal("manage")
                payload = self._read_json(body)
                result = self.service.evolution.add_evaluation_case(
                    str(payload.get("name", "")),
                    str(payload.get("diff", "")),
                    payload.get("expected_findings", []),
                    str(payload.get("split", "validation")),
                    "api",
                )
                self._send_json(201, result)
                return
            if path == "/v1/evolution/auto":
                principal = self._principal("manage")
                payload = self._read_json(body)
                result = self.service.evolution.auto_propose(
                    str(payload.get("skill_name", "llm-review")), principal.tenant_id
                )
                if result["decision"] == "activated":
                    self.service.reload_skills()
                self._send_json(201, result)
                return
            jobs_match = EVOLUTION_JOBS.match(path)
            if jobs_match:
                principal = self._principal("evolve")
                payload = self._read_json(body)
                outcome = self.service.evolution_controller.enqueue(
                    principal.tenant_id,
                    str(payload.get("capability_kind", "")),
                    str(payload.get("capability_name", "")),
                    str(payload.get("trigger_type", "manual")),
                    str(payload.get("trigger_ref", "")),
                    payload.get("repository_scope"),
                )
                self.service.store.audit(
                    principal.tenant_id, principal.username, "evolution.job.enqueue",
                    outcome["job"]["id"],
                    {"created": outcome["created"],
                     "capability": outcome["job"]["capability_name"]},
                )
                self._send_json(201 if outcome["created"] else 200, outcome)
                return
            job_action_match = EVOLUTION_JOB_ACTION.match(path)
            if job_action_match:
                principal = self._principal("evolve")
                job_id = job_action_match.group(1)
                action = job_action_match.group(2)
                controller = self.service.evolution_controller
                if action == "pause":
                    result = controller.pause(job_id, principal.tenant_id)
                elif action == "resume":
                    result = controller.resume(job_id, principal.tenant_id)
                elif action == "cancel":
                    result = controller.cancel(job_id, principal.tenant_id)
                else:
                    result = controller.retry(job_id, principal.tenant_id)
                self.service.store.audit(
                    principal.tenant_id, principal.username,
                    "evolution.job.%s" % action, job_id,
                    {"status": result["job"]["status"]},
                )
                self._send_json(200, result)
                return
            if path == "/v1/evolution/propose":
                self._principal("manage")
                payload = self._read_json(body)
                result = self.service.evolution.propose(
                    str(payload.get("skill_name", "")), str(payload.get("prompt", "")),
                    float(payload["regression_score"]) if "regression_score" in payload else None,
                )
                if result["decision"] == "activated":
                    self.service.reload_skills()
                self._send_json(201, result)
                return
            if path == "/v1/skill-evolution/auto":
                principal = self._principal("manage")
                payload = self._read_json(body)
                result = self.service.skill_evolution.auto_propose(
                    str(payload.get("skill_name", "evolved-review")), principal.tenant_id
                )
                if result["decision"] == "activated":
                    self.service.reload_skills()
                self.service.store.audit(
                    principal.tenant_id, principal.username, "skill.evolution.auto",
                    str(payload.get("skill_name", "evolved-review")),
                    {"decision": result["decision"], "run_id": result.get("run_id")},
                )
                self._send_json(201, result)
                return
            if path == "/v1/skill-evolution/propose":
                principal = self._principal("manage")
                payload = self._read_json(body)
                result = self.service.skill_evolution.propose(
                    str(payload.get("skill_name", "")), payload.get("artifact"),
                    principal.tenant_id,
                )
                if result["decision"] == "activated":
                    self.service.reload_skills()
                self.service.store.audit(
                    principal.tenant_id, principal.username, "skill.evolution.propose",
                    str(payload.get("skill_name", "")),
                    {"decision": result["decision"], "run_id": result.get("run_id")},
                )
                self._send_json(201, result)
                return
            match = SKILL_ARTIFACT_ACTIVATE.match(path)
            if match:
                principal = self._principal("manage")
                ok = self.service.skill_evolution.rollback(
                    match.group(1), int(match.group(2)), principal.tenant_id
                )
                if ok:
                    self.service.reload_skills()
                self.service.store.audit(
                    principal.tenant_id, principal.username, "skill.evolution.activate",
                    match.group(1), {"version": int(match.group(2)), "activated": ok},
                )
                self._send_json(200 if ok else 404, {"activated": ok})
                return
            match = SKILL_ARTIFACT_ARCHIVE.match(path)
            if match:
                principal = self._principal("manage")
                try:
                    request_body = self._read_json(body) if body else {}
                except ValueError:
                    request_body = {}
                reason = str(request_body.get("reason", "") if isinstance(request_body, dict) else "")[:500]
                ok = self.service.store.transition_skill_artifact(
                    principal.tenant_id, match.group(1), int(match.group(2)),
                    "archived", principal.username, reason,
                )
                self.service.store.audit(
                    principal.tenant_id, principal.username, "skill.evolution.archive",
                    match.group(1),
                    {"version": int(match.group(2)), "archived": ok, "reason": reason},
                )
                self._send_json(200 if ok else 409, {"archived": ok})
                return
            match = ROLLBACK.match(path)
            if match:
                self._principal("manage")
                ok = self.service.evolution.rollback(match.group(1), int(match.group(2)))
                if ok:
                    self.service.reload_skills()
                self._send_json(200 if ok else 404, {"activated": ok})
                return
            self._send_json(404, {"error": "not found"})
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
        except ChatBusyError as exc:
            self._send_json(409, {"error": str(exc)})
        except PermissionError as exc:
            self._send_json(403, {"error": str(exc)})
        except Exception as exc:
            metrics.inc("http_errors_total")
            self._send_json(500, {"error": "operation failed", "detail": str(exc)})

    def do_PATCH(self) -> None:
        """Work Package 5: edit a draft chat insight (category/finding/note)."""
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        try:
            body = self._read_body()
            match = CHAT_INSIGHT_EDIT.match(path)
            if not match:
                self._send_json(404, {"error": "not found"})
                return
            principal = self._require_chat()
            if principal is None:
                return
            payload = self._read_json(body)
            result = self.service.edit_chat_insight(
                match.group(1), payload.get("category"), payload.get("finding"),
                payload.get("note"), principal,
            )
            self.service.store.audit(
                principal.tenant_id, principal.username, "chat.insight.edit",
                match.group(1),
                {"category": result["category"], "status": result["status"]},
            )
            self._send_json(200, result)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
        except PermissionError as exc:
            self._send_json(403, {"error": str(exc)})
        except Exception as exc:
            metrics.inc("http_errors_total")
            self._send_json(500, {"error": "operation failed", "detail": str(exc)})


def run() -> None:
    settings = Settings.from_env()
    service = ReviewService(settings)
    handler = type("ConfiguredApiHandler", (ApiHandler,), {"service": service, "settings": settings})
    server = ThreadingHTTPServer((settings.host, settings.port), handler)
    print("EvoAgent dashboard: http://%s:%d" % (settings.host, settings.port))
    print("Persistence: %s | Queue: %s | Orchestrator: %s" % (
        "postgresql" if settings.database_url else "sqlite", service.queue.backend, service.reviewer.name
    ))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        # Work Package 10: fixed shutdown order.
        # 1. HTTP stops accepting new requests.
        server.shutdown()
        server.server_close()
        # 2. Queue drains/stops workers, then 3. Trace exporter shuts down.
        #    Store connections are per-operation (context-managed) so there is
        #    no persistent database handle to close here.
        service.close()
