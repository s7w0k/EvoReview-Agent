"""Tests for the procedure step executor (Section 8.4 of the plan)."""
import unittest

from evoagent.procedure.executor import (
    ProcedureBudgetExceeded,
    ProcedureExecutor,
    ProcedureStepError,
)
from evoagent.procedure.parser import parse_procedure


def skill_with(*steps, budget=None):
    return parse_procedure({
        "name": "s",
        "procedure": list(steps),
        "budget": budget or {"max_steps": 6, "max_tool_calls": 8},
    })


def tool_step(tool, args=None, result_var=""):
    return {"tool": tool, "args": args or {}, "result_var": result_var}


class ProcedureExecutorTest(unittest.TestCase):

    def test_runs_tool_steps_and_observations(self):
        calls = []
        def invoker(name, args):
            calls.append((name, args))
            return {"ok": True}
        skill = skill_with(tool_step("search_code", {"query": "auth"}, "hits"))
        result = ProcedureExecutor(invoker).execute(skill)

        self.assertEqual(calls, [("search_code", {"query": "auth"})])
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual(len(result.observations), 1)
        self.assertEqual(result.observations[0].kind, "tool")
        self.assertTrue(result.complete)

    def test_previous_symbol_resolves_to_last_output(self):
        calls = []
        def invoker(name, args):
            calls.append(args)
            return "output-a"
        skill = skill_with(
            tool_step("a", {}, "first"),
            tool_step("b", {"sym": "previous"}),
        )
        result = ProcedureExecutor(invoker).execute(skill)
        self.assertEqual(calls[1], {"sym": "output-a"})

    def test_result_var_interpolation(self):
        calls = []
        def invoker(name, args):
            if name == "a":
                return "tok-123"
            calls.append(args)
            return None
        skill = skill_with(
            tool_step("a", {}, "token"),
            tool_step("b", {"lookup": "{token}"}),
        )
        ProcedureExecutor(invoker).execute(skill)
        self.assertEqual(calls[0], {"lookup": "tok-123"})

    def test_check_step_evaluated(self):
        verdicts = []
        def check(name):
            verdicts.append(name)
            return name == "guard_ok"
        skill = skill_with({"check": "guard_ok"})
        result = ProcedureExecutor(lambda n, a: None, check_evaluator=check).execute(skill)
        self.assertEqual(verdicts, ["guard_ok"])
        self.assertEqual(result.observations[0].kind, "check")
        self.assertEqual(result.observations[0].result, True)

    def test_tool_failure_recorded_as_error_observation(self):
        def invoker(name, args):
            raise RuntimeError("boom")
        skill = skill_with(tool_step("a", {}))
        result = ProcedureExecutor(invoker).execute(skill)
        self.assertEqual(result.tool_calls, 1)
        self.assertIsNotNone(result.observations[0].error)
        self.assertIn("boom", result.observations[0].error)

    def test_step_budget_exceeded(self):
        def invoker(name, args):
            return None
        skill = skill_with(
            tool_step("a", {}), tool_step("b", {}),
            budget={"max_steps": 1, "max_tool_calls": 8},
        )
        with self.assertRaises(ProcedureBudgetExceeded) as ctx:
            ProcedureExecutor(invoker).execute(skill)
        self.assertEqual(ctx.exception.budget_name, "steps")

    def test_tool_call_budget_exceeded(self):
        def invoker(name, args):
            return None
        skill = skill_with(
            *[tool_step("a", {}) for _ in range(3)],
            budget={"max_steps": 6, "max_tool_calls": 2},
        )
        with self.assertRaises(ProcedureBudgetExceeded) as ctx:
            ProcedureExecutor(invoker).execute(skill)
        self.assertEqual(ctx.exception.budget_name, "tool_calls")

    def test_previous_without_prior_output_raises(self):
        def invoker(name, args):
            return None
        skill = skill_with(tool_step("a", {"sym": "previous"}))
        with self.assertRaises(ProcedureStepError):
            ProcedureExecutor(invoker).execute(skill)


if __name__ == "__main__":
    unittest.main()