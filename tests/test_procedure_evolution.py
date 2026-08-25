"""Tests for procedure skill evolution lifecycle (Section 8.5/8.6/8.7).

Focused on verifying: candidates must pass schema + static validation + a
generalization/replay gate before they become ACTIVE; a failing candidate never
gets activated; rollback restores a prior ACTIVE version.
"""
import unittest

from evoagent.procedure.executor import ProcedureExecutor
from evoagent.procedure.parser import parse_procedure
from evoagent.procedure.registry import (
    ProcedureNotActive,
    ProcedureRegistry,
    ProcedureSkillConflict,
    SkillStatus,
)
from evoagent.procedure.validator import ProcedureValidator


def candidate(name="c", procedure=None, budget=None, version=1):
    return parse_procedure({
        "name": name,
        "version": version,
        "procedure": procedure or [{"tool": "t", "args": {}}],
        "budget": budget or {"max_steps": 6, "max_tool_calls": 8},
    })


class ProcedureEvolutionFeatureTest(unittest.TestCase):
    """High-level: a candidate must survive all gates before activation."""

    def test_candidate_pipeline_to_activate(self):
        reg = ProcedureRegistry()
        skill = candidate()
        reg.register(skill)
        reg.validate("c", 1)
        reg.shadow("c", 1)
        reg.activate("c", 1)

        self.assertIs(reg.active("c").status, SkillStatus.ACTIVE)
        self.assertIsNotNone(reg.active_skill("c"))

    def test_failing_candidate_is_rejected_not_activated(self):
        reg = ProcedureRegistry()
        reg.register(candidate())
        # A candidate whose procedure references a tool outside the allow-list
        # is statically unsafe -> must be rejected, never activated.
        unsafe = candidate(procedure=[
            {"tool": "delete_all", "args": {}},
        ], version=2)
        result = ProcedureValidator(allowed_tools=["t"]).validate(unsafe)
        self.assertFalse(result.valid)

        reg.register(unsafe, parent_version=1)
        with self.assertRaises(ProcedureSkillConflict):
            # A failed candidate may not be promoted straight to ACTIVE.
            reg.activate("c", 2)
        reg.reject("c", 2)
        self.assertIs(reg.get("c", 2).status, SkillStatus.REJECTED)

    def test_rejected_candidate_cannot_be_activated(self):
        reg = ProcedureRegistry()
        reg.register(candidate())
        reg.register(candidate(budget={"max_steps": 3, "max_tool_calls": 8},
                               version=2),
                     parent_version=1)
        reg.reject("c", 2)
        with self.assertRaises(ProcedureSkillConflict):
            reg.activate("c", 2)

    def test_rollback_restores_previous_active(self):
        reg = ProcedureRegistry()
        v1 = candidate(name="n", budget={"max_steps": 1, "max_tool_calls": 1})
        reg.register(v1)
        reg.validate("n", 1)
        reg.shadow("n", 1)
        reg.activate("n", 1)

        v2 = candidate(name="n", budget={"max_steps": 9, "max_tool_calls": 9},
                       version=2)
        reg.register(v2, parent_version=1)
        reg.validate("n", 2)
        reg.shadow("n", 2)
        reg.activate("n", 2)
        self.assertEqual(reg.active("n").version, 2)

        restored = reg.rollback("n")
        self.assertEqual(restored.version, 1)
        self.assertEqual(reg.active("n").version, 1)
        self.assertIs(reg.get("n", 2).status, SkillStatus.ROLLED_BACK)

    def test_rollback_without_previous_raises(self):
        reg = ProcedureRegistry()
        reg.register(candidate(name="only"))
        reg.validate("only", 1)
        reg.shadow("only", 1)
        reg.activate("only", 1)
        with self.assertRaises(ProcedureSkillConflict):
            reg.rollback("only")

    def test_no_active_skill_raises(self):
        reg = ProcedureRegistry()
        with self.assertRaises(ProcedureNotActive):
            reg.active_skill("missing")


class ProcedureEvolutesValidCandidateTest(unittest.TestCase):
    """End-to-end: a repeated, reusable workflow becomes an active skill."""

    def test_reusable_workflow_executes_as_skill(self):
        workflow = candidate(procedure=[
            {"tool": "search_code", "args": {"query": "authorization"},
             "result_var": "hits"},
            {"check": "permission_guard_exists"},
        ])
        # 1. static safety
        valid = ProcedureValidator(allowed_tools=["search_code"]).validate(workflow)
        self.assertTrue(valid.valid)
        # 2. gate
        reg = ProcedureRegistry()
        reg.register(workflow)
        reg.validate("c", 1)
        reg.shadow("c", 1)
        # 3. replay-style validation decider
        ok = self._replay_verdict(workflow)
        self.assertTrue(ok)
        # 4. activate
        reg.activate("c", 1)
        self.assertIs(reg.active("c").status, SkillStatus.ACTIVE)

    def _replay_verdict(self, skill):
        recorded = []
        invoker = self._recording_invoker(recorded)
        result = ProcedureExecutor(invoker).execute(skill)
        return result.complete and result.tool_calls == 1

    @staticmethod
    def _recording_invoker(recorded):
        def invoker(name, args):
            recorded.append((name, args))
            return {"found": 1}
        return invoker


if __name__ == "__main__":
    unittest.main()