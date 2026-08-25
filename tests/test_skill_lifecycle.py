"""Work Package 2: compatible Skill artifact lifecycle.

Covers: old-schema in-place upgrade, idempotent migration, status/active
consistency, historical rollback, rejected/archived gating, legacy call
signatures, provenance fields, and the dark switch.
"""
import json
import os
import sqlite3
import tempfile
import unittest

from evoagent import skill_lifecycle
from evoagent.store import TaskStore


def artifact(name="evolved-review"):
    return {"name": name, "rules": []}


class SkillLifecycleTests(unittest.TestCase):
    def setUp(self):
        skill_lifecycle.set_enabled(True)
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.store = TaskStore(self.path)

    def tearDown(self):
        skill_lifecycle.set_enabled(True)
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_legacy_save_signature_still_works(self):
        # Old 4-positive-arg call with no new parameters.
        value = self.store.save_skill_artifact("evolved-review", artifact(), 1.0, True)
        self.assertEqual("active", value["status"])
        active = self.store.get_active_skill_artifact("evolved-review")
        self.assertEqual(1, active["version"])
        self.assertEqual("agent-created", active["origin"])
        self.assertEqual({}, active["provenance"])

    def test_old_schema_db_upgrades_and_keeps_active_skill(self):
        # Create a fresh database containing ONLY the OLD skill_artifact_versions
        # schema (no lifecycle columns) and one active=1 row, then open TaskStore.
        handle, old_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        try:
            conn = sqlite3.connect(old_path)
            try:
                conn.execute(
                    """CREATE TABLE skill_artifact_versions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        tenant_id TEXT NOT NULL DEFAULT 'default',
                        skill_name TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        artifact_json TEXT NOT NULL,
                        artifact_sha256 TEXT NOT NULL,
                        score REAL NOT NULL,
                        active INTEGER NOT NULL DEFAULT 0,
                        parent_version INTEGER,
                        created_at TEXT NOT NULL,
                        UNIQUE(tenant_id, skill_name, version)
                    )"""
                )
                conn.execute(
                    "INSERT INTO skill_artifact_versions(tenant_id,skill_name,version,"
                    "artifact_json,artifact_sha256,score,active,parent_version,created_at) "
                    "VALUES ('default','evolved-review',3,'{\"name\":\"evolved-review\"}',"
                    "'%s',0.9,1,2,'2025-01-01T00:00:00+00:00')" % ("a" * 64)
                )
                conn.commit()
            finally:
                conn.close()

            store = TaskStore(old_path)
            # The legacy active=1 version must still be loadable after upgrade.
            active = store.get_active_skill_artifact("evolved-review")
            self.assertIsNotNone(active)
            self.assertEqual(3, active["version"])
            self.assertEqual("active", active["status"])
            # A newly saved non-active version must not replace the running one.
            store.save_skill_artifact("evolved-review", artifact(), 0.5, False)
            self.assertEqual(3, store.get_active_skill_artifact("evolved-review")["version"])
        finally:
            if os.path.exists(old_path):
                os.unlink(old_path)

    def test_repeated_startup_is_idempotent(self):
        self.store.save_skill_artifact("evolved-review", artifact(), 1.0, True)
        before = self.store.list_skill_artifact_versions("evolved-review")
        # Re-open the same database (simulates a second startup).
        store2 = TaskStore(self.path)
        after = store2.list_skill_artifact_versions("evolved-review")
        self.assertEqual(
            [(v["version"], v["status"], v["active"]) for v in before],
            [(v["version"], v["status"], v["active"]) for v in after],
        )

    def test_consistency_diagnostics_are_clean_and_detect_drift(self):
        self.store.save_skill_artifact("evolved-review", artifact(), 1.0, True)
        self.store.save_skill_artifact("evolved-review", artifact(), 0.5, False)
        self.assertEqual([], self.store.check_skill_artifact_consistency())
        # Force status='active' on the non-active version and confirm it is
        # reported as an inconsistency, not silently repaired.
        with self.store._lock, self.store._connection() as conn:
            conn.execute(
                "UPDATE skill_artifact_versions SET status='active' WHERE version=2"
            )
        version_two = [
            item for item in self.store.check_skill_artifact_consistency()
            if item["version"] == 2
        ]
        self.assertTrue(any(item["type"] == "inactive_as_active" for item in version_two))

    def test_historical_validated_version_can_be_reactivated(self):
        self.store.save_skill_artifact("evolved-review", artifact(), 1.0, True)
        self.store.save_skill_artifact("evolved-review", artifact(), 0.9, True)
        versions = {
            v["version"]: v
            for v in self.store.list_skill_artifact_versions("evolved-review")
        }
        # v1 was replaced -> deactivated and moved back to validated.
        self.assertEqual("validated", versions[1]["status"])
        self.assertFalse(versions[1]["active"])
        # A validated historical version can be rolled back (reactivated).
        self.assertTrue(self.store.activate_skill_artifact("evolved-review", 1))
        self.assertEqual(1, self.store.get_active_skill_artifact("evolved-review")["version"])

    def test_rejected_and_archived_versions_cannot_be_activated(self):
        self.store.save_skill_artifact(
            "evolved-review", artifact(), 1.0, True,
        )
        rejected = self.store.save_skill_artifact(
            "evolved-review", artifact(), 0.5, False,
            status=skill_lifecycle.REJECTED,
        )
        self.assertFalse(self.store.activate_skill_artifact(
            "evolved-review", rejected["version"],
        ))
        # Archive a non-active version, then confirm it cannot activate.
        self.assertTrue(self.store.transition_skill_artifact(
            "default", "evolved-review", rejected["version"],
            skill_lifecycle.ARCHIVED, "op", "cleanup",
        ))
        self.assertFalse(self.store.activate_skill_artifact(
            "evolved-review", rejected["version"],
        ))

    def test_active_version_cannot_be_archived_directly(self):
        self.store.save_skill_artifact("evolved-review", artifact(), 1.0, True)
        self.assertFalse(self.store.transition_skill_artifact(
            "default", "evolved-review", 1, skill_lifecycle.ARCHIVED, "op", "retire",
        ))
        self.assertEqual([], self.store.check_skill_artifact_consistency())

    def test_dark_switch_disables_lifecycle_gating(self):
        self.store.save_skill_artifact("evolved-review", artifact(), 1.0, True)
        validated = self.store.save_skill_artifact(
            "evolved-review", artifact(), 0.5, False,  # no evolution run recorded
        )
        self.assertEqual(skill_lifecycle.VALIDATED, validated["status"])
        # Enabled: a validated version without a run is activatable.
        skill_lifecycle.set_enabled(True)
        self.assertTrue(self.store.activate_skill_artifact(
            "evolved-review", validated["version"],
        ))
        # Dark switch off: legacy activation requires an activated run, so a
        # plain validated version cannot be activated.
        self.store.activate_skill_artifact("evolved-review", 1)  # make v1 active again
        skill_lifecycle.set_enabled(False)
        self.assertFalse(self.store.activate_skill_artifact(
            "evolved-review", validated["version"],
        ))
        skill_lifecycle.set_enabled(True)

    def test_provenance_and_new_fields_survive_round_trip(self):
        provenance = {
            "origin": "agent-created",
            "source_task_ids": ["t1"], "source_case_ids": ["c1"],
            "source_experience_ids": [],
            "generator": {"type": "feedback-rule-builder", "version": "1"},
            "dataset": {"validation_sha256": "v", "holdout_sha256": "h"},
            "runtime_version": "0.3",
        }
        saved = self.store.save_skill_artifact(
            "evolved-review", artifact(), 1.0, True, "tenant-a",
            status="active", origin="agent-created", provenance=provenance,
            repository_scope="org/repo", patch={"add": "x.py"},
        )
        self.assertEqual("0.3", saved["provenance"]["runtime_version"])
        listed = self.store.list_skill_artifact_versions("evolved-review", "tenant-a")[0]
        for key in ("version", "score", "active", "artifact", "artifact_sha256",
                    "created_at", "parent_version"):
            self.assertIn(key, listed)
        self.assertEqual("active", listed["status"])
        self.assertEqual("agent-created", listed["origin"])
        self.assertEqual("org/repo", listed["repository_scope"])
        self.assertEqual("0.3", listed["provenance"]["runtime_version"])
        self.assertEqual("t1", listed["provenance"]["source_task_ids"][0])


if __name__ == "__main__":
    unittest.main()