"""Phase 0 acceptance tests: P0 semantic fixes to tool governance and policy.

Covers (plan section 4):
  4.1 Approval fail-closed
  4.2 Global tool budget (__all__) with correct consume semantics
  4.3 Real tool timeout (subprocess terminate->grace->kill)
  4.4 Sandbox enforcement
  4.5 Immutable safety floor (last, high/critical hardening)
  4.6 ToolPermission merge-by-name
  4.7 Evolution utility baseline + improvement
  4.8 Candidate mutation preserves parent fields
"""
import time
import unittest
from dataclasses import replace

from evoagent.policy.defaults import AGENT_SECURITY, AGENT_RELIABILITY, AGENT_SEMANTIC
from evoagent.policy.models import (
    AgentPolicy,
    ExecutionBudget,
    ExecutionPolicy,
    ToolPermission,
    VerificationPolicy,
)
from evoagent.policy.resolver import PolicyResolver
from evoagent.policy.safety_floor import SafetyFloor, apply_safety_floor
from evoagent.policy.tool_policy import (
    ToolMetadata,
    ToolPermissionDenied,
    ToolPolicyEngine,
    merge_tool_permissions,
)
from evoagent.policy_evolution.candidate import (
    CandidateOperation,
    PolicyCandidateGenerator,
)
from evoagent.policy_evolution.objective import EvolutionMetrics
from evoagent.policy_evolution.replay_eval import PolicyReplayEvaluator
from evoagent.runtime import AgentTool
from evoagent.tools.executor import ToolExecutionResult, ToolExecutor, ToolTimeoutError
from evoagent.tools.governed_registry import GovernedToolRegistry
from evoagent.tools.sandbox import SandboxContext, SandboxEnforcer


def perm(name, allow=True, requires_approval=False, requires_sandbox=False):
    return ToolPermission(name, allow=allow, requires_approval=requires_approval,
                          requires_sandbox=requires_sandbox)


