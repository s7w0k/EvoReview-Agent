"""Phase 6: service-level deployment HTTP surface.

Drives the new ``/v1/runtime-policies``, ``/v1/policy-deployments`` and
``/v1/policy-evolution/propose`` endpoints through the real :class:`ApiHandler`
so the write path exercises RBAC, tenant scoping and persistent state.
"""
import io
import os
import tempfile
import unittest

from evoagent.api import ApiHandler
from evoagent.config import Settings
from evoagent.service import ReviewService

TENANT = "default"


def _settings(path: str):
    return Settings(
        host="127.0.0.1", port=8080, db_path=path, max_diff_bytes=20000,
        max_steps=8, timeout_seconds=10, llm_base_url="", llm_api_key="",
        llm_model="", github_webhook_secret="", github_token="",
        auto_post_review=False,
    )


class _CapturingHandler(ApiHandler):
    service = None
    settings = None

    def __init__(self, path, body=None):
        self.path = path
        data = body or b""
        self.rfile = io.BytesIO(data)
        self.headers = {"Content-Length": str(len(data))}
        self.captured_status = None
        self.captured_body = None

    def send_response(self, code, message=None):
        self.captured_status = code

    def send_header(self, *args, **kwargs):  # noqa: D102
        pass

    def end_headers(self):  # noqa: D102
        pass

    def _headers(self, status, content_type, length):  # noqa: D102
        self.captured_status = status

    def _send_json(self, status, value):  # noqa: D102
        self.captured_status = status
        self.captured_body = value

    def _send_text(self, status, text, content_type="text/plain; charset=utf-8"):  # noqa: D102
        self.captured_status = status
        self.captured_body = text

    def log_message(self, fmt, *args):  # noqa: D102
        pass


class ServiceDeploymentApiTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.service = ReviewService(_settings(self.path))
        _CapturingHandler.service = self.service
        _CapturingHandler.settings = self.service.settings

    def tearDown(self):
        self.service.close()
        for suffix in ("", ".control.json"):
            try:
                os.unlink(self.path + suffix)
            except OSError:
                pass

    def _get(self, path):
        handler = _CapturingHandler.__new__(_CapturingHandler)
        _CapturingHandler.__init__(handler, path)
        handler.headers["Authorization"] = "Bearer dev"
        handler.do_GET()
        return handler.captured_status, handler.captured_body

    def _post(self, path, payload):
        handler = _CapturingHandler.__new__(_CapturingHandler)
        _CapturingHandler.__init__(handler, path,
                                   body=payload.encode("utf-8"))
        handler.headers["Authorization"] = "Bearer dev"
        handler.do_POST()
        return handler.captured_status, handler.captured_body

    def test_runtime_policies_list_and_get(self):
        status, body = self._get("/v1/runtime-policies")
        self.assertEqual(200, status)
        self.assertEqual({"policies"}, set(body.keys()))
        # bootstrap baselines are registered for every risk level.
        self.assertIn("policies", body)
        ids = {p["policy_id"] for p in body["policies"]}
        self.assertTrue(ids.issuperset(
            {"baseline-low", "baseline-medium", "baseline-high", "baseline-critical"}))
        status, row = self._get("/v1/runtime-policies/baseline-high")
        self.assertEqual(200, status)
        self.assertEqual("baseline-high", row["policy_id"])

    def test_propose_lists_and_creates_deployment(self):
        status, proposed = self._post("/v1/policy-evolution/propose", "{}")
        self.assertEqual(201, status)
        self.assertEqual({"candidate_id", "hypothesis_id", "policy"}, set(proposed.keys()))
        policy = proposed["policy"]
        status, created = self._post("/v1/policy-deployments", io_join(policy))
        self.assertEqual(201, status)
        deployment_id = created["deployment_id"]
        self.assertEqual("DRAFT", created["state"])
        status, row = self._get(f"/v1/policy-deployments/{deployment_id}")
        self.assertEqual(200, status)
        self.assertEqual(deployment_id, row["deployment_id"])

    def test_deployment_lifecycle_actions(self):
        _, proposed = self._post("/v1/policy-evolution/propose", "{}")
        _, created = self._post("/v1/policy-deployments", io_join(proposed["policy"]))
        deployment_id = created["deployment_id"]
        expected = {
            "replay-pass": "REPLAY_PASSED", "shadow": "SHADOW",
            "canary": "CANARY", "promote": "PROMOTED",
        }
        for action, state in expected.items():
            status, body = self._post(
                f"/v1/policy-deployments/{deployment_id}/{action}", "{}")
            self.assertEqual(200, status, msg=action)
            self.assertEqual(state, body["state"], msg=action)
        # a fresh deployment can be rolled back.
        _, proposed2 = self._post("/v1/policy-evolution/propose", "{}")
        _, created2 = self._post("/v1/policy-deployments", io_join(proposed2["policy"]))
        did = created2["deployment_id"]
        for action in ("replay-pass", "shadow", "canary"):
            self._post(f"/v1/policy-deployments/{did}/{action}", "{}")
        status, body = self._post(f"/v1/policy-deployments/{did}/rollback", "{}")
        self.assertEqual(200, status)
        self.assertEqual("ROLLED_BACK", body["state"])

    def test_unknown_policy_returns_404(self):
        status, _ = self._get("/v1/runtime-policies/does-not-exist")
        self.assertEqual(404, status)


def io_join(policy):
    import json
    return json.dumps({"policy": policy})