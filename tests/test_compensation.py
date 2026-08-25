import unittest

from evoagent.recovery.compensation import CompensationHandler


class CompensationTest(unittest.TestCase):
    def test_register_and_compensate(self):
        handler = CompensationHandler()
        calls = []

        def rollback(arguments):
            calls.append(arguments)
            return "undone"

        handler.register("create_comment", rollback)
        self.assertTrue(handler.compensates("create_comment"))
        self.assertEqual(handler.compensate("create_comment", {"id": 5}), "undone")
        self.assertEqual(calls, [{"id": 5}])

    def test_missing_handler_raises(self):
        handler = CompensationHandler()
        with self.assertRaises(KeyError):
            handler.compensate("missing", {})


if __name__ == "__main__":
    unittest.main()