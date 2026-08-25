import unittest

from evoagent.replay.models import ReplaySnapshot
from evoagent.replay.recorder import ReplayToolRegistry
from evoagent.runtime import AgentTool, ToolRegistry
from evoagent.tools.audit import hash_args


def echo(**arguments):
    return {"echo": arguments}


class ReplayToolsTest(unittest.TestCase):
    def setUp(self):
        self.live = ToolRegistry([
            AgentTool("read_file", "read", {"properties": {"path": {"type": "string"}}}, echo),
            AgentTool("push_fix", "push", {}, echo),
        ])
        self.snapshot = ReplaySnapshot(tool_observations=[
            {"fingerprint": "read_file#" + hash_args({"path": "a.py"}),
             "observation": "RECORDED-CONTENT"},
        ])

    def test_deterministic_replay_returns_recorded_observation(self):
        registry = ReplayToolRegistry(self.snapshot, self.live)
        result = registry.invoke("read_file", {"path": "a.py"})
        self.assertEqual(result, "RECORDED-CONTENT")

    def test_deterministic_missing_observation_raises(self):
        registry = ReplayToolRegistry(self.snapshot, self.live)
        from evoagent.runtime import AgentLoopProtocolError
        with self.assertRaises(AgentLoopProtocolError):
            registry.invoke("read_file", {"path": "other.py"})

    def test_live_side_effect_tool_forbidden(self):
        registry = ReplayToolRegistry(self.snapshot, self.live, mode="live")
        from evoagent.runtime import AgentLoopProtocolError
        with self.assertRaises(AgentLoopProtocolError):
            registry.invoke("push_fix", {})

    def test_live_read_only_reinvokes(self):
        registry = ReplayToolRegistry(self.snapshot, self.live, mode="live")
        result = registry.invoke("read_file", {"path": "b.py"})
        self.assertEqual(result, {"echo": {"path": "b.py"}})


class ReplayDeterminismTest(unittest.TestCase):
    def test_same_snapshot_same_output(self):
        from evoagent.replay.runner import ReplayRunner

        def stepper(state):
            obs = state["observations"]
            if not obs:
                return {"action": "tool", "tool": "read_file", "arguments": {"path": "a.py"}}
            return {"action": "final", "findings": obs}

        snapshot = ReplaySnapshot(tool_observations=[
            {"fingerprint": "read_file#" + hash_args({"path": "a.py"}), "observation": "DATA"},
        ])
        runner = ReplayRunner()
        first = runner.run(snapshot, stepper, mode="deterministic")
        second = runner.run(snapshot, stepper, mode="deterministic")
        self.assertEqual(first["observations"], second["observations"])
        self.assertEqual(first["output"], second["output"])


if __name__ == "__main__":
    unittest.main()