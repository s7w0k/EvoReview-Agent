import unittest

from evoagent.replay.models import ReplaySnapshot
from evoagent.replay.runner import ReplayRunner
from evoagent.tools.audit import hash_args


class CounterfactualReplayTest(unittest.TestCase):
    def _snapshot(self):
        return ReplaySnapshot(
            snapshot_id="s1", task_id="t1", prompt_version="v10",
            policy_version="p1", diff_hash="abc",
            context_snapshot={"max_steps": 20},
            tool_observations=[
                {"fingerprint": "search_code#" + hash_args({"query": "auth"}),
                 "observation": [{"path": "auth/login.py"}]},
            ],
        )

    def test_candidate_uses_more_steps(self):
        def stepper_with(steps):
            def stepper(state):
                if len(state["observations"]) < steps:
                    return {"action": "tool", "tool": "search_code",
                            "arguments": {"query": "auth"}}
                return {"action": "final", "findings": ["f"]}
            return stepper

        snapshot = self._snapshot()
        runner = ReplayRunner()

        def metrics(result):
            return {"finding_f1": 0.5 + 0.05 * len(result["observations"]),
                    "high_risk_recall": 0.9}

        baseline = runner.run_and_measure(snapshot, stepper_with(1), metrics)
        candidate = runner.run_and_measure(snapshot, stepper_with(3), metrics)
        self.assertEqual(baseline["tool_calls"], 1)
        self.assertEqual(candidate["tool_calls"], 3)
        self.assertEqual(baseline["finding_f1"], 0.55)
        self.assertEqual(candidate["finding_f1"], 0.65)


if __name__ == "__main__":
    unittest.main()