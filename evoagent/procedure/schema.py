"""Procedure Skill DSL data model.

A procedure skill is a *restricted* agent workflow, not a code plugin.  It may
only invoke tools registered in the tool registry and evaluate named checks.  No
arbitrary Python / shell / network / dynamic import is allowed.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class ProceduralStep:
    kind: str  # "tool" | "check"
    tool: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    result_var: str = ""
    check: str = ""
    on_failure: str = "continue"  # "abort" | "continue" (plan section 10.6)

    def to_dict(self) -> Dict[str, Any]:
        value: Dict[str, Any] = {"kind": self.kind}
        if self.tool:
            value["tool"] = self.tool
        if self.args:
            value["args"] = dict(self.args)
        if self.result_var:
            value["result_var"] = self.result_var
        if self.check:
            value["check"] = self.check
        if self.on_failure and self.on_failure != "continue":
            value["on_failure"] = self.on_failure
        return value

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "ProceduralStep":
        return cls(
            kind=str(value.get("kind", "tool")),
            tool=str(value.get("tool", "")),
            args=dict(value.get("args", {})),
            result_var=str(value.get("result_var", "")),
            check=str(value.get("check", "")),
            on_failure=str(value.get("on_failure", "continue")),
        )


@dataclass
class ProcedureTrigger:
    paths: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    risk_level: List[str] = field(default_factory=list)


@dataclass
class ProcedureBudget:
    max_steps: int = 6
    max_tool_calls: int = 8


@dataclass
class ProcedureSkill:
    name: str
    trigger: ProcedureTrigger = field(default_factory=ProcedureTrigger)
    procedure: List[ProceduralStep] = field(default_factory=list)
    required_evidence: List[str] = field(default_factory=list)
    budget: ProcedureBudget = field(default_factory=ProcedureBudget)
    version: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)

    def tool_names(self) -> List[str]:
        return [step.tool for step in self.procedure if step.kind == "tool"]

    def check_names(self) -> List[str]:
        return [step.check for step in self.procedure if step.kind == "check"]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "trigger": {
                "paths": list(self.trigger.paths),
                "keywords": list(self.trigger.keywords),
                "risk_level": list(self.trigger.risk_level),
            },
            "procedure": [step.to_dict() for step in self.procedure],
            "required_evidence": list(self.required_evidence),
            "budget": {"max_steps": self.budget.max_steps,
                       "max_tool_calls": self.budget.max_tool_calls},
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "ProcedureSkill":
        return cls(
            name=str(value.get("name", "")),
            version=int(value.get("version", 1)),
            trigger=ProcedureTrigger(**{
                key: value.get("trigger", {}).get(key, [])
                for key in ("paths", "keywords", "risk_level")
            }),
            procedure=[ProceduralStep.from_dict(item)
                       for item in value.get("procedure", [])],
            required_evidence=list(value.get("required_evidence", [])),
            budget=ProcedureBudget(**dict(
                value.get("budget", {}),
                max_steps=int(value.get("budget", {}).get("max_steps", 6)),
                max_tool_calls=int(value.get("budget", {}).get("max_tool_calls", 8)),
            )),
            metadata=dict(value.get("metadata", {})),
        )