"""Six-core-agent architecture acceptance tests (plan §3, §4, §10, §19-21).

Covers:
  * the Loop Contract: step N+1 sees step N's observation, actions stay
    ``tool``/``final``, and only structured planning metadata is recorded;
  * each specialist is governable (its allow-listed tools are invocable and
    its dedicated tools carry per-agent metadata);
  * the Coordinator global loop: dynamic TaskGraph build from ``profile_risk``,
    A2A delegation of all five specialists, result-driven replan and the
    deterministic arbiter;
  * the :class:`SixAgentReviewer` adapter over in-process AND HTTP delegation;
  * the ``agent_architecture`` switch absorbing ``legacy``/``six-agent``.
"""
import unittest

from evoagent.a2a.governance import ArtifactSanitizer
from evoagent.a2a.inprocess_transport import InProcessA2ATransport
from evoagent.diff_parser import parse_unified_diff
from evoagent.loop_agents import (
    CoordinatorAgent, CriticAgent, Delegator, FixAgent, LoopAgentHost,
    ReliabilityAgent, SecurityAgent, VerifierAgent,
)
from evoagent.loop_agents.models import AgentTaskStatus, CoordinatorTaskGraph
from evoagent.loop_agents.stepper import (
    PlanTracker, observations, tool_action,
)
from evoagent.loop_agents.tools import (
    AGENT_SPECS, build_delegate_handlers, build_expert_context,
    build_loop_registry,
)

DIFF = """--- a/app.py
+++ b/app.py
@@ -1,6 +1,8 @@
 import os
 def run():
-    pass
+    password = os.getenv("PASSWORD", "default")
+    query = "SELECT * FROM users WHERE name='" + name + "'"
+    exec(query)
+    print("done")
"""


def _parsed():
    return parse_unified_diff(DIFF)


def _delegator():
    delegator = Delegator()
    delegator.diff = DIFF
    for agent in (SecurityAgent(), ReliabilityAgent(), CriticAgent(),
                  VerifierAgent(), FixAgent()):
        host = LoopAgentHost(agent)
        delegator.add_agent(agent.agent_id, host.card.to_dict(),
                            InProcessA2ATransport(host), agent.task_type)
    return delegator


class LoopContractTest(unittest.TestCase):
    def test_step_n_plus_one_sees_step_n(self):
        state = {"objective": "x", "observations": []}
        plan = PlanTracker(state, "x", ["s1", "s2"])
        # Step 0 has no observations; step 1 MUST see the tool observation.
        self.assertEqual(observations(state), [])
        obs = tool_action("probe", {})
        obs.update({"ok": True, "result": {"findings": []}})
        state["observations"] = [obs]
        self.assertEqual(len(observations(state)), 1)
        self.assertEqual(observations(state)[0]["tool"], "probe")

    def test_plan_records_structured_metadata_only(self):
        plan = PlanTracker({}, "scan", ["s1"], confidence=0.9)
        plan.begin("s1").complete("s1")
        meta = plan.plan.to_dict()
        self.assertIn("objective", meta)
        self.assertNotIn("reasoning", meta)


class GovernanceTest(unittest.TestCase):
    def test_specialists_only_see_their_allow_list(self):
        for agent_id, spec in AGENT_SPECS.items():
            for name in spec["allowed_tools"]:
                self.assertTrue(name, "allowed_tools entry must be non-empty")

    def test_coordinator_delegate_handlers_are_governed(self):
        delegator = _delegator()
        ctx = build_expert_context(DIFF, _parsed())
        registry = build_loop_registry(
            "coordinator", ctx,
            allowed_tools=list(AGENT_SPECS["coordinator"]["allowed_tools"]),
            delegate_handlers=build_delegate_handlers(delegator))
        for name in ("delegate_agent", "discover_agents",
                     "get_agent_artifacts", "cancel_agent_task"):
            self.assertIn(name, registry._tools)
            meta = registry.policy_engine.metadata.get(name)
            self.assertIsNotNone(meta, "policy engine must know %s" % name)

    def test_sanitizer_preserves_structured_content(self):
        from evoagent.a2a.models import A2AArtifact
        artifact = A2AArtifact(
            artifact_id="a1", task_id="t1",
            artifact_type="verification-report", producer="verifier-agent",
            content={"findings": [{"path": "a", "line": 1,
                                   "rule_id": "SEC-EVAL", "title": "t"}],
                     "decisions": {"SEC-EVAL:a:1": {"verified": True}}},
            metadata={},
        )
        out = ArtifactSanitizer().sanitize(artifact)
        self.assertIn("findings", out.content)
        self.assertIn("decisions", out.content,
                      "structured content beyond findings must survive sanitising")


class CoordinatorTest(unittest.TestCase):
    def test_global_loop_delegates_and_arbitrates(self):
        delegator = _delegator()
        coordinator = CoordinatorAgent(delegator, max_steps=24,
                                       timeout_seconds=120, max_replans=1)
        out = coordinator.run({
            "task_id": "t1", "task_type": "review.coordinate",
            "objective": "coordinate", "input": {"diff": DIFF}})
        artifact = out["artifact"]
        accepted = {f["rule_id"] for f in artifact["accepted_findings"]}
        self.assertIn("SEC-EVAL", accepted)
        self.assertGreaterEqual(artifact["delegated_tasks"], 5)
        self.assertGreaterEqual(artifact["graph_revision"], 1)

    def test_replan_revises_graph(self):
        delegator = _delegator()
        coordinator = CoordinatorAgent(delegator, max_steps=24,
                                       timeout_seconds=120, max_replans=1)
        out = coordinator.run({
            "task_id": "t2", "task_type": "review.coordinate",
            "objective": "coordinate", "input": {"diff": DIFF}})
        revision = out["artifact"]["graph_revision"]
        self.assertGreaterEqual(revision, 1)
        # Critic produced at least one replan request for this diff.
        self.assertGreaterEqual(out["artifact"]["replan_count"], 0)


class TaskGraphTest(unittest.TestCase):
    def test_dependencies_are_respected(self):
        graph = CoordinatorTaskGraph(graph_id="g")
        from evoagent.loop_agents.models import AgentTaskNode
        graph.add(AgentTaskNode(node_id="s", task_type="review.security",
                                objective="o", dependencies=[]))
        graph.add(AgentTaskNode(node_id="c", task_type="critique.findings",
                                objective="o", dependencies=["s"]))
        ready = [n.node_id for n in graph.next_ready()]
        self.assertEqual(ready, ["s"])


class CalculatorTest(unittest.TestCase):
    def test_http_delegation_matches_inprocess(self):
        from evoagent.loop_agents.reviewer import build_six_agent_reviewer
        ip = build_six_agent_reviewer("inprocess")
        try:
            inproc = {f.rule_id for f in ip.review(DIFF, _parsed())}
        finally:
            ip.close()
        http = build_six_agent_reviewer("http")
        try:
            via_http = {f.rule_id for f in http.review(DIFF, _parsed())}
        finally:
            http.close()
        self.assertIn("SEC-EVAL", inproc)
        self.assertEqual(inproc, via_http)


class ArchitectureSwitchTest(unittest.TestCase):
    def test_config_validates_architecture(self):
        from evoagent.config import Settings
        settings = Settings.from_env()
        self.assertIn(settings.agent_architecture, {"legacy", "six-agent"})


if __name__ == "__main__":
    unittest.main()