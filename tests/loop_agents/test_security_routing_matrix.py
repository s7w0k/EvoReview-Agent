'''Domain routing matrix (plan Phase 2 / §6.1).

The shared predicates -- used by the profiler, the planner and the fallback
planner -- must never silently drop a security specialist: a high-risk or
critical diff is dual-routed, a clean diff keeps only the lightweight
reliability pass, and pure reliability still reaches the reliability specialist.
'''
import unittest

from evoagent.diff_parser import parse_unified_diff
from evoagent.loop_agents.planning.risk_signals import (
    AGENT_RELIABILITY,
    AGENT_SECURITY,
    classify_risk,
    should_route_reliability,
    should_route_security,
)


def _profile(line):
    diff = f"--- a/app.py\n+++ b/app.py\n@@ -1,1 +1,1 @@\n+{line}\n"
    return classify_risk(diff, parse_unified_diff(diff))


class SecurityRoutingMatrixTests(unittest.TestCase):
    def test_pure_security_dual_routs_high_risk(self):
        risk = _profile("result = eval(user_input)")
        self.assertEqual("critical", risk["level"])
        self.assertIn(AGENT_SECURITY, risk["agents"])
        self.assertTrue(should_route_security(risk))
        # high risk => security specialist can never be dropped => both routes
        self.assertIn(AGENT_RELIABILITY, risk["agents"])
        self.assertTrue(should_route_reliability(risk))

    def test_high_security_never_drops_reliability(self):
        risk = _profile("subprocess.run(user_cmd, shell=True)")
        self.assertIn("high", (risk["level"], "double-check"))
        self.assertTrue(should_route_security(risk))
        self.assertTrue(should_route_reliability(risk))

    def test_pure_reliability_routes_reliability_only(self):
        risk = _profile("while True:")
        self.assertTrue(should_route_reliability(risk))
        self.assertIn(AGENT_RELIABILITY, risk["agents"])

    def test_clean_diff_keeps_light_reliability_only(self):
        risk = _profile("x = x + 1")
        self.assertEqual("low", risk["level"])
        self.assertFalse(should_route_security(risk))
        self.assertNotIn(AGENT_SECURITY, risk["agents"])
        # reliability baseline keeps running on clean PRs
        self.assertTrue(should_route_reliability(risk))

    def test_weak_signature_does_not_force_security(self):
        risk = _profile("cfg = get_config()")  # no security signal
        self.assertFalse(should_route_security(risk))

    def test_debug_print_routes_reliability(self):
        # Phase-7 residual: PRs adding only a debug print must still reach the
        # reliability specialist so REL-DEBUG-PRINT fires (no silent drop).
        risk = _profile("print(value)")
        self.assertIn(AGENT_RELIABILITY, risk["agents"])
        self.assertTrue(should_route_reliability(risk))


if __name__ == "__main__":
    unittest.main()