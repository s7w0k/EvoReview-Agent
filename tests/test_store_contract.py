"""Store contract tests.

Verify that the SQLite and PostgreSQL backends expose the same public API surface
and return consistent records.  The contract runs in full against SQLite; when a
PostgreSQL DSN is available via EVOAGENT_DATABASE_URL (starts with postgres), the
same suite runs against PostgresTaskStore too.
"""
import os
import tempfile
import unittest

from evoagent.store import TaskStore, utc_now
from evoagent.models import ReviewReport, TaskState, TraceEvent


def _make_sqlite_store():
    handle, path = tempfile.mkstemp(suffix=".db")
    os.close(handle)
    return TaskStore(path), path


def _make_postgres_store():
    from evoagent.postgres_store import PostgresTaskStore
    return PostgresTaskStore(os.environ["EVOAGENT_DATABASE_URL"]), None


def _report():
    return ReviewReport(
        repository="contract/repo", pull_request=9, summary="s", risk="low",
    )


def _event():
    return TraceEvent(1, TaskState.SUCCESS, "done", utc_now())


def _run_contract(self, store):
    # create/get
    store.create("ct-1", "contract/repo", 9, {"source": "contract"})
    task = store.get("ct-1")
    self.assertEqual("PENDING", task["state"])
    self.assertEqual("contract/repo", task["repository"])

    # trace/report
    store.succeed("ct-1", _report(), _event())
    self.assertEqual("SUCCESS", store.get("ct-1")["state"])
    self.assertEqual("low", store.get("ct-1")["report"]["risk"])

    # failure cases
    store.record_failure_case("ct-1", "missed_issue", {"note": "x"})
    cases = store.list_failure_cases(True, 10)
    self.assertTrue(any(c["task_id"] == "ct-1" for c in cases))

    # skill versions
    store.save_skill_version("ct-skill", "prompt", 0.5, activate=True)
    active = store.get_active_skill_version("ct-skill")
    self.assertEqual(1, active["version"])
    self.assertTrue(store.activate_skill_version("ct-skill", 1))

    # skill artifacts
    artifact = {"name": "evolved-review", "rules": []}
    store.save_skill_artifact("ct-skill", artifact, 0.5, activate=True, tenant_id="t1")
    got = store.get_active_skill_artifact("ct-skill", "t1")
    self.assertEqual("evolved-review", got["artifact"]["name"])
    self.assertTrue(store.activate_skill_artifact("ct-skill", 1, "t1"))

    # WP2 lifecycle: provenance round-trip, transition, tenant-wide listing.
    saved = store.save_skill_artifact(
        "ct-skill", artifact, 0.6, True, "t1",
        status="active", origin="agent-created",
        provenance={"origin": "agent-created", "runtime_version": "0.3"},
    )
    self.assertEqual("0.3", saved["provenance"]["runtime_version"])
    self.assertEqual("agent-created", saved["origin"])
    candidate = store.save_skill_artifact(
        "ct-skill", artifact, 0.4, False, "t1", status="validated",
    )
    self.assertTrue(store.transition_skill_artifact(
        "t1", "ct-skill", candidate["version"], "archived", "op", "test",
    ))
    self.assertEqual([], store.check_skill_artifact_consistency())
    tenant_versions = store.list_skill_artifact_versions_for_tenant("t1")
    self.assertTrue(any(v["skill_name"] == "ct-skill" for v in tenant_versions))

    # WP3 experience records: dedup, corroboration and consumption.
    fingerprint = "fp-contract"
    first = store.record_experience(
        "t1", "contract/repo", "ct-1", "feedback", "missed_issue",
        "rule_candidate", fingerprint, {"finding": {"rule_id": "SEC-X"}},
        "dangerous_call", 0.9, "observed",
    )
    self.assertTrue(first["inserted"])
    self.assertEqual("repository-local", first["scope"])
    self.assertFalse(store.record_experience(
        "t1", "contract/repo", "ct-1", "feedback", "missed_issue",
        "rule_candidate", fingerprint, {"finding": {"rule_id": "SEC-X"}},
        "dangerous_call", 0.9, "observed",
    )["inserted"])
    self.assertEqual(1, store.promote_experience_scope("t1", fingerprint, "tenant-shared"))
    store.corroborate_experiences("t1", fingerprint)
    self.assertEqual("corroborated", store.list_experiences("t1")[0]["status"])
    self.assertEqual("tenant-shared", store.list_experiences("t1")[0]["scope"])
    candidates = store.list_corroborated_rule_candidates("t1")
    self.assertTrue(any(e["fingerprint"] == fingerprint for e in candidates))
    store.mark_experience_consumed([first["id"]], "run-contract")
    self.assertEqual("consumed", store.list_experiences_by_ids([first["id"]])[0]["status"])

    # WP4 usage metrics accumulate per skill@version.
    store.record_skill_usage("t1", "ct-skill", 1, executions=2, findings_proposed=3)
    store.record_skill_usage("t1", "ct-skill", 1, executions=1, findings_proposed=1)
    usage = store.get_skill_usage_stats("t1", "ct-skill", 1)
    self.assertEqual(3, usage["executions"])
    self.assertEqual(4, usage["findings_proposed"])
    self.assertTrue(any(
        u["skill_name"] == "ct-skill" for u in store.list_skill_usage_stats("t1")
    ))

    # audit + memory
    store.audit("t1", "actor", "contract.run", "ct-1")
    self.assertTrue(any(
        e["action"] == "contract.run" for e in store.list_audit("t1")
    ))
    store.save_agent_memory({
        "id": "mem-1", "tenant_id": "t1", "repository": "contract/repo",
        "task_id": "ct-1", "agent": "a", "scope": "semantic", "kind": "note",
        "content": "experience", "keywords": ["exp"], "metadata": {},
        "importance": 0.5, "created_at": utc_now(), "expires_at": None,
    })
    memories = store.list_agent_memories("t1", "contract/repo", ("semantic",))
    self.assertTrue(any(m["id"] == "mem-1" for m in memories))

    # checkpoints + payload
    store.save_checkpoint("ct-1", "planning", {"ok": True}, "completed", 1)
    cp = store.load_checkpoints("ct-1")
    self.assertEqual("completed", cp["planning"]["status"])
    store.save_task_payload("ct-1", "diff-here")
    self.assertEqual("diff-here", store.get_task_payload("ct-1"))

    # dashboard stats must include the active artifact
    stats = store.dashboard_stats("t1")
    self.assertIn("active_skill_versions", stats)

    # WP8: evaluation cases carry source + category and honor the source filter.
    eval_diff = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+eval(x)\n"
    store.save_evaluation_case(
        "ct-eval", "validation", eval_diff,
        [{"path": "a.py", "line": 1, "rule_id": "SEC-EVAL", "min_severity": "high"}],
        "github-real", True, "risk",
    )
    github_listed = store.list_evaluation_cases("validation", True, 10, "github-real")
    self.assertTrue(any(
        c["name"] == "ct-eval" and c["category"] == "risk" for c in github_listed
    ))
    builtin_listed = store.list_evaluation_cases("validation", True, 10, "builtin")
    self.assertFalse(any(c["name"] == "ct-eval" for c in builtin_listed))

    # WP3: dataset-layering metadata round-trips on evaluation cases.
    store.save_evaluation_case(
        "ct-layered", "validation", eval_diff,
        [{"path": "a.py", "line": 1, "rule_id": "SEC-EVAL", "min_severity": "high"}],
        "github-real", True, "risk",
        suite_id="real-validation", dataset_version="v1", repository="contract/repo",
        language="python", source_uri="https://github.com/contract/repo/pull/9",
        labeler_ids=["l1", "l2"], label_schema_version="1.0",
        created_before_candidate=True,
    )
    layered = next(
        c for c in store.list_evaluation_cases("validation", True, 10, "github-real")
        if c["name"] == "ct-layered"
    )
    self.assertEqual("real-validation", layered["suite_id"])
    self.assertEqual("v1", layered["dataset_version"])
    self.assertEqual("python", layered["language"])
    self.assertEqual(["l1", "l2"], layered["labeler_ids"])
    self.assertTrue(layered["created_before_candidate"])

    # WP9: holdout rotation archives the oldest row and keeps the audit copy.
    store.save_evaluation_case("ct-h1", "holdout", eval_diff, [], "test")
    holdout_active = store.list_evaluation_cases("holdout", True, 10)
    h1 = next(c for c in holdout_active if c["name"] == "ct-h1")
    self.assertEqual([h1["id"]], store.archive_oldest_holdout_cases(1))
    self.assertFalse(any(
        c["id"] == h1["id"] for c in store.list_evaluation_cases("holdout", True, 10)
    ))
    self.assertTrue(any(
        c["id"] == h1["id"] for c in store.list_evaluation_cases("holdout", False, 10)
    ))

    # WP10: readiness probe and skill evolution run round-trip new keys.
    self.assertTrue(store.ping())
    store.save_skill_evolution_run({
        "id": "run-pg", "tenant_id": "t1", "skill_name": "ct-skill",
        "candidate_version": 1, "baseline_version": None, "decision": "deferred",
        "candidate_score": 0.0, "baseline_score": 0.0,
        "metrics": {
            "reproducibility": {"dataset_source": "builtin", "dataset_sha256": "abc"},
            "candidate": {"per_finding_cost_estimate": None},
        },
        "created_at": utc_now(),
    })
    runs = store.list_skill_evolution_runs(50)
    self.assertTrue(any(
        r["id"] == "run-pg"
        and r["metrics"]["reproducibility"]["dataset_source"] == "builtin"
        for r in runs
    ))

    # WP4 shadow deployments: save/observe/promote parity between backends.
    store.save_deployment("t1", "ct-skill", {
        "stable_version": 1, "candidate_version": 2,
        "auto_promote": True, "min_samples": 2,
        "max_disagreement_rate": 0.5, "max_error_rate": 0.5,
    })
    dep = store.get_deployment("t1", "ct-skill")
    self.assertTrue(dep["auto_promote"])
    store.record_shadow_observation(
        "t1", "ct-skill", "ct-1", "stable",
        {"finding": "x"}, {"finding": "y"}, 0.0,
    )
    store.record_shadow_observation(
        "t1", "ct-skill", "ct-1", "stable",
        {"finding": "x"}, {"finding": "x"}, 0.0,
    )
    self.assertEqual("promoted", store.get_deployment("t1", "ct-skill")["status"])
    observations = store.list_release_observations("t1", "ct-skill")
    self.assertEqual(2, len(observations))
    self.assertEqual("x", observations[0]["primary"]["finding"])

    # Work Package 1: report-chat store parity.
    session = store.create_chat_session(
        "t1", "ct-1", "contract/repo", "分析本期风险", "u-1", "fp-1",
    )
    self.assertEqual("active", session["status"])
    self.assertEqual("fp-1", session["report_fingerprint"])
    # tenant isolation on session reads.
    self.assertEqual({}, store.get_chat_session(session["id"], "t-other"))
    self.assertEqual(1, len(store.list_task_chat_sessions("ct-1", "t1")))

    # message JSON round-trip and client_request_id idempotency.
    msg1 = store.append_chat_message(
        "t1", session["id"], "user", "为什么 SEC-EVAL 是高风险？",
        [{"type": "finding", "ref": "finding:0"}], client_request_id="req-1",
    )
    msg1_again = store.append_chat_message(
        "t1", session["id"], "user", "重复请求", [], client_request_id="req-1",
    )
    self.assertEqual(msg1["id"], msg1_again["id"])
    self.assertEqual("finding", msg1["citations"][0]["type"])
    # A second, distinct request appends a new row (ordering is preserved).
    store.append_chat_message(
        "t1", session["id"], "assistant", "占位", [], client_request_id="req-2",
    )
    completed = store.complete_chat_message(
        msg1["id"], "t1", "因为它是代码执行。",
        [{"type": "diff", "path": "a.py", "line": 1}], provider="mock", model="m",
    )
    self.assertEqual("completed", completed["status"])
    self.assertEqual("diff", completed["citations"][0]["type"])
    self.assertEqual(2, len(store.list_chat_messages(session["id"], "t1")))
    failed = store.fail_chat_message(msg1["id"], "t1", "model timeout")
    self.assertEqual("failed", failed["status"])
    self.assertEqual("model timeout", failed["error"])

    # context snapshot round-trip.
    snapshot = store.save_chat_context_snapshot(
        "t1", session["id"], msg1["id"], "fp-1", "ctx-1",
        [{"type": "finding", "ref": "finding:0"}], {"truncated": 2},
    )
    self.assertEqual("ctx-1", store.get_chat_context_snapshot(msg1["id"], "t1")["context_fingerprint"])
    self.assertEqual(2, snapshot["truncation"]["truncated"])

    # insight lifecycle: draft -> confirmed, tenant-scoped reads.
    insight = store.create_chat_insight(
        "t1", session["id"], msg1["id"], "ct-1", "false_positive",
        {"rule_id": "SEC-EVAL"}, "该规则不适用", 0.8, {"valid": True},
    )
    self.assertEqual("draft", insight["status"])
    self.assertEqual("SEC-EVAL", insight["finding"]["rule_id"])
    self.assertEqual(1, len(store.list_chat_insights(session["id"], "t1")))
    confirmed = store.update_chat_insight_status(
        insight["id"], "t1", "confirmed", confirmed_by="u-1", feedback_case_id=7,
    )
    self.assertEqual("confirmed", confirmed["status"])
    self.assertEqual(7, confirmed["feedback_case_id"])
    self.assertEqual({}, store.get_chat_insight(insight["id"], "t-other"))

    # feedback idempotency via source_key: re-record returns the same case ID.
    first = store.record_failure_case("ct-1", "false_positive", {"note": "x"}, "chat_insight:%s" % insight["id"])
    second = store.record_failure_case("ct-1", "false_positive", {"note": "y"}, "chat_insight:%s" % insight["id"])
    self.assertEqual(first, second)
    cases = store.list_task_failure_cases("ct-1")
    self.assertEqual(1, sum(1 for c in cases if c.get("source_key")))

    # Closed-loop WP1: durable evolution jobs parity across backends.
    job = store.create_evolution_job(
        "job-ct", "t1", None, "rule_skill", "evolved-review",
        "manual", "", "job-key-ct", {"max_retries": 3}, max_retries=3,
    )
    self.assertEqual("pending", job["status"])
    self.assertEqual("collecting", job["current_step"])
    # Unique idempotency key prevents a duplicate insert (returns None).
    self.assertIsNone(store.create_evolution_job(
        "job-ct-dup", "t1", None, "rule_skill", "evolved-review",
        "manual", "", "job-key-ct", {"max_retries": 3}, max_retries=3,
    ))
    # Active-job lookup and tenant isolation.
    active = store.find_active_evolution_job("t1", "job-key-ct")
    self.assertEqual("job-ct", active["id"])
    self.assertIsNone(store.find_active_evolution_job("t-other", "job-key-ct"))
    # Lease acquisition is atomic and single-winner.
    self.assertTrue(store.acquire_evolution_job_lease(
        "job-ct", "t1", "worker-a", "2099-01-01T00:00:00+00:00"))
    self.assertFalse(store.acquire_evolution_job_lease(
        "job-ct", "t1", "worker-b", "2099-01-01T00:00:00+00:00"))
    # Checkpoint + completion round-trip.
    store.update_evolution_job_checkpoint(
        "job-ct", "t1", "done", {"step": "done", "decision": "deferred"})
    store.update_evolution_job(
        "job-ct", "t1", status="completed", current_step="done",
        candidate_version=2, evolution_run_id="run-ct", error=None)
    completed = store.get_evolution_job("job-ct", "t1")
    self.assertEqual("completed", completed["status"])
    self.assertEqual(2, completed["candidate_version"])
    self.assertEqual("deferred", completed["checkpoint"]["decision"])
    self.assertEqual(1, len(store.list_evolution_jobs("t1")))
    self.assertEqual({}, store.get_evolution_job("job-ct", "t-other") or {})

    # Closed-loop WP2: structured Hypothesis persistence parity.
    experience_id = store.list_experiences("t1")[0]["id"]
    hypothesis = {
        "id": "hyp-ct", "job_id": "job-ct", "tenant_id": "t1",
        "repository_scope": "contract/repo", "problem_type": "SEC-X",
        "failure_signature": "dangerous_call", "root_cause": "rule missed defect",
        "change_type": "rule_add", "expected_effect": {"expected": "fewer missed issues"},
        "affected_domains": ["security"], "risk_level": "high",
        "permissions": [], "evaluation_requirements": {"expected": "metric"},
        "rationale": "structural", "evidence_ids": [experience_id],
        "status": "draft", "provenance": {
            "source_experience_ids": [experience_id],
            "source_case_ids": [], "source_task_ids": ["ct-1"], "manual_confirmed": False,
        },
    }
    saved_hyp = store.create_hypothesis(hypothesis)
    self.assertEqual("draft", saved_hyp["status"])
    self.assertEqual("security", saved_hyp["affected_domains"][0])
    self.assertEqual([experience_id], saved_hyp["evidence_ids"])
    self.assertEqual("draft", store.get_hypothesis("hyp-ct", "t1")["status"])
    self.assertIsNone(store.get_hypothesis("hyp-ct", "t-other"))
    approved = store.update_hypothesis(
        "hyp-ct", "t1", status="approved", reviewed_by="u-1")
    self.assertEqual("approved", approved["status"])
    self.assertEqual("u-1", approved["reviewed_by"])
    self.assertEqual(1, len(store.list_hypotheses("t1")))

    # Closed-loop WP5: gate results and richer shadow observation parity.
    saved_gate = store.save_gate_result({
        "tenant_id": "t1", "job_id": "job-ct", "candidate_kind": "rule_skill",
        "candidate_name": "ct-skill", "candidate_version": 2, "stage": "shadow",
        "gate_name": "shadow_to_canary", "passed": True,
        "threshold": {"min_samples": 100}, "evidence": {"samples": 120},
    })
    self.assertTrue(saved_gate["passed"])
    self.assertEqual({"min_samples": 100}, saved_gate["threshold"])
    self.assertEqual(1, len(store.list_gate_results("t1", stage="shadow")))

    store.record_shadow_observation(
        "t1", "ct-skill", "ct-1", "stable", {"finding": "x"}, {"finding": "y"},
        1.0, candidate_version=2, latency_ms=12.5, cost_estimate=0.001,
        metrics={"high_risk_missed": 0},
    )
    obs = store.list_release_observations("t1", "ct-skill")[0]
    self.assertEqual(2, obs["candidate_version"])
    self.assertEqual(12.5, obs["latency_ms"])
    self.assertEqual(0, obs["metrics"]["high_risk_missed"])
    self.assertTrue(store.backfill_release_observation(
        "t1", "ct-skill", "ct-1", "good", "accepted", True))
    obs = store.list_release_observations("t1", "ct-skill")[0]
    self.assertEqual("accepted", obs["feedback_category"])
    self.assertTrue(obs["accepted"])


class ContractTestsMixin:
    def test_contract(self):
        _run_contract(self, self.store)


class SqliteContractTests(ContractTestsMixin, unittest.TestCase):
    def setUp(self):
        self.store, self.path = _make_sqlite_store()

    def tearDown(self):
        if self.path and os.path.exists(self.path):
            os.unlink(self.path)


@unittest.skipUnless(
    os.environ.get("EVOAGENT_DATABASE_URL", "").startswith("postgres"),
    "EVOAGENT_DATABASE_URL must point to a PostgreSQL instance",
)
class PostgresContractTests(ContractTestsMixin, unittest.TestCase):
    def setUp(self):
        self.store, self.path = _make_postgres_store()
        # The contract must be repeatable on a shared database: start from a
        # clean public schema so re-runs never hit primary-key collisions.
        conn = self.store._connect()
        try:
            rows = conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public'"
            ).fetchall()
            tables = [row["table_name"] for row in rows]
            if tables:
                conn.execute(
                    "TRUNCATE %s RESTART IDENTITY CASCADE" % ", ".join(tables)
                )
            conn.commit()
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()