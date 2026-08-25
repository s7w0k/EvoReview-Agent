"""Parse procedure skill definitions from dict / JSON into ``ProcedureSkill``.

The DSL is declarative and intentionally *empty* of executable constructs: a
skill is only a list of steps (tool calls / named checks) plus metadata.  There
is no YAML dependency here; the source form is plain ``dict`` or ``json`` text,
which keeps the parser dependency-free and safe.
"""
from typing import Any, Dict, Mapping, Union

from .schema import ProceduralStep, ProcedureTrigger, ProcedureSkill


def parse_step(value: Union[Mapping[str, Any], ProceduralStep]) -> ProceduralStep:
    """Normalise a single step into a ``ProceduralStep``."""
    if isinstance(value, ProceduralStep):
        return value
    if not isinstance(value, Mapping):
        raise ValueError(
            "procedure step must be a mapping or ProceduralStep, got "
            f"{type(value).__name__}"
        )
    value = dict(value)
    kind = str(value.get("kind", "")).strip().lower()
    if kind == "":
        # Infer the kind from which keys are present, mirroring the DSL samples
        # where a ``check: ...`` entry can appear without an explicit kind.
        kind = "check" if (value.get("check") and not value.get("tool")) else "tool"
    if kind not in ("tool", "check"):
        raise ValueError(f"unsupported step kind: {kind!r}")
    return ProceduralStep(
        kind=kind,
        tool=str(value.get("tool", "") or ""),
        args=dict(value.get("args", {}) or {}),
        result_var=str(value.get("result_var", "") or ""),
        check=str(value.get("check", "") or ""),
    )


def parse_trigger(value: Any) -> ProcedureTrigger:
    """Normalise the trigger block into a ``ProcedureTrigger``."""
    if value is None:
        return ProcedureTrigger()
    if not isinstance(value, Mapping):
        raise ValueError("trigger must be a mapping")
    value = dict(value)
    return ProcedureTrigger(
        paths=[str(p) for p in value.get("paths", []) or []],
        keywords=[str(w) for w in value.get("keywords", []) or []],
        risk_level=[str(r) for r in value.get("risk_level", []) or []],
    )


def parse_procedure(value: Union[Mapping[str, Any], ProcedureSkill]) -> ProcedureSkill:
    """Parse a raw dict / already-typed skill into a validated ``ProcedureSkill``.

    Raises:
        ValueError: when required fields are missing or structurally invalid.
    """
    if isinstance(value, ProcedureSkill):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("procedure skill must be a mapping or ProcedureSkill")
    value = dict(value)
    name = str(value.get("name", "")).strip()
    if not name:
        raise ValueError("procedure skill requires a non-empty 'name'")

    steps_value = value.get("procedure", []) or []
    if not isinstance(steps_value, list):
        raise ValueError("procedure skill 'procedure' must be a list of steps")

    progress = [parse_step(item) for item in steps_value]

    return ProcedureSkill(
        name=name,
        trigger=parse_trigger(value.get("trigger")),
        procedure=progress,
        required_evidence=[str(e) for e in value.get("required_evidence", []) or []],
        budget=_parse_budget(value.get("budget")),
        version=int(value.get("version", 1)),
        metadata=dict(value.get("metadata", {}) or {}),
    )


def _parse_budget(value: Any) -> Any:
    """Return a ``ProcedureBudget`` from the budget block (imported lazily)."""
    from .schema import ProcedureBudget

    if value is None:
        return ProcedureBudget()
    if not isinstance(value, Mapping):
        raise ValueError("budget must be a mapping")
    value = dict(value)
    return ProcedureBudget(
        max_steps=_positive_int(value.get("max_steps", 6), "max_steps"),
        max_tool_calls=_positive_int(value.get("max_tool_calls", 8), "max_tool_calls"),
    )


def _positive_int(raw: Any, field_name: str) -> int:
    try:
        number = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"budget.{field_name} must be an integer, got {raw!r}")
    if number < 1:
        raise ValueError(f"budget.{field_name} must be >= 1, got {number}")
    return number


def parse_json(text: str) -> ProcedureSkill:
    """Parse a JSON-encoded procedure skill definition."""
    import json

    return parse_procedure(json.loads(text))