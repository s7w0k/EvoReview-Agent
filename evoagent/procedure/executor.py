"""Step executor for procedure skills.

Execution is a *controlled* sequence: each step is dispatched through a tool
invoker (which the harness authorises and audits) or a named-check evaluator.
No arbitrary code is ever executed.  The declared budget (max_steps /
max_tool_calls) is strictly enforced at runtime.
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .schema import ProceduralStep, ProcedureSkill

# Callables injected by the harness.
ToolInvoker = Callable[[str, Dict[str, Any]], Any]  # (name, args) -> observation
CheckEvaluator = Callable[[str], bool]  # check name -> boolean verdict


class ProcedureBudgetExceeded(Exception):
    """Raised when a procedure skill exceeds its declared runtime budget."""

    def __init__(self, budget: str, limit: int):
        # ``budget`` is "steps" or "tool_calls".
        super().__init__(f"procedure budget exceeded ({budget} > {limit})")
        self.budget_name = budget
        self.limit = limit


class ProcedureStepError(Exception):
    """Raised when a tool step fails or a referenced symbol is missing."""


@dataclass
class ProcedureObservation:
    """A single recorded observation from a procedure execution."""

    step_index: int
    kind: str
    name: str
    result: Any = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_index": self.step_index,
            "kind": self.kind,
            "name": self.name,
            "result": self.result,
            "error": self.error,
        }


@dataclass
class ProcedureRunResult:
    """Outcome of executing a ``ProcedureSkill``."""

    skill_name: str
    steps_executed: int = 0
    tool_calls: int = 0
    observations: List[ProcedureObservation] = field(default_factory=list)
    contexts: Dict[str, Any] = field(default_factory=dict)
    complete: bool = False
    status: str = "RUNNING"  # "SUCCESS" | "PARTIAL" | "FAILED" | "ABORTED"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_name": self.skill_name,
            "steps_executed": self.steps_executed,
            "tool_calls": self.tool_calls,
            "complete": self.complete,
            "status": self.status,
            "observations": [obs.to_dict() for obs in self.observations],
            "contexts": self.contexts,
        }


class ProcedureExecutor:
    """Executes a procedure skill step-by-step under a strict budget."""

    def __init__(
        self,
        tool_invoker: ToolInvoker,
        check_evaluator: Optional[CheckEvaluator] = None,
    ):
        self._invoke = tool_invoker
        self._check = check_evaluator or (lambda _name: False)

    # -- public API ---------------------------------------------------------

    def execute(self, skill: ProcedureSkill) -> ProcedureRunResult:
        """Run the skill against the injected tool invoker / check evaluator.

        Runs until the procedure is exhausted or a tool step fails with
        ``on_failure="abort"``.  The ``on_failure`` policy (plan section 10.6)
        decides whether a failed tool step halts the run (``abort``) or lets
        the remaining steps execute (``continue``).  The resulting ``status``
        is ``SUCCESS`` / ``PARTIAL`` / ``FAILED`` accordingly.
        """
        result = ProcedureRunResult(skill_name=skill.name)
        symbols: Dict[str, Any] = {}
        previous: Optional[Any] = None
        had_failure = False

        for index, step in enumerate(skill.procedure):
            self._enforce_step_budget(result, skill)
            if step.kind == "tool":
                observation, output = self._run_tool_step(
                    step, index, symbols, previous, result)
                failed = observation.error is not None
                if failed:
                    had_failure = True
                    if step.on_failure == "abort":
                        result.observations.append(observation)
                        result.steps_executed += 1
                        self._set_status(result, skill, had_failure, failed_abort=True)
                        return result
                previous = output
                if step.result_var:
                    symbols[step.result_var] = output
            else:
                observation = self._run_check_step(step, index, symbols, result)
                previous = None
            result.observations.append(observation)
            result.steps_executed += 1

        self._set_status(result, skill, had_failure, failed_abort=False)
        return result

    def _set_status(
        self,
        result: ProcedureRunResult,
        skill: ProcedureSkill,
        had_failure: bool,
        *,
        failed_abort: bool,
    ) -> None:
        """Finalise the run's completion flag and ``status`` value."""
        if failed_abort:
            # A tool step failed and its on_failure policy halted the run.
            result.complete = False
            result.status = "FAILED"
            return
        result.complete = True
        result.status = "PARTIAL" if had_failure else "SUCCESS"

    # -- internals ----------------------------------------------------------

    def _enforce_step_budget(self, result: ProcedureRunResult, skill: ProcedureSkill):
        if result.steps_executed >= skill.budget.max_steps:
            raise ProcedureBudgetExceeded("steps", skill.budget.max_steps)
        if result.tool_calls >= skill.budget.max_tool_calls:
            raise ProcedureBudgetExceeded("tool_calls", skill.budget.max_tool_calls)

    def _run_tool_step(
        self,
        step: ProceduralStep,
        index: int,
        symbols: Dict[str, Any],
        previous: Optional[Any],
        result: ProcedureRunResult,
    ) -> tuple:
        args = self._render_args(step.args, symbols=symbols, previous=previous)
        try:
            output = self._invoke(step.tool, args)
        except Exception as exc:  # tool's own voice is surfaced as an error obs
            observation = ProcedureObservation(
                step_index=index, kind="tool", name=step.tool, error=str(exc))
            result.tool_calls += 1
            return observation, _failed_marker(exc)

        result.tool_calls += 1
        observation = ProcedureObservation(
            step_index=index, kind="tool", name=step.tool, result=output)
        return observation, output

    def _run_check_step(
        self,
        step: ProceduralStep,
        index: int,
        symbols: Dict[str, Any],
        result: ProcedureRunResult,
    ) -> ProcedureObservation:
        verdict = self._check(step.check)
        observation = ProcedureObservation(
            step_index=index, kind="check", name=step.check, result=verdict)
        return observation

    def _render_args(
        self,
        args: Dict[str, Any],
        *,
        symbols: Dict[str, Any],
        previous: Optional[Any],
    ) -> Dict[str, Any]:
        """Resolve ``{symbol}`` references and the ``previous`` marker in args.

        The *value* ``"previous"`` (exactly) is replaced with the previous tool
        output; strings may embed ``{result_var}`` references resolved from the
        symbols produced by earlier tool steps.
        """
        resolved: Dict[str, Any] = {}
        for key, raw in args.items():
            if isinstance(raw, str) and raw == "previous":
                if previous is None:
                    raise ProcedureStepError(
                        f"arg {key!r} references 'previous' but there is no "
                        "previous tool output")
                resolved[key] = previous
                continue

            resolved[key] = self._resolve_value(raw, symbols=symbols)

        return resolved

    def _resolve_value(self, value: Any, *, symbols: Dict[str, Any]) -> Any:
        if isinstance(value, str):
            if value.startswith("{") and value.endswith("}"):
                symbol = value[1:-1]
                if symbol in symbols:
                    return symbols[symbol]
            return value
        if isinstance(value, dict):
            return {k: self._resolve_value(v, symbols=symbols)
                    for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve_value(v, symbols=symbols) for v in value]
        return value


def _failed_marker(exc: Exception) -> Any:
    """A stable marker carrying the tool's failure for downstream checks."""
    return {"_failed": str(exc)}