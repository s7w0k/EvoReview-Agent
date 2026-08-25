"""Phase 4 acceptance tests: automatic DecisionTrace + ReplaySnapshot.

Covers (plan section 8):
  8.1  Every real task auto-creates a DecisionTrace with recorded events.
  8.2  DecisionTraceRepository persists traces (not a bare dict).
  8.3  ReplaySnapshotBuilder assembles a snapshot from the run inputs.
  8.4  Replay observations are consumed in ordered, occurrence-aware fashion.
  8.5  Replay Level L1 / L2 / L3 is defined and routed.
"""
import os
import tempfile
import unittest

from evoagent.decision_trace.repository import DecisionTraceRepository
from evoagent.execution.context import ReviewExecutionContext
from evoagent.harness import ReviewHarness
from evoagent.replay.builder import ReplaySnapshotBuilder
from evoagent.replay.models import (
    ReplayLevel,
    ReplayObservationIndex,
    ReplaySnapshot,
    fingerprint,
)
from evoagent.replay.recorder import ReplayToolRegistry
from evoagent.replay.repository import ReplayRepository
from evoagent.reviewer import LocalRuleReviewer
from evoagent.runtime import AgentLoopProtocolError
from evoagent.store import TaskStore


SIMPLE_DIFF = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+eval(value)\n"


class DecisionTraceRepositoryTest(unittest.TestCase):
    """8.2: traces are persisted via the repository, not a bare dict."""

    def test_append_and_query(self):
        repo = DecisionTraceRepository()
        trace = repo.begin("t-1")
        from evoagent.decision_trace.trace import TraceEvent
        repo.append("t-1", TraceEvent("e1", "policy_resolution", policy_id="p"))
        repo.append("t-1", TraceEvent("e2", "task_started"))
        self.assertEqual(2, len(repo.events("t-1")))
        self.assertEqual(["t-1"], repo.task_ids())
        self.assertIs(trace, repo.trace("t-1"))


class ReplayObservationIndexTest(unittest.TestCase):
    """8.4: repeated tool+args calls are consumed in order, exactly once."""

    def test_ordered_occurrence_consumption(self):
        args = {"path": "a.py"}
        key = fingerprint("read_file", args)
        index = ReplayObservationIndex([
            {"fingerprint": key, "observation": "FIRST"},
            {"fingerprint": key, "observation": "SECOND"},
        ])
        self.assertEqual("FIRST", index.take("read_file", args))
        self.assertEqual("SECOND", index.take("read_file", args))
        self.assertIsNone(index.take("read_file", args))

    def test_registry_replays_duplicate_calls_in_order(self):
        args = {"path": "a.py"}
        key = fingerprint("read_file", args)
        snapshot = ReplaySnapshot(tool_observations=[
            {"fingerprint": key, "observation": "A"},
            {"fingerprint": key, "observation": "B"},
        ])
        registry = ReplayToolRegistry(snapshot)
        self.assertEqual("A", registry.invoke("read_file", args))
        self.assertEqual("B", registry.invoke("read_file", args))
        with self.assertRaises(AgentLoopProtocolError):
            registry.invoke("read_file", args)

    def test_registry_index_is_per_instance(self):
        args = {"path": "a.py"}
        key = fingerprint("read_file", args)
        snapshot = ReplaySnapshot(tool_observations=[
            {"fingerprint": key, "observation": "A"},
        ])
        first = ReplayToolRegistry(snapshot)
        second = ReplayToolRegistry(snapshot)
        self.assertEqual("A", first.invoke("read_file", args))
        # A fresh registry instance still has its own copy.
        self.assertEqual("A", second.invoke("read_file", args))


