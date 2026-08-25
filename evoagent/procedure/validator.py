"""Static safety validation for procedure skills.

A procedure skill is a *restricted workflow*, not a code plugin.  It may only
refer to tools that are explicitly authorised, only evaluate named checks, and
must stay within a declared budget.  This module performs the static checks
*without* executing anything.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .schema import ProceduralStep, ProcedureSkill

# Black-listed constructs that have no place in a restricted workflow.
FORBIDDEN_TOKEN = (
    "eval",
    "exec",
    "__import__",
    "import ",
    "subprocess",
    "os.system",
    "socket",
    "requests.",
    "url.",
    "http://",
    "https://",
    "shell",
    "system(",
)


@dataclass
class ProcedureValidationIssue:
    """A single static-validation problem found in a procedure skill."""

    severity: str  # "error" | "warning"
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return {"severity": self.severity, "message": self.message}


@dataclass
class ProcedureValidationResult:
    """Aggregated static-validation outcome for a procedure skill."""

    valid: bool
    issues: List[ProcedureValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> List[ProcedureValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> List[ProcedureValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "issues": [item.to_dict() for item in self.issues],
        }


class ProcedureValidator:
    """Deterministic static validator for procedure skills."""

    def __init__(
        self,
        allowed_tools: Optional[Iterable[str]] = None,
        denied_tools: Optional[Iterable[str]] = None,
        allowed_checks: Optional[Sequence[str]] = None,
    ):
        # ``None`` means "not checked"; an empty set means "no tool allowed".
        self._allowed_tools = (
            None if allowed_tools is None else {str(t) for t in allowed_tools}
        )
        self._denied_tools = {str(t) for t in (denied_tools or [])}
        self._allowed_checks = (
            None if allowed_checks is None else list(allowed_checks)
        )

    # -- public API ---------------------------------------------------------

    def validate(self, skill: ProcedureSkill) -> ProcedureValidationResult:
        """Validate *without* executing the skill.  Returns the outcome."""
        issues: List[ProcedureValidationIssue] = []

        if not skill.name:
            issues.append(ProcedureValidationIssue(
                "error", "skill requires a non-empty 'name'"))

        self._validate_budget(skill, issues)
        self._validate_tools(skill, issues)
        self._validate_checks(skill, issues)
        self._validate_step_structure(skill, issues)
        self._validate_no_code(skill, issues)

        valid = all(issue.severity == "warning" for issue in issues)
        return ProcedureValidationResult(valid=valid, issues=issues)

    # -- internals ----------------------------------------------------------

    def _validate_budget(self, skill: ProcedureSkill, issues: List[Any]) -> None:
        budget = skill.budget
        if budget.max_steps < 1:
            issues.append(ProcedureValidationIssue(
                "error", f"budget.max_steps must be >= 1, got {budget.max_steps}"))
        if budget.max_tool_calls < 1:
            issues.append(ProcedureValidationIssue(
                "error",
                f"budget.max_tool_calls must be >= 1, got {budget.max_tool_calls}"))
        if len(skill.procedure) > budget.max_steps:
            issues.append(ProcedureValidationIssue(
                "error",
                f"procedure has {len(skill.procedure)} steps but budget.max_steps "
                f"allows only {budget.max_steps}"))

    def _validate_tools(self, skill: ProcedureSkill, issues: List[Any]) -> None:
        tool_counts = _count_tools(skill)
        for tool_name, count in tool_counts.items():
            if not skill.budget or not skill.budget.max_tool_calls:
                continue
            if tool_name in self._denied_tools:
                issues.append(ProcedureValidationIssue(
                    "error", f"tool {tool_name!r} is denied by policy"))
            if self._allowed_tools is not None and tool_name not in self._allowed_tools:
                meta = ("no tools" if not self._allowed_tools
                        else "not in the allow-list")
                issues.append(ProcedureValidationIssue(
                    "error", f"tool {tool_name!r} uses {meta}"))
            if count > skill.budget.max_tool_calls:
                issues.append(ProcedureValidationIssue(
                    "error",
                    f"tool {tool_name!r} called {count}x, exceeding "
                    f"budget.max_tool_calls={skill.budget.max_tool_calls}"))

    def _validate_checks(self, skill: ProcedureSkill, issues: List[Any]) -> None:
        for step in skill.procedure:
            if step.kind != "check":
                continue
            if not step.check:
                issues.append(ProcedureValidationIssue(
                    "error", "a 'check' step requires a non-empty 'check' name"))
            elif self._allowed_checks is not None and \
                    step.check not in self._allowed_checks:
                issues.append(ProcedureValidationIssue(
                    "error",
                    f"check {step.check!r} is not in the set of registered checks"))

    def _validate_step_structure(self, skill: ProcedureSkill, issues: List[Any]) -> None:
        # Symbols produced by earlier tool steps are legal references.
        produced: List[str] = []
        for index, step in enumerate(skill.procedure):
            if step.kind == "tool":
                if not step.tool:
                    issues.append(ProcedureValidationIssue(
                        "error",
                        f"step {index} is a tool step but has an empty 'tool' name"))
                self._validate_args_refs(step, produced, index, issues)
                if step.result_var:
                    produced.append(step.result_var)
            elif step.kind == "check":
                produced.clear()

    def _validate_args_refs(self, step: ProceduralStep, produced, index, issues):
        """Validate every ``{symbol}`` reference used by a step's arguments.

        A reference may point at:
          * ``previous`` -- the most recent tool output (must exist), or
          * a ``result_var`` produced by an earlier tool step (must exist).
        Forward / unknown references are rejected to keep the DSL declarative.
        """
        produced_set = set(produced)
        for key, raw in step.args.items():
            for ref in _arg_refs(raw):
                if ref == "previous":
                    if not produced:
                        issues.append(ProcedureValidationIssue(
                            "error",
                            f"step {index} arg {key!r} references 'previous' but "
                            "no earlier tool step produced output"))
                elif ref not in produced_set:
                    issues.append(ProcedureValidationIssue(
                        "error",
                        f"step {index} arg {key!r} references unknown symbol "
                        f"{ref!r} (not produced by any earlier tool step)"))

    def _validate_no_code(self, skill: ProcedureSkill, issues: List[Any]) -> None:
        stack = _flatten_leaf_values(skill)
        for value in stack:
            lowered = str(value).lower()
            for token in FORBIDDEN_TOKEN:
                if token in lowered:
                    issues.append(ProcedureValidationIssue(
                        "error",
                        f"forbidden construct {token!r} present in DSL; "
                        "procedures are restricted workflows and cannot run code"))
                    break


def _count_tools(skill: ProcedureSkill) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for step in skill.procedure:
        if step.kind == "tool":
            counts[step.tool] = counts.get(step.tool, 0) + 1
    return counts


def _flatten_leaf_values(skill: ProcedureSkill) -> Iterable[Any]:
    """Yield every leaf value (string / number / bool) inside the skill."""
    def walk(node):
        if isinstance(node, dict):
            for value in node.values():
                yield from walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                yield from walk(value)
        else:
            yield node

    yield from walk(skill.to_dict())


def _arg_refs(raw: Any) -> List[str]:
    """Extract ``{symbol}`` references and the literal ``previous`` marker."""
    refs = []
    if isinstance(raw, str):
        # The exact value "previous" is a magic reference to the last output.
        if raw == "previous":
            refs.append("previous")
        import re

        refs += re.findall(r"\{(\w+)\}", raw)
    elif isinstance(raw, dict):
        for value in raw.values():
            refs += _arg_refs(value)
    elif isinstance(raw, list):
        for value in raw:
            refs += _arg_refs(value)
    return refs


def validate_skill(
    skill: ProcedureSkill,
    allowed_tools=None,
    allowed_checks=None,
) -> ProcedureValidationResult:
    """Convenience wrapper that builds a validator and validates instantly."""
    return ProcedureValidator(
        allowed_tools=allowed_tools, allowed_checks=allowed_checks
    ).validate(skill)