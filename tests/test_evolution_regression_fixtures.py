"""Phase 11 acceptance tests: evolution regression fixtures (plan section 15.6).

``tests/fixtures/evolution/*.json`` pins the invariant that a *known-good*
candidate always PASSES the hard gate while a *known-bad* candidate (a critical
miss regardless of quality gain) is always gate-REJECTED.  This is the safety
backstop that CI enforces on every change.
"""
import json
import os
import unittest

from evoagent.policy_evolution.gate import EvolutionGate
from evoagent.policy_evolution.objective import EvolutionMetrics

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "evolution")


def _load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return json.load(fh)


def _metrics(payload):
    return EvolutionMetrics(**payload)


class KnownGoodRegressionFixtureTest(unittest.TestCase):
    """15.6 -- Known Good candidate MUST pass the hard gate."""

    def test_known_good_passes(self):
        data = _load("known_good_candidate.json")
        self.assertEqual(data["expectation"], "pass")
        gate = EvolutionGate()
        decision = gate.evaluate(
            _metrics(data["baseline"]), _metrics(data["candidate"]))
        self.assertTrue(decision.approved, msg=f"gate rejected: {decision.reasons}")


class KnownBadRegressionFixtureTest(unittest.TestCase):
    """15.6 -- Known Bad candidate MUST be hard-gate rejected."""

    def test_known_bad_rejected(self):
        data = _load("known_bad_candidate.json")
        self.assertEqual(data["expectation"], "reject")
        gate = EvolutionGate()
        decision = gate.evaluate(
            _metrics(data["baseline"]), _metrics(data["candidate"]))
        self.assertTrue(decision.rejected)
        # The rejection must cite the critical miss, not just quality noise.
        joined = " ".join(decision.reasons).lower()
        self.assertIn("critical miss", joined)

    def test_even_quality_gain_cannot_pass_critical_miss(self):
        """Safety constraints take precedence over optimization (section 9.4)."""
        data = _load("known_bad_candidate.json")
        m = _metrics(data["candidate"])
        self.assertGreater(m.quality_score, _metrics(data["baseline"]).quality_score)
        decision = EvolutionGate().evaluate(
            _metrics(data["baseline"]), m)
        self.assertTrue(decision.rejected)


if __name__ == "__main__":
    unittest.main()