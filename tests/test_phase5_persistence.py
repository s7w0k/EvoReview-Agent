"""Phase 5 acceptance tests: comprehensive persistence + restart recovery.

Covers (plan section 9):
  9.1  The storage/repositories package is importable and table-oriented.
  9.2  Runtime policy + overrides are persisted.
  9.3  Replay snapshots and runs are persisted.
  9.4  Procedures and deployments are persisted.
  9.5  Lineage, attributions and outcomes are persisted.
  9.6  Tool audit, failure, recovery, decision-trace and budget events persist.
  9.7  Restart recovery: a recreated store preserves every artifact.
"""
import os
import tempfile
import unittest

from evoagent.decision_trace.trace import TraceEvent
from evoagent.replay.models import ReplaySnapshot, ReplayRun
from evoagent.storage.json_store import JSONFileStore
from evoagent.storage.repositories.decision_trace import (
    PersistedDecisionTraceRepository,
)
from evoagent.storage.repositories.deployment import DeploymentRepository
from evoagent.storage.repositories.evolution_budget import EvolutionBudgetRepository
from evoagent.storage.repositories.failure import FailureRepository
from evoagent.storage.repositories.lineage import LineageRepository
from evoagent.storage.repositories.procedure import ProcedureRepository
from evoagent.storage.repositories.recovery import RecoveryRepository
from evoagent.storage.repositories.replay import ReplayRepository
from evoagent.storage.repositories.runtime_policy import (
    PersistedRuntimePolicyRepository,
)
from evoagent.storage.repositories.tool_audit import ToolAuditRepository


def make_store(path):
    return JSONFileStore(path)


class RepositoryPersistenceTest(unittest.TestCase):
    """9.1-9.6: each repository persists to its own table."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "state.json")
        self.store = make_store(self.path)

    def tearDown(self):
        for name in os.listdir(self.dir):
            os.unlink(os.path.join(self.dir, name))
        os.rmdir(self.dir)

    def test_runtime_policy_persists(self):
        repo = PersistedRuntimePolicyRepository(self.store)
        repo.save_policy("p-low", 3, {"max_steps": 8}, risk_level="low",
                         parent_version=2, status="CANARY")
        self.assertEqual("CANARY", repo.latest("p-low")["status"])
        self.assertEqual(3, repo.version("p-low", 3)["version"])

    def test_replay_snapshot_and_run_persist(self):
        repo = ReplayRepository(self.store)
        snapshot = ReplaySnapshot(task_id="t-1", repository="org/r")
        run = ReplayRun(snapshot_id=snapshot.snapshot_id, decision="PROMOTED")
        repo.save_snapshot(snapshot)
        repo.save_run(run)
        self.assertEqual("org/r", repo.snapshot(snapshot.snapshot_id)["repository"])
        self.assertEqual("PROMOTED", repo.run(run.run_id)["decision"])
        self.assertEqual(1, len(repo.runs_for_snapshot(snapshot.snapshot_id)))

    def test_gov_and_evolution_repos_persist(self):
        from evoagent.recovery.failures import FailureEvent, FailureType, RecoveryAction
        from evoagent.tools.audit import AuditEntry

        tool = ToolAuditRepository(self.store)
        tool.add(AuditEntry(
            task_id="t1", agent_id="a", tool_name="read_file", arguments_hash="h",
            authorized=True,
        ))
        self.assertEqual(1, len(tool.for_task("t1")))

        fail = FailureRepository(self.store)
        fail.add(FailureEvent(
            task_id="t1", failure_type=FailureType.MODEL_TIMEOUT,
            recoverable=True, recovery_action=RecoveryAction.RETRY,
        ))
        self.assertEqual(1, len(fail.of_type("MODEL_TIMEOUT")))

        rec = RecoveryRepository(self.store)
        rec.add("r-1", {"task_id": "t1", "recovery_action": "RETRY"})
        self.assertEqual(1, len(rec.by_action("RETRY")))

        dep = DeploymentRepository(self.store)
        dep.save_deployment("d-1", {"policy_id": "p", "status": "SHADOW"})
        self.assertEqual(1, len(dep.by_status("SHADOW")))

        proc = ProcedureRepository(self.store)
        proc.save_skill("coverage", {"name": "coverage"})
        proc.deploy("coverage", {"status": "ACTIVE"})
        self.assertEqual("coverage", proc.deployments()[0]["name"])

        lineage = LineageRepository(self.store)
        lineage.save_lineage("l-1", {"name": "evolve-1"})
        lineage.add_outcome("o-1", {"lineage_id": "l-1", "verdict": "keep"})
        self.assertEqual("keep", lineage.outcomes()[0]["verdict"])

        budget = EvolutionBudgetRepository(self.store)
        budget.add_usage("u-1", {"tenant_id": "t", "budget_name": "evolve",
                                 "cost": 1.5})
        self.assertEqual(1.5, budget.total_spent("evolve"))


class RestartRecoveryTest(unittest.TestCase):
    """9.7: destroy + recreate the store, verify every artifact survives."""

    def test_restart_preserves_all_artifacts(self):
        path = os.path.join(tempfile.mkdtemp(), "state.json")
        try:
            store = JSONFileStore(path)
            runtime = PersistedRuntimePolicyRepository(store)
            runtime.save_policy("p-low", 1, {"max_steps": 8}, risk_level="low")
            dep = DeploymentRepository(store)
            dep.save_deployment("d-1", {"policy_id": "p-low", "status": "CANARY"})
            budget = EvolutionBudgetRepository(store)
            budget.add_usage("u-1", {"tenant_id": "t", "budget_name": "evolve",
                                     "cost": 2.0})
            snap_repo = ReplayRepository(store)
            saved_snapshot = ReplaySnapshot(task_id="t-1", repository="org/r")
            snap_repo.save_snapshot(saved_snapshot)
            snap_repo.save_run(ReplayRun(
                snapshot_id=saved_snapshot.snapshot_id, decision="PROMOTED",
            ))
            trace = PersistedDecisionTraceRepository(store)
            trace.append("t-1", TraceEvent("e1", "policy_resolution"))
            trace.append("t-1", TraceEvent("e2", "task_started"))

            # Simulate a crashed + recreated service pointing at the same file.
            restarted = JSONFileStore(path)

            self.assertEqual(
                "low",
                PersistedRuntimePolicyRepository(restarted).latest("p-low")["risk_level"],
            )
            deployments = DeploymentRepository(restarted).by_status("CANARY")
            self.assertEqual("p-low", deployments[0]["policy_id"])
            self.assertEqual(2.0, EvolutionBudgetRepository(restarted).total_spent("evolve"))
            replay = ReplayRepository(restarted)
            persisted_snaps = replay.snapshots_for_task("t-1")
            self.assertEqual(1, len(persisted_snaps))
            snapshot_id = persisted_snaps[0]["snapshot_id"]
            runs = replay.runs_for_snapshot(snapshot_id)
            self.assertEqual(1, len(runs))
            self.assertEqual("PROMOTED", runs[0]["decision"])
            trace_events = PersistedDecisionTraceRepository(restarted).events("t-1")
            self.assertEqual(2, len(trace_events))
            self.assertEqual(["policy_resolution", "task_started"],
                             [e["action_type"] for e in trace_events])
        finally:
            shutil_rmtree(os.path.dirname(path))


def shutil_rmtree(path):
    import shutil

    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()