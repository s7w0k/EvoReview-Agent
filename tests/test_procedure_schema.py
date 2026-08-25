"""Tests for procedure skill schema + parser (Section 8.2/8.4 of the plan)."""
import unittest

from evoagent.procedure.schema import (
    ProceduralStep,
    ProcedureBudget,
    ProcedureSkill,
    ProcedureTrigger,
)
from evoagent.procedure.parser import (
    parse_json,
    parse_procedure,
    parse_step,
)


def sample_skill():
    return parse_procedure({
        "name": "auth-bypass-review",
        "version": 3,
        "trigger": {
            "paths": ["auth/**", "security/**"],
            "keywords": ["authentication", "permission", "token"],
            "risk_level": ["medium", "high"],
        },
        "procedure": [
            {"kind": "tool", "tool": "search_code",
             "args": {"query": "authorization"}, "result_var": "hits"},
            {"kind": "check", "check": "permission_guard_exists"},
            {"kind": "tool", "tool": "find_tests",
             "args": {"symbol_from": "previous"}},
        ],
        "required_evidence": ["source", "security_guard", "reachable_sink"],
        "budget": {"max_steps": 6, "max_tool_calls": 8},
    })


class ProcedureSchemaRoundTripTest(unittest.TestCase):

    def test_to_dict_from_dict_round_trip(self):
        skill = sample_skill()
        restored = ProcedureSkill.from_dict(skill.to_dict())
        self.assertEqual(restored.name, "auth-bypass-review")
        self.assertEqual(restored.version, 3)
        self.assertEqual(restored.trigger.paths, ["auth/**", "security/**"])
        self.assertEqual(restored.trigger.risk_level, ["medium", "high"])
        self.assertEqual(len(restored.procedure), 3)
        self.assertEqual(restored.budget.max_steps, 6)
        self.assertEqual(restored.budget.max_tool_calls, 8)
        self.assertEqual(restored.required_evidence,
                         ["source", "security_guard", "reachable_sink"])
        self.assertEqual(restored.to_dict(), skill.to_dict())

    def test_tool_and_check_names(self):
        skill = sample_skill()
        self.assertEqual(skill.tool_names(), ["search_code", "find_tests"])
        self.assertEqual(skill.check_names(), ["permission_guard_exists"])

    def test_parse_step_normalises_kind(self):
        step = parse_step({"tool": "search_code", "args": {"query": "auth"}})
        self.assertEqual(step.kind, "tool")
        self.assertEqual(step.tool, "search_code")
        bad = parse_step({"kind": ""})
        self.assertEqual(bad.kind, "tool")

    def test_parse_step_rejects_unknown_kind(self):
        with self.assertRaises(ValueError):
            parse_step({"kind": "run_python"})

    def test_parse_json(self):
        skill = parse_json(
            '{"name": "a", "procedure": [{"tool": "t"}]}')
        self.assertEqual(skill.name, "a")
        self.assertEqual(skill.procedure[0].tool, "t")

    def test_parse_procedure_requires_name(self):
        with self.assertRaises(ValueError):
            parse_procedure({"procedure": []})

    def test_default_trigger_and_budget(self):
        skill = parse_procedure({"name": "x"})
        self.assertEqual(skill.trigger.paths, [])
        self.assertEqual(skill.budget.max_steps, 6)


if __name__ == "__main__":
    unittest.main()