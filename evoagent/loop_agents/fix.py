"""Fix Agent (plan §2.6, §16) -- genuine patch strategy replan.

The loop is *observation-driven*: each observed outcome selects the next
strategy instead of blindly walking a counter.  Strategies never repeat after
they fail:

    generate_deterministic_patch
      -> compile fail?            -> generate_ast_patch
      -> compile ok -> test fail? -> generate_ast_patch
      -> test ok                  -> Final
    generate_ast_patch
      -> compile fail?            -> generate_model_assisted_patch
      -> compile ok -> test fail? -> generate_model_assisted_patch
      -> test ok                  -> Final
    generate_model_assisted_patch
      -> compile / test fail?     -> abort (exhausted)
      -> ok                       -> Final
"""
from typing import Any, Dict, List, Optional

from .base import BaseLoopAgent
from .stepper import (
    PlanTracker, final_action, observations, tool_action, tool_results,
)

# ordered ladder of patch strategies -- a strategy is never retried after it
# has produced a patch (its observation already exists).
_GENERATORS: List[str] = [
    "generate_deterministic_patch",
    "generate_ast_patch",
    "generate_model_assisted_patch",
]

_DANGEROUS_TTL = ("disable validation", "ignore error", "catch all")


def _patch_of(results: List[Dict[str, Any]]) -> str:
    for result in reversed(results):
        patch = result.get("patch")
        if isinstance(patch, str):
            return patch
    return ""


def _used_generators(state: Dict[str, Any]) -> List[str]:
    return [g for g in _GENERATORS if tool_results(state, g)]


def _active_generator(state: Dict[str, Any]) -> Optional[str]:
    used = _used_generators(state)
    return used[-1] if used else None


def _next_generator(state: Dict[str, Any]) -> Optional[str]:
    used = set(_used_generators(state))
    return next((g for g in _GENERATORS if g not in used), None)


def _last_result(state: Dict[str, Any], tool: str) -> Optional[Dict[str, Any]]:
    results = tool_results(state, tool)
    return results[-1] if results else None


