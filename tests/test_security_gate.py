"""Closed-loop WP8: safety, permission and dangerous-evolution interception."""
import hashlib
import json
import os
import tempfile
import unittest

from evoagent import evolution_policy as policy
from evoagent import security_gate as gate
from evoagent.config import Settings


def _artifact(**overrides):
    artifact = {
        "name": "evolved-review",
        "permissions": [],
        "rules": [{
            "rule_id": "SEC-X", "severity": "high", "match": "dangerous_call(data)",
            "title": "Dangerous call", "explanation": "Unsafe API added.",
            "fix": "Use safe_call.", "test": "Add regression test.",
        }],
    }
    artifact.update(overrides)
    return artifact


class DangerousArtifactTests(unittest.TestCase):
    def test_empty_permissions_ok(self):
        self.assertEqual([], gate.dangerous_artifact_reasons(_artifact()))

    def test_non_empty_permissions_rejected(self):
        reasons = gate.dangerous_artifact_reasons(_artifact(permissions=["network"]))
        self.assertTrue(any("permissions" in r for r in reasons))

    def test_forbidden_construct_rejected(self):
        artifact = _artifact()
        artifact["rules"][0]["explanation"] = "use eval(user_input) here"
        reasons = gate.dangerous_artifact_reasons(artifact)
        self.assertTrue(any("eval(" in r for r in reasons))


class ArtifactIntegrityTests(unittest.TestCase):
    def test_hash_mismatch(self):
        artifact = _artifact()
        reasons = gate.check_artifact_integrity(artifact, "deadbeef")
        self.assertIn("artifact hash mismatch", reasons)

    def test_missing_provenance_origin(self):
        artifact = _artifact()
        canonical = json.dumps(artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        sha = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        reasons = gate.check_artifact_integrity(artifact, sha, provenance={})
        self.assertIn("missing provenance origin", reasons)

    def test_valid_integrity_passes(self):
        artifact = _artifact()
        canonical = json.dumps(artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        sha = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        reasons = gate.check_artifact_integrity(
            artifact, sha, parent_version=1,
            provenance={"origin": "agent-created", "runtime_version": "0.3"},
            runtime_version="0.3",
        )
        self.assertEqual([], reasons)


class PermissionReviewTests(unittest.TestCase):
    def test_manual_code_review_for_new_permissions(self):
        self.assertTrue(gate.requires_manual_code_review("tool_proposal", ["network"]))
        self.assertFalse(gate.requires_manual_code_review("rule_add", ["network"]))

    def test_sandbox_adequate(self):
        self.assertTrue(gate.sandbox_adequate({
            "isolation": "container", "network": False, "filesystem": "read-only",
        }))
        self.assertFalse(gate.sandbox_adequate({"isolation": "python-i"}))
        self.assertFalse(gate.sandbox_adequate({
            "isolation": "container", "network": True, "filesystem": "read-only",
        }))


class DualApprovalTests(unittest.TestCase):
    def test_requires_second_approver(self):
        self.assertTrue(policy.requires_second_approver("u1", "u1", risk_level="high"))
        self.assertFalse(policy.requires_second_approver("u1", "u2", risk_level="high"))
        self.assertFalse(policy.requires_second_approver("u1", "u1", risk_level="low"))


class ProductionProfileValidationTests(unittest.TestCase):
    def _settings(self, **kwargs):
        values = {
            "host": "127.0.0.1", "port": 8080, "db_path": "tmp.db",
            "max_diff_bytes": 10000, "max_steps": 8, "timeout_seconds": 10,
            "llm_base_url": "", "llm_api_key": "", "llm_model": "",
            "github_webhook_secret": "", "github_token": "", "auto_post_review": False,
            "skills_dir": "skills",
        }
        values.update(kwargs)
        return Settings(**values)

    def test_production_profile_requires_safeguards(self):
        with self.assertRaises(ValueError):
            self._settings(evolution_production_profile=True).validate_production_profile()

    def test_valid_production_profile_passes(self):
        s = self._settings(
            evolution_production_profile=True, auth_required=True,
            auth_secret="x" * 32, eval_source="github-real", eval_min_holdout_cases=1,
            bootstrap_admin_username="admin",
            evolution_approval_policy="always",
        )
        s.validate_production_profile()  # must not raise


if __name__ == "__main__":
    unittest.main()
