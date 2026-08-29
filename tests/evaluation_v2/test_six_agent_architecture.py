"""Evaluation V2 must exercise the real six-agent-v2 production leader."""
import gc
import shutil
import tempfile
import time
import unittest
from os.path import join

from evoagent.evaluation_v2.adapters import (
    CurrentHarnessEvaluationAdapter,
    EvolvedHarnessEvaluationAdapter,
    build_evaluation_service,
)
from evoagent.skill_evolution import DeclarativeSkillReviewer

from tests.evaluation_v2.helpers import make_risk_case


class SixAgentArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        gc.collect()
        for _ in range(5):
            try:
                shutil.rmtree(self.tmp)
                return
            except PermissionError:
                time.sleep(0.1)
                gc.collect()

    def test_evaluation_service_pins_six_agent_v2(self):
        svc = build_evaluation_service(join(self.tmp, "settings.db"))
        try:
            self.assertEqual("six-agent-v2", svc.settings.agent_architecture)
        finally:
            svc.close()

    def test_current_adapter_exposes_runtime_wiring(self):
        adapter = CurrentHarnessEvaluationAdapter(join(self.tmp, "current.db"))
        try:
            result = adapter.review_case(make_risk_case())
            self.assertEqual("six-agent-v2", result.architecture)
            self.assertTrue(result.graph_shapes)
            self.assertIn("security-agent", result.called_agents)
            self.assertTrue(result.loop_steps_by_agent)
        finally:
            adapter.close()

    def test_evolved_candidate_is_invoked_inside_security_tool_layer(self):
        artifact = {
            "name": "evolved-frozen-test",
            "description": "test candidate",
            "rules": [{
                "rule_id": "CWE-95", "severity": "critical", "match": "eval(",
                "title": "eval", "explanation": "unsafe eval", "fix": "remove eval",
                "test": "test safe parser", "confidence": 0.9,
            }],
        }
        reviewer = DeclarativeSkillReviewer(artifact, version=1)
        candidate_id = "eval-v2-evolved-frozen-test"
        adapter = EvolvedHarnessEvaluationAdapter(
            join(self.tmp, "evolved.db"), [reviewer], candidate_id=candidate_id)
        try:
            result = adapter.review_case(make_risk_case())
            self.assertEqual("six-agent-v2", result.architecture)
            self.assertGreater(result.skill_invocations.get(candidate_id, 0), 0)
            self.assertGreater(result.skill_invocations.get("security-rule@1", 0), 0)
        finally:
            adapter.close()


if __name__ == "__main__":
    unittest.main()
