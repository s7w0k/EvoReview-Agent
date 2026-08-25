"""Phase 6 acceptance tests: Procedure skills truly auto-evolve.

Covers:
  * executor ``on_failure`` abort / continue wiring + run ``status``
  * production trace mining (min_support / success_rate / verification_pass)
  * synthesizer constrained to the allow-list -> valid DSL candidate
  * strict candidate lifecycle (no skipping evaluation gates)
"""
import unittest

from evoagent.procedure.executor import ProcedureExecutor, ProcedureRunResult
from evoagent.procedure.lifecycle import (
    CandidateStatus,
    CandidateTransitionError,
    ProcedureCandidate,
    ProcedureCandidateLifecycle,
)
from evoagent.procedure.miner import ProcedureMiner, TraceRecord
from evoagent.procedure.schema import ProcedureSkill, ProceduralStep
from evoagent.procedure.synthesizer import ProcedureSynthesizer


def make_skill(name="s", procedure=None):
    proc = procedure or [
        ProceduralStep(kind="tool", tool="search_code", args={}, result_var="r0"),
        ProceduralStep(kind="tool", tool="find_callers", args={}),
    ]
    return ProcedureSkill(
        name=name,
        procedure=proc,
        budget=__import__("evoagent.procedure.schema", fromlist=["ProcedureBudget"])
        .ProcedureBudget(max_steps=4, max_tool_calls=6),
    )


class ExecutorOnFailureTest(unittest.TestCase):
    def _skill(self, steps):
        from evoagent.procedure.schema import ProcedureBudget
        return ProcedureSkill(
            name="x",
            procedure=steps,
            budget=ProcedureBudget(max_steps=4, max_tool_calls=6),
        )

    def test_continue_keeps_running_and_marks_partial(self):
        calls = []
        def invoker(name, args):
            calls.append(name)
            if name == "fail_tool":
                raise RuntimeError("oops")
            return {"ok": True}

        steps = [
            ProceduralStep(kind="tool", tool="fail_tool", args={},
                           on_failure="continue"),
            ProceduralStep(kind="tool", tool="a", args={}),
        ]
        result = ProcedureExecutor(invoker).execute(self._skill(steps))
        self.assertTrue(result.complete)
        self.assertEqual(result.status, "PARTIAL")
        self.assertEqual(calls, ["fail_tool", "a"])
        self.assertEqual(result.steps_executed, 2)

    def test_abort_stops_remaining_and_marks_failed(self):
        calls = []
        def invoker(name, args):
            calls.append(name)
            if name == "fail_tool":
                raise RuntimeError("oops")
            return {"ok": True}

        steps = [
            ProceduralStep(kind="tool", tool="fail_tool", args={},
                           on_failure="abort"),
            ProceduralStep(kind="tool", tool="a", args={}),
        ]
        result = ProcedureExecutor(invoker).execute(self._skill(steps))
        self.assertFalse(result.complete)
        self.assertEqual(result.status, "FAILED")
        self.assertEqual(calls, ["fail_tool"])  # remaining step must not run
        self.assertEqual(result.steps_executed, 1)

    def test_success_status_when_all_steps_ok(self):
        def invoker(name, args):
            return {"ok": True}
        result = ProcedureExecutor(invoker).execute(self._skill(make_skill().procedure))
        self.assertTrue(result.complete)
        self.assertEqual(result.status, "SUCCESS")

    def test_run_result_to_dict_includes_status(self):
        result = ProcedureRunResult(skill_name="s", status="FAILED")
        self.assertEqual(result.to_dict()["status"], "FAILED")


