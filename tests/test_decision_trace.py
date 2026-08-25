"""Tests for decision-trace observability (plan section 12)."""
import unittest

from evoagent.decision_trace import DecisionDiff, TraceEvent, TraceLogger


class DecisionTraceTest(unittest.TestCase):

    def _logger_with_baseline(self):
        logger = TraceLogger()
        base = logger.begin("t1")
        base.add(TraceEvent(step_id="1", action_type="policy_resolution",
                            policy_id="p1"))
        base.add(TraceEvent(step_id="2", action_type="agent_step",
                            agent_id="security-agent", tool="search_diff"))
        base.add(TraceEvent(step_id="3", action_type="agent_step",
                            agent_id="security-agent", tool="final"))
        return logger, base

    def test_tool_path(self):
        _, base = self._logger_with_baseline()
        self.assertEqual(base.tool_path(), ["search_diff", "final"])

    def test_candidate_diff_reports_added_step(self):
        logger, base = self._logger_with_baseline()
        cand = logger.begin("t2")
        cand.add(TraceEvent(step_id="1", action_type="agent_step",
                            tool="search_diff"))
        cand.add(TraceEvent(step_id="2", action_type="agent_step",
                            tool="find_callers"))
        cand.add(TraceEvent(step_id="3", action_type="agent_step",
                            tool="inspect_tests"))
        cand.add(TraceEvent(step_id="4", action_type="agent_step",
                            tool="final"))
        diff = logger.diff(base, cand)
        self.assertTrue(diff.differs)
        self.assertIn("find_callers", diff.added_steps)
        self.assertIn("inspect_tests", diff.added_steps)

    def test_no_diff_when_identical(self):
        logger = TraceLogger()
        a = logger.begin("a")
        a.add(TraceEvent(step_id="1", action_type="agent_step", tool="search_diff"))
        a.add(TraceEvent(step_id="2", action_type="agent_step", tool="final"))
        b = DecisionDiff(a.tool_path(), a.tool_path())
        self.assertFalse(b.differs)

    def test_trace_captures_failure_and_recovery(self):
        logger = TraceLogger()
        logger.record("t", TraceEvent(step_id="1", action_type="agent_step",
                                      failure=True, recovery_action="retry_backoff"))
        trace = logger.trace("t")
        self.assertEqual(trace.events[0].failure, True)
        self.assertEqual(trace.events[0].recovery_action, "retry_backoff")

    def test_render_lists_added(self):
        diff = DecisionDiff(["search_diff", "final"],
                            ["search_diff", "find_callers", "final"])
        text = diff.render()
        self.assertIn("Baseline:", text)
        self.assertIn("Candidate:", text)
        self.assertIn("Added", text)


if __name__ == "__main__":
    unittest.main()