"""Tests tying procedure budget enforcement to static + runtime validation."""
import unittest

from evoagent.procedure.executor import ProcedureExecutor
from evoagent.procedure.parser import parse_procedure
from evoagent.procedure.validator import ProcedureValidator


def build(procedure, budget):
    return parse_procedure({
        "name": "s",
        "procedure": procedure,
        "budget": budget,
    })


class ProcedureBudgetTest(unittest.TestCase):

    def test_static_budget_blocks_at_parse_time(self):
        with self.assertRaises(ValueError):
            parse_procedure({
                "name": "s",
                "procedure": [{"tool": "t", "args": {}}],
                "budget": {"max_steps": 0},
            })

    def test_runtime_enforces_max_tool_calls(self):
        skill = build(
            [{"tool": "t", "args": {}}, {"tool": "t", "args": {}}, {"tool": "t", "args": {}}],
            {"max_steps": 6, "max_tool_calls": 2},
        )
        with self.assertRaises(Exception) as ctx:
            ProcedureExecutor(lambda n, a: None).execute(skill)
        self.assertIn("tool_calls", str(ctx.exception))

    def test_runtime_enforces_max_steps(self):
        skill = build(
            [{"tool": "t", "args": {}}, {"tool": "t", "args": {}}, {"tool": "t", "args": {}}],
            {"max_steps": 2, "max_tool_calls": 8},
        )
        with self.assertRaises(Exception) as ctx:
            ProcedureExecutor(lambda n, a: None).execute(skill)
        self.assertIn("steps", str(ctx.exception))

    def test_validation_and_execution_agree_on_limits(self):
        skill = build(
            [{"tool": "t", "args": {}}] * 4,
            {"max_steps": 10, "max_tool_calls": 10},
        )
        valid = ProcedureValidator(allowed_tools=["t"]).validate(skill)
        self.assertTrue(valid.valid)
        result = ProcedureExecutor(lambda n, a: None).execute(skill)
        self.assertEqual(result.tool_calls, 4)


if __name__ == "__main__":
    unittest.main()