def choose_fix_tool(state: Dict[str, Any],
                    finding: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the next ``{tool, args}`` selected by the last observation."""
    obs = observations(state)
    # Nothing observed yet -> start the ladder.
    if not obs:
        return {"tool": "generate_deterministic_patch", "args": {"finding": finding}}

    active = _active_generator(state)
    last_name = obs[-1].get("tool")

    # A generator just produced a patch -> gate it with the compile step.
    if last_name in _GENERATORS:
        return {"tool": "compile_patch", "args": {"patch": _patch_of(
            tool_results(state, last_name))}}

    # Compile gate observed -> route on compile_ok.
    if last_name == "compile_patch":
        compile_result = _last_result(state, "compile_patch")
        compile_ok = bool(compile_result and compile_result.get("compile_ok"))
        if compile_ok:
            patch = _patch_of(tool_results(state, active)) if active else ""
            return {"tool": "run_patch_tests", "args": {"patch": patch}}
        # compile failed -> replan to the next strategy (never repeat).
        nxt = _next_generator(state)
        if nxt is None:
            return None  # exhausted -> abort
        return {"tool": nxt, "args": {"finding": finding}}

    # Test gate observed -> route on pass/fail.
    if last_name == "run_patch_tests":
        test_result = _last_result(state, "run_patch_tests")
        passed = bool(test_result is None or test_result.get("passed", True))
        if passed:
            return None  # verified -> final
        nxt = _next_generator(state)
        if nxt is None:
            return None  # exhausted -> abort
        return {"tool": nxt, "args": {"finding": finding}}

    # Unknown / stale tail -> restart the ladder for a fresh patch.
    if active is None:
        return {"tool": "generate_deterministic_patch", "args": {"finding": finding}}
    return {"tool": "compile_patch", "args": {"patch": _patch_of(
        tool_results(state, active))}}


class FixAgent(BaseLoopAgent):
    agent_id = "fix-agent"
    capabilities = ("patch-generation", "repair-verification", "safe-fix")
    task_type = "fix.generate"
    artifact_type = "fix-patch"
    tool_allowlist = (
        "inspect_diff", "generate_deterministic_patch", "generate_ast_patch",
        "generate_model_assisted_patch", "compile_patch", "run_patch_tests",
        "inspect_patch_diff", "measure_patch_scope",
    )

    def build_initial_state(self, task: Dict[str, Any]) -> Dict[str, Any]:
        state = dict(task)
        findings = list(state.get("findings")
                        or (task.get("input") or {}).get("findings") or [])
        state["findings"] = findings
        state["finding"] = findings[0] if findings else {}
        primary = state["finding"]
        state["stale_input"] = bool(
            primary.get("verification_artifact_id") and (
                int(primary.get("finding_version", 1))
                != int(primary.get("latest_finding_version", 1))
                or int(primary.get("verification_version", 0)) <= 0
            )
        )
        state["objective"] = str(task.get("objective") or "generate a verified repair")
        state["plan"] = None
        return state

    @staticmethod
    def _safe(finding: Dict[str, Any]) -> bool:
        text = str(finding.get("fix") or "").lower()
        return not any(token in text for token in _DANGEROUS_TTL)

    def agent_step(self, state: Dict[str, Any]) -> Dict[str, Any]:
        self._last_state = state
        finding = state.get("finding") or {}
        plan = PlanTracker(
            state, str(state.get("objective")),
            ["generate patch", "compile", "test / replan"], confidence=0.8)

        if state.get("stale_input"):
            return final_action(
                agent_id=self.agent_id, rejected=True,
                reason_code="FIX_STALE_INPUT", verified=False)
        decision = choose_fix_tool(state, finding)
        if decision is None:
            active = _active_generator(state) or ""
            used = _used_generators(state)
            plan.complete("test / replan")
            reached_test = any(tool_results(state, "run_patch_tests"))
            return final_action(
                patch=_patch_of(tool_results(state, active)) if active else "",
                generator=active,
                strategies_tried=used,
                replanned=len(used) > 1,
                verified=bool(reached_test and (
                    _last_result(state, "run_patch_tests") or {}
                ).get("passed", True)),
                exhausted=len(used) >= len(_GENERATORS),
                agent_id=self.agent_id)
        plan.begin("tool:" + decision["tool"])
        return tool_action(decision["tool"], dict(decision["args"]))

    def build_artifact(self, result) -> Dict[str, Any]:
        state = {"observations": list(result.observations)}
        task = self._last_task
        findings = list(task.get("findings")
                        or (task.get("input") or {}).get("findings") or [])
        primary = findings[0] if findings else {}

        used = [g for g in _GENERATORS if tool_results(state, g)]
        active = used[-1] if used else ""
        final_patch = _patch_of(tool_results(state, active)) if active else ""

        compiles = tool_results(state, "compile_patch")
        compiled_ok = all(item.get("compile_ok") for item in compiles) if compiles else False
        tests = tool_results(state, "run_patch_tests")
        tests_passed = all(item.get("passed", True) for item in tests) if tests else True

        changed_files = []
        for gen in _GENERATORS:
            for result_ in tool_results(state, gen):
                for path in result_.get("changed_files", []):
                    if path not in changed_files:
                        changed_files.append(path)

        return {
            "task_type": self.task_type,
            "agent_id": self.agent_id,
            "patch": final_patch,
            "changed_files": changed_files,
            "generator": active,
            "strategies_tried": used,
            "replanned": len(used) > 1,
            "patch_strategy_count": len(used),
            "verification": "verified" if (compiled_ok and tests_passed)
            else "unverified",
            "test_results": {"passed": tests_passed, "run": len(tests)},
            "risk_summary": "low" if self._safe(primary) else "medium",
            "target_rule_id": primary.get("rule_id", ""),
            "finding_id": primary.get("finding_id", ""),
            "finding_version": int(primary.get("finding_version", 1)),
            "verification_artifact_id": primary.get(
                "verification_artifact_id", ""),
            "verification_version": int(primary.get("verification_version", 0)),
            "stale_input_rejected": bool((getattr(self, "_last_state", {}) or {}).get(
                "stale_input")),
        }


__all__ = ["FixAgent", "choose_fix_tool"]