class MinerTest(unittest.TestCase):
    def test_qualified_pattern_is_mined(self):
        records = [
            TraceRecord("auth", "high", ["search_code", "find_callers", "read_file"], outcome="accepted", verification=True)
            for _ in range(5)
        ]
        sources = ProcedureMiner().mine(records, task_type="auth")
        self.assertEqual(len(sources), 1)
        pattern = sources[0].pattern
        self.assertEqual(list(pattern.tool_path),
                         ["search_code", "find_callers", "read_file"])
        self.assertGreaterEqual(pattern.support, 5)
        self.assertGreaterEqual(pattern.success_rate, 0.8)
        self.assertGreaterEqual(pattern.verification_rate, 0.8)

    def test_low_support_is_not_mined(self):
        records = [
            TraceRecord("auth", "high", ["search_code"], outcome="accepted", verification=True)
            for _ in range(2)
        ]
        self.assertEqual(ProcedureMiner().mine(records), [])

    def test_low_verification_is_not_mined(self):
        records = [
            TraceRecord("auth", "high", ["search_code"], outcome="accepted", verification=False)
            for _ in range(6)
        ]
        self.assertEqual(ProcedureMiner().mine(records), [])

    def test_filters_by_risk_type(self):
        records = [
            TraceRecord("auth", "high", ["search_code"], outcome="accepted", verification=True)
            for _ in range(6)
        ] + [
            TraceRecord("auth", "low", ["search_code"], outcome="accepted", verification=True)
            for _ in range(6)
        ]
        sources = ProcedureMiner().mine(records, risk_type="high")
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].pattern.risk_type, "high")


class SynthesizerTest(unittest.TestCase):
    def _source(self):
        return ProcedureMiner().mine([
            TraceRecord("auth", "high", ["search_code", "find_callers"],
                        outcome="accepted", verification=True)
            for _ in range(6)
        ])[0]

    def test_synthesizes_valid_dsl_candidate(self):
        source = self._source()
        synth = ProcedureSynthesizer(
            allowed_tools=["search_code", "find_callers"],
            allowed_checks=[],
        )
        result = synth.synthesize(source.pattern, hypothesis_id="hyp-1")
        self.assertTrue(result.valid)
        self.assertEqual(result.skill.metadata["hypothesis_id"], "hyp-1")
        self.assertEqual(result.skill.tool_names(), ["search_code", "find_callers"])

    def test_rejects_out_of_allowlist_tool(self):
        source = ProcedureMiner().mine([
            TraceRecord("auth", "high", ["search_code", "rm"], outcome="accepted", verification=True)
            for _ in range(6)
        ])[0]
        synth = ProcedureSynthesizer(allowed_tools=["search_code"])
        with self.assertRaises(ValueError):
            synth.synthesize(source.pattern)


class LifecycleTest(unittest.TestCase):
    def test_strict_order_reaches_active(self):
        candidate = ProcedureCandidate(skill=make_skill())
        lc = ProcedureCandidateLifecycle(candidate, hypothesis_id="h1")
        self.assertIs(lc.status, CandidateStatus.DRAFT)
        lc.static_validate()
        lc.replay_pass()
        lc.holdout_pass()
        lc.enter_shadow()
        lc.enter_canary()
        lc.activate()
        self.assertIs(lc.status, CandidateStatus.ACTIVE)
        self.assertEqual(len(candidate.history), 6)

    def test_skipping_gate_is_forbidden(self):
        candidate = ProcedureCandidate(skill=make_skill())
        lc = ProcedureCandidateLifecycle(candidate)
        lc.static_validate()
        # STATIC_VALIDATED -> ACTIVE must be rejected: evaluation gates skipped.
        with self.assertRaises(CandidateTransitionError):
            lc.activate()
        with self.assertRaises(CandidateTransitionError):
            lc.static_validate().holdout_pass()  # replay gate skipped

    def test_rejected_cannot_reach_active(self):
        candidate = ProcedureCandidate(skill=make_skill())
        lc = ProcedureCandidateLifecycle(candidate)
        lc.static_validate()
        lc.reject()
        self.assertIs(lc.status, CandidateStatus.REJECTED)
        with self.assertRaises(CandidateTransitionError):
            lc.replay_pass()

    def test_rollback_from_active(self):
        candidate = ProcedureCandidate(skill=make_skill())
        lc = ProcedureCandidateLifecycle(candidate)
        lc.static_validate(); lc.replay_pass(); lc.holdout_pass()
        lc.enter_shadow(); lc.enter_canary(); lc.activate()
        lc.rollback()
        self.assertIs(lc.status, CandidateStatus.ROLLED_BACK)


if __name__ == "__main__":
    unittest.main()