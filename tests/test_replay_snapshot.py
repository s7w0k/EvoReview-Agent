import unittest

from evoagent.replay.models import ReplaySnapshot
from evoagent.replay.snapshot import Counterfactual, SnapshotStore


class ReplaySnapshotTest(unittest.TestCase):
    def test_round_trip_and_lookup(self):
        snapshot = ReplaySnapshot(
            task_id="t1", repository="repo", diff_hash="abc",
            prompt_version="v1", policy_version="p1",
            tool_observations=[{
                "fingerprint": "read_file#deadbeef", "observation": "file: hello",
            }],
        )
        restored = ReplaySnapshot.from_dict(snapshot.to_dict())
        self.assertEqual(restored.snapshot_id, snapshot.snapshot_id)
        self.assertEqual(restored.repository, "repo")

    def test_counterfactual_substitutes_one_variable(self):
        store = SnapshotStore()
        base = ReplaySnapshot(
            task_id="t1", diff_hash="abc", prompt_version="v10",
            skill_versions={"auth": "2"}, policy_version="p1",
            model_name="m1",
            tool_observations=[{"fingerprint": "x#y", "observation": 1}],
        )
        store.save(base)
        derived = Counterfactual(store).substitute(base.snapshot_id, prompt_version="v12")
        self.assertEqual(derived.prompt_version, "v12")
        self.assertEqual(derived.policy_version, "p1")  # unchanged
        self.assertEqual(derived.model_name, "m1")

    def test_counterfactual_unknown_key(self):
        from evoagent.replay.snapshot import Counterfactual as CF
        with self.assertRaises(KeyError):
            CF(SnapshotStore()).substitute("missing", policy_version="x")


if __name__ == "__main__":
    unittest.main()