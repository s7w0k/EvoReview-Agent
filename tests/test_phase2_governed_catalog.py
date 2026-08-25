"""Phase 2 acceptance tests: GovernedToolRegistry takes over every tool.

Covers (plan section 6):
  6.1  Unified Tool Catalog with ``ToolDefinition``.
  6.2  First batch of 10 governed tools.
  6.3  AgentLoop passes agent identity through ``invoke_as``.
  6.4  ProcedureExecutor is bound to the governed registry (no bypass).
  6.5  Live replay uses a read-only replay policy (side-effects denied).
  6.6  Harness tool metrics increment on the governed path.
"""
import unittest
from dataclasses import replace

from evoagent.agents import MultiAgentCoordinator
from evoagent.policy.models import ExecutionPolicy, ToolPermission
from evoagent.policy.tool_policy import (
    ToolPermissionDenied,
    ToolPolicyEngine,
)
from evoagent.procedure.executor import ProcedureExecutor
from evoagent.procedure.schema import (
    ProceduralStep,
    ProcedureBudget,
    ProcedureSkill,
)
from evoagent.replay.models import ReplaySnapshot
from evoagent.replay.recorder import ReplayToolRegistry
from evoagent.runtime import AgentLoop, AgentTool
from evoagent.tools.audit import ToolAuditLogger
from evoagent.tools.catalog import (
    build_runtime_tools,
    build_tool_metadata,
)
from evoagent.tools.governed_registry import (
    GovernedToolRegistry,
    procedure_tool_invoker,
    read_only_replay_policy,
)
from evoagent.metrics import Metrics


CATALOG_TOOLS = {
    "search_diff", "changed_line", "list_changed_files", "recall_memory",
    "read_file", "search_code", "find_callers", "find_tests",
    "run_static_analysis", "run_tests",
}


def governed_registry(execution_policy=None):
    from evoagent.policy.defaults import default_policy
    definitions = build_runtime_tools(diff="", parsed=None)
    return GovernedToolRegistry(
        [d.tool for d in definitions],
        execution_policy=execution_policy or default_policy("low"),
        policy_engine=ToolPolicyEngine(build_tool_metadata()),
        audit=ToolAuditLogger(),
        timeout_extension=30.0,
    )


class CatalogTest(unittest.TestCase):
    def test_unified_catalog_has_first_batch(self):
        definitions = build_runtime_tools()
        present = {d.tool.name for d in definitions}
        self.assertTrue(CATALOG_TOOLS.issubset(present))
        # Every definition bundles its ToolMetadata.
        for definition in definitions:
            self.assertEqual(definition.tool.name, definition.metadata.name)

    def test_metadata_is_static_and_stable(self):
        metadata = build_tool_metadata()
        self.assertTrue(CATALOG_TOOLS.issubset(metadata))
        # run_tests is a sandboxed side-effect; search_diff is read-only.
        self.assertTrue(metadata["run_tests"].requires_sandbox)
        self.assertTrue(metadata["run_tests"].side_effect)
        self.assertTrue(metadata["search_diff"].idempotent)


class AgentLoopIdentityTest(unittest.TestCase):
    def test_agent_identity_routed_through_governed_registry(self):
        registry = governed_registry()
        agent_id = "security-agent"

        def stepper(state):
            if state["loop_step"] == 1:
                return {"action": "tool", "tool": "search_diff",
                        "arguments": {"query": "eval"}}
            return {"action": "final", "findings": []}

        loop = AgentLoop(max_steps=3, timeout_seconds=5)
        result = loop.run(stepper, registry, {}, event_sink=None,
                          agent_id=agent_id, task_id="t-1")
        entries = registry.audit.entries()
        self.assertGreaterEqual(len(entries), 1)
        self.assertEqual(entries[0].agent_id, agent_id)
        self.assertEqual(entries[0].task_id, "t-1")
        self.assertEqual(entries[0].tool_name, "search_diff")

    def test_governance_denies_side_effect_tool(self):
        registry = governed_registry()
        with self.assertRaises(ToolPermissionDenied):
            registry.invoke_as("agent", "run_tests", {}, task_id="t")


class ProcedureGovernanceTest(unittest.TestCase):
    def test_procedure_executor_uses_governed_registry(self):
        registry = governed_registry()
        invoker = procedure_tool_invoker(registry, "coverage-check", task_id="t-9")
        skill = ProcedureSkill(
            name="coverage-check",
            procedure=[
                ProceduralStep(kind="tool", tool="search_diff",
                               args={"query": "TODO"}, result_var="hits"),
            ],
            budget=ProcedureBudget(max_steps=5, max_tool_calls=10),
        )
        executor = ProcedureExecutor(invoker)
        result = executor.execute(skill)
        self.assertEqual(result.tool_calls, 1)
        entries = registry.audit.entries()
        self.assertEqual(entries[0].agent_id, "procedure:coverage-check")
        self.assertEqual(entries[0].task_id, "t-9")


class ReplayGovernanceTest(unittest.TestCase):
    def test_read_only_replay_policy_denies_side_effect(self):
        policy = read_only_replay_policy(
            ["search_diff", "changed_line", "list_changed_files"]
        )
        definitions = build_runtime_tools()
        registry = GovernedToolRegistry(
            [d.tool for d in definitions],
            execution_policy=policy,
            policy_engine=ToolPolicyEngine(build_tool_metadata()),
        )
        # read-only tool is allowed.
        registry.invoke_as("replay-agent", "search_diff", {"query": "x"}, task_id="r")
        # side-effect tool is denied by the read-only policy.
        with self.assertRaises(ToolPermissionDenied):
            registry.invoke_as("replay-agent", "run_tests", {}, task_id="r")

    def test_live_replay_routes_through_governed_registry(self):
        snapshot = ReplaySnapshot(task_id="t-live")
        registry = governed_registry()
        replay_tools = ReplayToolRegistry(
            snapshot, registry, read_only_tools={}, mode="live",
        )
        snapshot.context_snapshot["max_steps"] = 5
        value = replay_tools.invoke("search_diff", {"query": "pass"})
        self.assertIsInstance(value, list)
        entries = registry.audit.entries()
        self.assertEqual(entries[0].agent_id, "replay-agent")
        self.assertEqual(entries[0].task_id, "t-live")


class ToolMetricsTest(unittest.TestCase):
    def test_governed_metrics_increment(self):
        metrics = Metrics()
        # Patch the module-global metrics the registry uses onto a fresh instance
        # so this test never depends on global counters.
        from evoagent.tools import governed_registry as gr
        original = gr.metrics
        gr.metrics = metrics
        try:
            registry = governed_registry()
            registry.invoke_as("a", "search_diff", {"query": "x"}, task_id="t")
            self.assertEqual(metrics.counters.get("tool_calls_total", 0), 1)
            with self.assertRaises(ToolPermissionDenied):
                registry.invoke_as("a", "run_tests", {}, task_id="t")
            self.assertGreaterEqual(
                metrics.counters.get("tool_policy_violation_total", 0), 1
            )
        finally:
            gr.metrics = original


if __name__ == "__main__":
    unittest.main()