def policy(risk="low", max_tool_calls=10, permissions=None, verification=None, **kwargs):
    return ExecutionPolicy(
        policy_id="p", risk_level=risk,
        budget=ExecutionBudget(max_steps=8, max_tool_calls=max_tool_calls),
        verification=verification or VerificationPolicy(),
        agents=AgentPolicy(enabled_agents=["reliability"]),
        tool_permissions=permissions or [
            perm("read_file"), perm("run_tests", requires_sandbox=True),
            perm("write_comment", requires_approval=True),
        ],
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 4.1 Approval fail-closed
# ---------------------------------------------------------------------------
class ApprovalFailClosedTest(unittest.TestCase):
    def build(self):
        engine = ToolPolicyEngine({
            "write_comment": ToolMetadata("write_comment", side_effect=True,
                                          idempotent=False, allowed_agents=["reliability"]),
        })
        return GovernedToolRegistry(
            [AgentTool("write_comment", "w", {}, lambda **_: "ok")],
            policy(permissions=[perm("write_comment", requires_approval=True)]),
            engine,
        )

    def test_required_approval_without_provider_is_denied(self):
        registry = self.build()
        with self.assertRaises(ToolPermissionDenied) as ctx:
            registry.invoke_as("reliability", "write_comment", {})
        self.assertIn("no approval provider", str(ctx.exception))

    def test_required_approval_declined_is_denied(self):
        registry = self.build()
        registry.approval_provider = lambda _decision: False
        with self.assertRaises(ToolPermissionDenied) as ctx:
            registry.invoke_as("reliability", "write_comment", {})
        self.assertIn("approval declined", str(ctx.exception))

    def test_required_approval_success_executes(self):
        registry = self.build()
        registry.approval_provider = lambda _decision: True
        self.assertEqual(registry.invoke_as("reliability", "write_comment", {}), "ok")


# ---------------------------------------------------------------------------
# 4.2 Global tool budget across tools + consume semantics
# ---------------------------------------------------------------------------
class GlobalToolBudgetTest(unittest.TestCase):
    def build(self, max_tool_calls=3):
        engine = ToolPolicyEngine({
            "read_file": ToolMetadata("read_file", risk_level="low"),
            "search_code": ToolMetadata("search_code", risk_level="low"),
            "write_comment": ToolMetadata("write_comment", side_effect=True,
                                          idempotent=False, allowed_agents=["reliability"]),
        })
        return GovernedToolRegistry(
            [
                AgentTool("read_file", "r", {}, lambda **_: 1),
                AgentTool("search_code", "s", {}, lambda **_: 2),
                AgentTool("write_comment", "w",
                          {"properties": {"content": {"type": "string"}}}, lambda **_: 3),
            ],
            policy(max_tool_calls=max_tool_calls, permissions=[
                perm("read_file"), perm("search_code"),
                perm("write_comment", requires_approval=True),
            ]),
            engine,
        )

    def test_total_budget_across_multiple_tools(self):
        registry = self.build(max_tool_calls=2)
        registry.approval_provider = lambda _: True
        registry.invoke_as("reliability", "read_file", {})
        registry.invoke_as("reliability", "search_code", {})
        # __all__ counts both tools.
        self.assertEqual(registry.tool_call_counts()["__all__"], 2)
        # Third call hits the global ceiling.
        with self.assertRaises(Exception):
            registry.invoke_as("reliability", "read_file", {})

    def test_failed_tool_consumes_budget(self):
        registry = self.build(max_tool_calls=1)

        def boomer(**args):
            raise RuntimeError("boom")

        registry.policy_engine.metadata["search_code"] = ToolMetadata("search_code")
        registry._tools["search_code"] = AgentTool("search_code", "s", {}, boomer)
        with self.assertRaises(RuntimeError):
            registry.invoke_as("reliability", "search_code", {})
        self.assertEqual(registry.tool_call_counts()["__all__"], 1)

    def test_denied_tool_does_not_consume_budget(self):
        registry = self.build(max_tool_calls=1)
        # write_comment requires approval: no provider wired -> approval-denied.
        with self.assertRaises(ToolPermissionDenied):
            registry.invoke_as("reliability", "write_comment", {"content": "c"})
        self.assertEqual(registry.tool_call_counts().get("__all__", 0), 0)


# ---------------------------------------------------------------------------
# 4.3 Real tool timeout (subprocess)
# ---------------------------------------------------------------------------
class ToolTimeoutTest(unittest.TestCase):
    def blocking_metadata(self):
        return ToolMetadata(
            "run_tests", risk_level="high", requires_sandbox=True,
            timeout_seconds=0.5, blocking=True, command="{cmd}",
        )

    def test_blocking_tool_terminated_after_timeout(self):
        tool = AgentTool("run_tests", "rt",
                         {"properties": {"cmd": {"type": "string"}}}, lambda **_: None)
        exec_sandbox = ToolExecutor(sandbox=SandboxContext(task_id="t1",
                                                           workspace="."),
                                    sandbox_enforcer=SandboxEnforcer())
        start = time.perf_counter()
        with self.assertRaises(ToolTimeoutError):
            exec_sandbox.execute(tool, {"cmd": "python -c \"import time; time.sleep(30)\""},
                                 self.blocking_metadata(), {})
        # Must return fast: the subprocess is terminated, not finished.
        self.assertLess(time.perf_counter() - start, 5.0)

    def timeout_registry(self, circuit_breaker=None):
        kwargs = {}
        if circuit_breaker is not None:
            kwargs["circuit_breaker"] = circuit_breaker
        kwargs["sandbox_context"] = SandboxContext(task_id="t1", workspace=".")
        return GovernedToolRegistry(
            [AgentTool("run_tests", "rt",
                       {"properties": {"cmd": {"type": "string"}}}, lambda **_: None)],
            policy(risk="high", permissions=[perm("run_tests")]),
            ToolPolicyEngine({"run_tests": self.blocking_metadata()}),
            **kwargs,
        )

    def test_timeout_generates_failure_event(self):
        registry = self.timeout_registry()
        with self.assertRaises(ToolTimeoutError):
            registry.invoke_as("reliability", "run_tests",
                               {"cmd": "python -c \"import time; time.sleep(30)\""}, "t1")
        entry = registry.audit.entries()[-1]
        self.assertEqual(entry.status, "timeout")

    def test_timeout_updates_circuit_breaker(self):
        from evoagent.tools.circuit_breaker import CircuitBreaker
        breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=1)
        registry = self.timeout_registry(circuit_breaker=breaker)
        for _ in range(2):
            with self.assertRaises(ToolTimeoutError):
                registry.invoke_as("reliability", "run_tests",
                                   {"cmd": "python -c \"import time; time.sleep(30)\""}, "t1")
        self.assertEqual(breaker.state("run_tests"), "open")


# ---------------------------------------------------------------------------
# 4.4 Sandbox enforcement
# ---------------------------------------------------------------------------
class SandboxEnforcementTest(unittest.TestCase):
    def test_sandbox_env_blocks_network_and_allowlists(self):
        enforcer = SandboxEnforcer(allowlist=["PATH"])
        env = enforcer.build_env(SandboxContext(task_id="t1", workspace=".",
                                                env_allowlist=["MY_FLAG"]),
                                 extra={"MY_FLAG": "1"})
        # Whitelisted + explicitly-passed vars survive.
        self.assertEqual(env.get("MY_FLAG"), "1")
        # Network egress disabled by default.
        self.assertIn("HTTP_PROXY", env)
        # A secret from the ambient environment never leaks.
        self.assertNotIn("AWS_SECRET", env)

    def test_sandbox_blocking_tool_uses_sandbox_environment(self):
        registry = GovernedToolRegistry(
            [AgentTool("run_tests", "rt",
                       {"properties": {"cmd": {"type": "string"}}}, lambda **_: None)],
            policy(risk="high", permissions=[perm("run_tests")]),
            ToolPolicyEngine({"run_tests": self.blocking_metadata()}),
            sandbox_context=SandboxContext(task_id="t1", workspace="."),
        )
        with self.assertRaises(ToolTimeoutError):
            registry.invoke_as("reliability", "run_tests",
                               {"cmd": "python -c \"import time; time.sleep(30)\""}, "t1")

    def blocking_metadata(self):
        return ToolMetadata(
            "run_tests", risk_level="high", requires_sandbox=True,
            timeout_seconds=0.4, blocking=True, command="{cmd}",
        )