class ReplayLevelTest(unittest.TestCase):
    """8.5: the three replay levels are defined."""

    def test_level_enum(self):
        self.assertEqual(
            "L1_TOOL", ReplayLevel.L1_TOOL.value,
        )
        self.assertEqual(
            "L2_TOOL_AND_MODEL", ReplayLevel.L2_TOOL_AND_MODEL.value,
        )
        self.assertEqual(
            "L3_LIVE_COUNTERFACTUAL", ReplayLevel.L3_LIVE_COUNTERFACTUAL.value,
        )

    def test_snapshot_stores_level_and_registry_honours_it(self):
        snapshot = ReplaySnapshot(
            replay_level=ReplayLevel.L2_TOOL_AND_MODEL.value,
            tool_observations=[
                {"fingerprint": fingerprint("read_file", {"path": "a"}),
                 "observation": "DATA"},
            ],
        )
        registry = ReplayToolRegistry(snapshot)
        self.assertEqual(ReplayLevel.L2_TOOL_AND_MODEL, registry.replay_level)


class ReplaySnapshotBuilderTest(unittest.TestCase):
    """8.3: a snapshot can be assembled from context + trace + audit + input."""

    def test_build_from_execution_context_and_task_input(self):
        context = ReviewExecutionContext(
            task_id="t-9", tenant_id="tenant-a", repository="org/repo",
            model_name="gpt-x", prompt_version="pv-1",
            runtime_policy_version=7,
        )
        builder = ReplaySnapshotBuilder()
        snapshot = builder.build(
            execution_context=context, task_id="t-9",
            repository="org/repo", diff=SIMPLE_DIFF,
            expected_output={"risk": "high"},
            replay_level=ReplayLevel.L3_LIVE_COUNTERFACTUAL.value,
        )
        self.assertEqual("t-9", snapshot.task_id)
        self.assertEqual("gpt-x", snapshot.model_name)
        self.assertEqual("7", snapshot.policy_version)
        self.assertEqual("pv-1", snapshot.prompt_version)
        self.assertEqual(ReplayLevel.L3_LIVE_COUNTERFACTUAL.value,
                         snapshot.replay_level)
        self.assertEqual({"risk": "high"}, snapshot.expected_output)
        self.assertTrue(snapshot.diff_hash)

    def test_from_recorder_saves_and_returns_snapshot(self):
        from evoagent.runtime import AgentTool, ToolRegistry
        from evoagent.replay.recorder import ReplayRecorder

        defined = []
        wrapped = ToolRegistry(
            [AgentTool("read_file", "r",
                       {"properties": {"path": {"type": "string"}}}, lambda **k: "v")]
        )
        recorder = ReplayRecorder(wrapped)
        recorder.invoke("read_file", {"path": "a.py"})
        store = ReplayRepository()
        built = ReplaySnapshotBuilder(repository=store).from_recorder(
            recorder, task_id="t-x",
        )
        self.assertEqual("t-x", built.task_id)
        self.assertEqual(1, len(built.tool_observations))
        self.assertIs(built, store.snapshot(built.snapshot_id))


class AutoTraceAndSnapshotHarnessTest(unittest.TestCase):
    """8.1 + 8.3: a real harness run records a trace and a replay snapshot."""

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.store = TaskStore(self.path)

    def tearDown(self):
        os.unlink(self.path)

    def test_run_records_trace_and_snapshot(self):
        task_id = "auto-trace-task"
        trace_logger = DecisionTraceRepository()
        replay_repository = ReplayRepository()
        context = ReviewExecutionContext(
            task_id=task_id, tenant_id="default", repository="demo/repo",
            model_name="test-model",
        )
        self.store.create(task_id, "demo/repo", 7, {"source": "test"})
        ReviewHarness(
            self.store, LocalRuleReviewer(), execution_context=context,
            trace_logger=trace_logger,
            replay_repository=replay_repository,
        ).run(task_id, "demo/repo", 7, SIMPLE_DIFF)

        events = trace_logger.events(task_id)
        action_types = [event.action_type for event in events]
        self.assertIn("policy_resolution", action_types)
        self.assertIn("task_started", action_types)
        self.assertIn("task_completed", action_types)

        snapshots = replay_repository.snapshots_for_task(task_id)
        self.assertEqual(1, len(snapshots))
        snapshot = snapshots[0]
        self.assertEqual("test-model", snapshot.model_name)
        self.assertEqual("demo/repo", snapshot.repository)


if __name__ == "__main__":
    unittest.main()