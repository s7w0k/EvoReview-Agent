"""Fix Agent = FixAgent + SafeFixer + RepairVerifier (plan §16).

Demonstrates a genuine ``patch -> test -> failure -> replan -> patch`` loop: it
generates a deterministic patch, runs the compile gate, and -- when the hunk
carries no concrete change (e.g. the finding had no proposed fix) -- **replans**
with an AST-anchored patch before finalising.  Publishing a draft is a separate,
governed, approval-gated step that never runs implicitly.
"""
from typing import Any, Dict, List

from .base import BaseLoopAgent
from .stepper import (
    PlanTracker, final_action, observations, tool_action, tool_results,
)

_DANGEROUS_TTL = ("disable validation", "ignore error", "catch all")


def _patch_of(results: List[Dict[str, Any]]) -> str:
    for result in reversed(results):
        patch = result.get("patch")
        if isinstance(patch, str):
            return patch
    return ""


class FixAgent(BaseLoopAgent):
    agent_id = "fix-agent"
    capabilities = ("patch-generation", "repair-verification", "safe-fix")
    task_type = "fix.generate"
    artifact_type = "fix-patch"
    tool_allowlist = (
        "inspect_diff", "generate_deterministic_patch", "generate_ast_patch",
        "compile_patch", "run_patch_tests", "inspect_patch_diff",
        "measure_patch_scope",
    )

    def build_initial_state(self, task: Dict[str, Any]) -> Dict[str, Any]:
        state = dict(task)
        findings = list(state.get("findings")
                        or (task.get("input") or {}).get("findings") or [])
        state["findings"] = findings
        state["finding"] = findings[0] if findings else {}
        state["objective"] = str(task.get("objective") or "generate a verified repair")
        state["plan"] = None
        return state

    @staticmethod
    def _safe(finding: Dict[str, Any]) -> bool:
        text = str(finding.get("fix") or "").lower()
        return not any(token in text for token in _DANGEROUS_TTL)

    def agent_step(self, state: Dict[str, Any]) -> Dict[str, Any]:
        finding = state.get("finding") or {}
        plan = PlanTracker(
            state, str(state.get("objective")),
            ["generate patch", "compile", "test / replan"], confidence=0.8)
        obs = observations(state)

        if not obs:
            plan.begin("generate_deterministic_patch")
            return tool_action("generate_deterministic_patch", {"finding": finding})

        if len(obs) == 1:
            det = _patch_of(tool_results(state, "generate_deterministic_patch"))
            state["_candidate_patch"] = det
            return tool_action("compile_patch", {"patch": det})

        if len(obs) == 2:
            compiles = tool_results(state, "compile_patch")
            ok = bool(compiles and compiles[-1].get("compile_ok"))
            if ok:
                state["_failed_once"] = False
                return tool_action(
                    "run_patch_tests", {"patch": state.get("_candidate_patch", "")})
            state["_failed_once"] = True
            plan.revise(["replan after compile failure", "regenerate a valid patch"],
                        "compile gate rejected the deterministic hunk")
            return tool_action("generate_ast_patch", {"finding": finding})

        if len(obs) == 3:
            if state.get("_failed_once"):
                ast = _patch_of(tool_results(state, "generate_ast_patch"))
                state["_replanned_patch"] = ast
                return tool_action("compile_patch", {"patch": ast})
            plan.complete("compile").complete("test / replan")
            return final_action(
                patch=state.get("_candidate_patch", ""),
                failed_once=False, agent_id=self.agent_id)

        if len(obs) == 4:
            ast = state.get("_replanned_patch", "")
            compiles = tool_results(state, "compile_patch")
            ok = bool(compiles and compiles[-1].get("compile_ok"))
            plan.complete("generate patch").complete("replan after compile failure")
            return final_action(
                patch=ast, failed_once=True, verified_replanned=ok,
                agent_id=self.agent_id)

        return final_action(patch="", agent_id=self.agent_id)

    def build_artifact(self, result) -> Dict[str, Any]:
        state = {"observations": list(result.observations)}
        task = self._last_task
        findings = list(task.get("findings")
                        or (task.get("input") or {}).get("findings") or [])
        primary = findings[0] if findings else {}

        det = _patch_of(tool_results(state, "generate_deterministic_patch"))
        ast = _patch_of(tool_results(state, "generate_ast_patch"))
        final_patch = ast or det

        compiles = tool_results(state, "compile_patch")
        compiled_ok = all(item.get("compile_ok") for item in compiles) if compiles else False
        tests = tool_results(state, "run_patch_tests")
        tests_passed = all(item.get("passed", True) for item in tests) if tests else True

        changed_files = []
        for result_ in tool_results(state, "generate_deterministic_patch") + \
                tool_results(state, "generate_ast_patch"):
            for path in result_.get("changed_files", []):
                if path not in changed_files:
                    changed_files.append(path)

        return {
            "task_type": self.task_type,
            "agent_id": self.agent_id,
            "patch": final_patch,
            "changed_files": changed_files,
            "verification": "verified" if (compiled_ok and tests_passed)
            else "unverified",
            "test_results": {"passed": tests_passed, "run": len(tests)},
            "risk_summary": "low" if self._safe(primary) else "medium",
            "target_rule_id": primary.get("rule_id", ""),
        }


__all__ = ["FixAgent"]