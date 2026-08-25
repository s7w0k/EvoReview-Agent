"""Turn a hypothesis + qualified pattern into a Procedure DSL candidate.

The synthesizer (plan section 10.4) is deliberately *not* a code generator.
Given a hypothesis and a mined ``CandidateSource`` it has to choose only from:

    * registered tools (validated against an explicit allow-list)
    * registered named checks
    * safe control fields (budget, ``on_failure``, trigger metadata)

The emitted ``ProcedureSkill`` must pass ``ProcedureValidator`` *before* it is
ever returned, so nothing unsafe can leak into a candidate from here.
"""
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .miner import ProcedurePattern
from .schema import ProceduralStep, ProcedureBudget, ProcedureSkill, ProcedureTrigger
from .validator import ProcedureValidator


@dataclass
class SynthesisResult:
    """A synthesised candidate plus the static-validation outcome."""

    skill: ProcedureSkill
    valid: bool
    issues: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill": self.skill.to_dict(),
            "valid": self.valid,
            "issues": list(self.issues),
        }


class ProcedureSynthesizer:
    """Deterministic DSL synthesizer constrained by an allow-list + budget.

    ``on_failure`` for each tool step is derived from a ``failure_policy`` hint
    (``"abort"`` / ``"continue"``) so evolved skills participate in the section
    10.6 run-status control without needing arbitrary code.
    """

    def __init__(
        self,
        allowed_tools: Optional[Iterable[str]] = None,
        allowed_checks: Optional[Sequence[str]] = None,
        budget: Optional[ProcedureBudget] = None,
        failure_policy: str = "continue",
    ):
        self._validator = ProcedureValidator(
            allowed_tools=allowed_tools, allowed_checks=allowed_checks,
        )
        self._allowed_tools = (
            None if allowed_tools is None else {str(t) for t in allowed_tools}
        )
        self._budget = budget or ProcedureBudget()
        if failure_policy not in ("abort", "continue"):
            raise ValueError(
                f"failure_policy must be 'abort' or 'continue', got {failure_policy!r}")
        self._failure_policy = failure_policy

    # -- public API ---------------------------------------------------------

    def synthesize(
        self,
        pattern: ProcedurePattern,
        *,
        hypothesis_id: Optional[str] = None,
        name: Optional[str] = None,
    ) -> SynthesisResult:
        """Build a candidate skill from a mined pattern and validate it.

        Returns a ``SynthesisResult``; the caller should only adopt the
        candidate when ``valid`` is ``True``.
        """
        skill = self._build_skill(
            pattern, hypothesis_id=hypothesis_id, name=name)
        outcome = self._validator.validate(skill)
        return SynthesisResult(
            skill=skill,
            valid=outcome.valid,
            issues=[str(issue.message)
                    for issue in outcome.issues if issue.severity == "error"],
        )

    # -- internals ----------------------------------------------------------

    def _build_skill(
        self,
        pattern: ProcedurePattern,
        *,
        hypothesis_id: Optional[str],
        name: Optional[str],
    ) -> ProcedureSkill:
        # Reject any tool in the qualified path that is not allow-listed, so
        # the synthesizer can never emit an ungoverned tool call.
        if self._allowed_tools is not None:
            for tool in pattern.tool_path:
                if tool not in self._allowed_tools:
                    raise ValueError(
                        f"tool {tool!r} in mined path is not in the allow-list")

        steps: List[ProceduralStep] = []
        for index, tool in enumerate(pattern.tool_path):
            steps.append(ProceduralStep(
                kind="tool",
                tool=tool,
                args={},
                result_var=f"r{index}" if index + 1 < len(pattern.tool_path) else "",
                on_failure=self._failure_policy,
            ))

        skill_name = name or f"evolved-{pattern.task_type}-{pattern.risk_type}"
        return ProcedureSkill(
            name=skill_name,
            trigger=ProcedureTrigger(
                keywords=[pattern.task_type.lower()],
                risk_level=[pattern.risk_type],
            ),
            procedure=steps,
            budget=ProcedureBudget(
                max_steps=len(steps) if len(steps) else 1,
                max_tool_calls=self._budget.max_tool_calls,
            ),
            version=1,
            metadata={
                "hypothesis_id": hypothesis_id,
                "mined_task_type": pattern.task_type,
                "mined_risk_type": pattern.risk_type,
                "support": pattern.support,
                "success_rate": round(pattern.success_rate, 4),
                "verification_rate": round(pattern.verification_rate, 4),
            },
        )