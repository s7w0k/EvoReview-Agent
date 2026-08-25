import unittest

from evoagent.recovery.no_progress import NoProgressDetector


class NoProgressTest(unittest.TestCase):
    def setUp(self):
        self.detector = NoProgressDetector(window=5, max_duplicates=3)

    def test_repeated_same_tool_detected(self):
        actions = [
            {"action": "tool", "tool": "search_code", "arguments": {"query": "auth"}},
        ] * 3
        self.assertTrue(self.detector.detect(actions))

    def test_repeated_same_tool_different_args_not_flagged(self):
        actions = [
            {"action": "tool", "tool": "search_code", "arguments": {"query": "a"}},
            {"action": "tool", "tool": "search_code", "arguments": {"query": "b"}},
            {"action": "tool", "tool": "search_code", "arguments": {"query": "c"}},
        ]
        self.assertFalse(self.detector.detect(actions))

    def test_two_cycle_detected(self):
        actions = [
            {"action": "tool", "tool": "A", "arguments": {}},
            {"action": "tool", "tool": "B", "arguments": {}},
            {"action": "tool", "tool": "A", "arguments": {}},
            {"action": "tool", "tool": "B", "arguments": {}},
        ]
        self.assertTrue(self.detector.detect(actions))

    def test_progress_like_sequence_not_detected(self):
        actions = [
            {"action": "tool", "tool": "read_file", "arguments": {}},
            {"action": "tool", "tool": "find_callers", "arguments": {}},
            {"action": "final", "output": "done"},
        ]
        self.assertFalse(self.detector.detect(actions))


if __name__ == "__main__":
    unittest.main()