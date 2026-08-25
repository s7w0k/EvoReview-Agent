import unittest

from evoagent.recovery.classifier import FailureClassifier
from evoagent.recovery.failures import FailureType


class FailureClassifierTest(unittest.TestCase):
    def setUp(self):
        self.classifier = FailureClassifier()

    def test_model_timeout(self):
        self.assertEqual(
            self.classifier.classify(message="request timed out", context={"node": "model_call"}),
            FailureType.MODEL_TIMEOUT,
        )

    def test_tool_timeout(self):
        self.assertEqual(
            self.classifier.classify(message="timed out", context={"node": "tool"}),
            FailureType.TOOL_TIMEOUT,
        )

    def test_rate_limit(self):
        self.assertEqual(
            self.classifier.classify(message="rate limit exceeded"),
            FailureType.MODEL_RATE_LIMIT,
        )

    def test_permission_denied(self):
        self.assertEqual(
            self.classifier.classify(message="permission denied"),
            FailureType.TOOL_PERMISSION_DENIED,
        )

    def test_side_effect_unknown(self):
        self.assertEqual(
            self.classifier.classify(message="unknown state", context={"side_effect_unknown": True}),
            FailureType.TOOL_SIDE_EFFECT_UNKNOWN,
        )

    def test_context_overflow(self):
        self.assertEqual(
            self.classifier.classify(exc=MemoryError("ctx")),
            FailureType.MODEL_CONTEXT_OVERFLOW,
        )

    def test_budget_exceeded(self):
        self.assertEqual(
            self.classifier.classify(message="step budget exceeded"),
            FailureType.BUDGET_EXCEEDED,
        )

    def test_unknown(self):
        self.assertEqual(
            self.classifier.classify(exc=ValueError("mystery")),
            FailureType.UNKNOWN,
        )


if __name__ == "__main__":
    unittest.main()