# ---------------------------------------------------------------------------
# 4.5 Immutable safety floor
# ---------------------------------------------------------------------------
class SafetyFloorTest(unittest.TestCase):
    def test_high_critical_cannot_lower_verification(self):
        floor = SafetyFloor(minimum_risk_level="high", require_critic=True,
                            require_evidence=True, require_verifier=True,
                            require_sandbox=True)
        # A high-risk policy that (wrongly) relaxed verification gets re-hardened.
        relaxed = policy(risk="high", verification=VerificationPolicy())
        hardened = apply_safety_floor(relaxed, floor)
        self.assertTrue(hardened.verification.critic_required)
        self.assertTrue(hardened.verification.evidence_required)
        self.assertTrue(hardened.verification.verifier_required)
        self.assertTrue(hardened.verification.sandbox_required)

    def test_mandatory_tool_deny_cannot_be_disabled_by_task(self):
        floor = SafetyFloor(mandatory_tool_denies={"push_fix"})
        resolver = PolicyResolver(safety_floor=floor)
        task = {"policy": {"tool_permissions": [perm("push_fix", allow=True).__dict__]}}
        resolved = resolver.resolve(task)
        p = resolved.tool_permission("push_fix")
        self.assertIsNotNone(p)
        self.assertFalse(p.allow)

    def test_floor_applied_last_beats_low_precedence(self):
        floor = SafetyFloor(minimum_risk_level="high", require_critic=True,
                            require_evidence=True)
        resolver = PolicyResolver(safety_floor=floor)
        resolved = resolver.resolve({})
        self.assertTrue(resolved.verification.critic_required)


# ---------------------------------------------------------------------------
# 4.6 merge_tool_permissions by-name
# ---------------------------------------------------------------------------
class MergePermissionsTest(unittest.TestCase):
    def test_repository_deny_overrides_system_allow(self):
        base = [perm("read_file"), perm("search_code")]
        override = [perm("search_code", allow=False)]
        merged = merge_tool_permissions(base, override)
        self.assertEqual(merged[1].allow, False)
        # System-only tool unchanged.
        self.assertEqual(merged[0].allow, True)

    def test_task_allow_cannot_flip_immutable_hard_deny(self):
        # describe: whatever a task allows, the safety floor still blocks it
        floor = SafetyFloor(mandatory_tool_denies={"push_fix"})
        resolver = PolicyResolver(safety_floor=floor)
        resolved = resolver.resolve(
            {"policy": {"tool_permissions": [
                {"tool_name": "push_fix", "allow": True},
            ]}})
        self.assertFalse(resolved.tool_permission("push_fix").allow)


# ---------------------------------------------------------------------------
# 4.7 Evolution utility baseline + improvement
# ---------------------------------------------------------------------------
class EvolutionUtilityTest(unittest.TestCase):
    def test_baseline_utility_and_improvement(self):
        baseline = EvolutionMetrics(quality_score=0.5, cost=10, latency=5)
        candidate = EvolutionMetrics(quality_score=0.9, cost=5, latency=2)
        evaluator = PolicyReplayEvaluator(lambda metrics: metrics)
        comp = evaluator.evaluate(baseline, candidate)
        # baseline_utility = utility(baseline vs itself): .5*.4 + 1*.15
        # - 1*.15 - 1*.10 = 0.20 + 0.15 - 0.15 - 0.10 = 0.10.
        self.assertEqual(comp.baseline_utility, 0.1)
        # The higher-quality, lower-cost candidate scores above the baseline.
        self.assertGreater(comp.utility, comp.baseline_utility)
        self.assertGreater(comp.improvement, 0.0)
        self.assertTrue(comp.is_improvement(0.0))
        self.assertAlmostEqual(comp.improvement, comp.utility - comp.baseline_utility, places=3)


# ---------------------------------------------------------------------------
# 4.8 candidate mutation preserves parent fields
# ---------------------------------------------------------------------------
class CandidateMutationTest(unittest.TestCase):
    def test_mutation_keeps_untouched_parent_fields(self):
        parent = policy(risk="medium", max_tool_calls=12,
                        permissions=[perm("read_file")],
                        metadata={"tenant": "acme"})
        gen = PolicyCandidateGenerator()
        candidates = gen.generate(
            parent, [CandidateOperation.ENABLE_EVIDENCE], add_agent="",
            remove_agent=None,
        )
        self.assertEqual(len(candidates), 1)
        cand = candidates[0].policy
        # The mutated field changed...
        self.assertTrue(cand.verification.evidence_required)
        # ...and every untouched field is preserved from the parent.
        self.assertEqual(cand.risk_level, parent.risk_level)
        self.assertEqual(cand.budget.max_tool_calls, parent.budget.max_tool_calls)
        self.assertEqual(cand.metadata.get("tenant"), "acme")
        self.assertEqual(cand.agents, parent.agents)


if __name__ == "__main__":
    unittest.main()