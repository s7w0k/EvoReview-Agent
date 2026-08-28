"""Deterministic step helpers shared by the six loop agents (plan §3.2, §4).

These helpers make every ``agent_step`` an observable
``Plan -> Act -> Observe -> Replan -> Final`` decision: the class reads only the
assembled ``observations`` that ``AgentLoop`` already fed into the state (so
step N+1 really depends on step N) and returns the *next* ``tool``/``final``
action.  Agents never execute tools themselves -- ``AgentLoop`` does that and
appends the observation back into the state.
"""
import json
from typing import Any, Dict, List, Optional

from .models import AgentPlanState


# -- action builders ----------------------------------------------------------

def tool_action(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Build a governed ``tool`` action for :class:`AgentLoop`."""
    return {"action": "tool", "tool": name, "arguments": dict(arguments)}


def final_action(**body: Any) -> Dict[str, Any]:
    """Build a ``final`` action carrying the structured artifact body."""
    body["action"] = "final"
    return body


# -- observation access (step N+1 can always see step N) ----------------------

def observations(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(state.get("observations") or [])


def last_observation(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    items = observations(state)
    return items[-1] if items else None


def last_tool_failed(state: Dict[str, Any]) -> bool:
    item = last_observation(state)
    return bool(item and not item.get("ok"))


def last_tool(state: Dict[str, Any]) -> Optional[str]:
    item = last_observation(state)
    return str(item.get("tool")) if item else None


def tool_results(state: Dict[str, Any], name: str) -> List[Dict[str, Any]]:
    """Decode the JSON observations produced by repeated calls of one tool."""
    results: List[Dict[str, Any]] = []
    for item in observations(state):
        if item.get("tool") == name and item.get("ok"):
            raw = item.get("result")
            if isinstance(raw, dict):
                results.append(raw)
                continue
            if isinstance(raw, str):
                try:
                    loaded = json.loads(raw)
                except Exception:
                    loaded = {"raw": raw}
                results.append(loaded if isinstance(loaded, dict) else {"value": loaded})
    return results


def result_findings(result: Any) -> List[Dict[str, Any]]:
    """Pull the ``findings`` list out of one-or-more scan tool result(s)."""
    if isinstance(result, list):
        merged: List[Dict[str, Any]] = []
        for item in result:
            merged.extend(_scan_findings(item))
        return merged
    return _scan_findings(result)


def _scan_findings(result: Any) -> List[Dict[str, Any]]:
    if not isinstance(result, dict):
        return []
    for key in ("findings", "items", "result"):
        value = result.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


# -- structured planning metadata --------------------------------------------

class PlanTracker:
    """Mutable, auditable planning metadata for one loop (plan §4.1).

    Only objective / subgoal / next-action / reason-code / confidence are
    recorded -- never a raw chain-of-thought.
    """

    def __init__(
        self, state: Dict[str, Any], objective: str,
        subgoals: Optional[List[str]] = None, confidence: float = 0.5,
    ) -> None:
        plan = state.get("plan")
        if not isinstance(plan, AgentPlanState):
            plan = AgentPlanState(objective=str(objective))
            state["plan"] = plan
        plan.objective = objective or plan.objective
        if subgoals and not plan.subgoals:
            plan.subgoals = list(subgoals)
        plan.confidence = confidence
        plan.next_action = ""
        self.state = state
        self.plan = plan

    def begin(self, action: str) -> "PlanTracker":
        self.plan.next_action = action
        self.plan.plan_version += 1
        return self

    def complete(self, subgoal: str) -> "PlanTracker":
        if subgoal and subgoal not in self.plan.completed:
            self.plan.completed.append(subgoal)
        return self

    def marker(self, subgoal: str) -> "PlanTracker":
        if subgoal and subgoal not in self.plan.completed:
            self.plan.completed.append(subgoal)
        return self

    def revise(self, subgoals: List[str], reason: str) -> "PlanTracker":
        self.plan.subgoals = list(subgoals)
        self.plan.revision_reason = reason
        self.plan.plan_version += 1
        return self


__all__ = [
    "tool_action", "final_action", "observations", "last_observation",
    "last_tool_failed", "last_tool", "tool_results", "result_findings",
    "PlanTracker",
]