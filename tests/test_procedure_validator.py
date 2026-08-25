"""Tests for the procedure static validator (Section 8.3/8.4 of the plan).

Key guarantee to prove: a procedure skill cannot encode arbitrary code and can
only use authorised tools.
"""
import unittest

from evoagent.procedure.parser import parse_procedure
from evoagent.procedure.validator import ProcedureValidator


def skill(procedure=None, budget=None, checks=None):
    return parse_procedure({
        "name": "s",
        "procedure": procedure or [{"tool": "search_code", "args": {}}],
        "budget": budget or {"max_steps": 6, "max_tool_calls": 8},
    })


class ProcedureValidatorTest(unittest.TestCase):

    def test_valid_skill_passes(self):
        v = ProcedureValidator(allowed_tools=["search_code"])
        result = v.validate(skill())
        self.assertTrue(result.valid)
        self.assertEqual(result.errors, [])

    def test_detects_dynamic_import(self):
        v = ProcedureValidator(allowed_tools=["t"])
        result = v.validate(skill(
            procedure=[{"tool": "t", "args": {"code": "import os"}}]))
        self.assertFalse(result.valid)
        self.assertTrue(any("import os" in i.message.lower()
                            or "import" in i.message.lower()
                            for i in result.errors))

    def test_detects_eval_exec_shell(self):
        v = ProcedureValidator(allowed_tools=["t"])
        for forbidden in ("eval(", "exec(", "subprocess", "os.system",
                          "socket", "http://", "shell"):
            result = v.validate(skill(
                procedure=[{"tool": "t", "args": {"payload": forbidden}}]))
            self.assertFalse(result.valid, forbidden)

    def test_rejects_not_authorised_tool(self):
        v = ProcedureValidator(allowed_tools=["search_code"])
        result = v.validate(skill(
            procedure=[{"tool": "delete_all"}]))
        self.assertFalse(result.valid)
        self.assertTrue(any("delete_all" in i.message for i in result.errors))

    def test_denied_tool_always_rejected(self):
        v = ProcedureValidator(denied_tools=["rm_rf"])
        result = v.validate(skill(procedure=[{"tool": "rm_rf"}]))
        self.assertFalse(result.valid)

    def test_rejects_unknown_check(self):
        v = ProcedureValidator(
            allowed_tools=["t"], allowed_checks=["known_check"])
        result = v.validate(skill(
            procedure=[
                {"tool": "t", "args": {}},
                {"check": "unknown_check"},
            ]))
        self.assertFalse(result.valid)
        self.assertTrue(any("unknown_check" in i.message for i in result.errors))

    def test_budget_strict(self):
        # Three tool calls on a budget of two tool calls must fail statically.
        v = ProcedureValidator(allowed_tools=["t"])
        result = v.validate(skill(
            procedure=[{"tool": "t", "args": {}}] * 3,
            budget={"max_steps": 6, "max_tool_calls": 2},
        ))
        self.assertFalse(result.valid)

    def test_step_count_exceeds_max_steps(self):
        v = ProcedureValidator(allowed_tools=["t"])
        result = v.validate(skill(
            procedure=[{"tool": "t", "args": {}}] * 5,
            budget={"max_steps": 3, "max_tool_calls": 8},
        ))
        self.assertFalse(result.valid)

    def test_previous_reference_without_prior_output(self):
        v = ProcedureValidator(allowed_tools=["t"])
        result = v.validate(skill(procedure=[
            {"tool": "t", "args": {"sym": "previous"}},
        ]))
        self.assertFalse(result.valid)
        self.assertTrue(any("previous" in i.message for i in result.errors))

    def test_forward_symbol_rejected(self):
        v = ProcedureValidator(allowed_tools=["t"])
        result = v.validate(skill(procedure=[
            {"tool": "t", "args": {"ref": "{later_var}"}},
            {"tool": "t", "args": {}, "result_var": "later_var"},
        ]))
        self.assertFalse(result.valid)


if __name__ == "__main__":
    unittest.main()