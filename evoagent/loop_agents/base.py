"""Common ``BaseLoopAgent`` abstraction (plan §4, §10).

Every core agent shares the same loop contract:

    Task -> BuildInitialState -> Plan -> Tool -> Observe -> Replan -> Final

``run()`` delegates to the existing bounded :class:`AgentLoop`, so step budget,
time budget, tool-call budget, no-progress detection, tool failure
classification and cancellation checks are all inherited from the runtime.
Subclasses implement three deterministic hooks: :meth:`build_initial_state`,
:meth:`agent_step` and :meth:`build_artifact`.

The step function obeys a hard requirement (plan §10): step N+1 can always see
step N's observation, because ``AgentLoop`` feeds the full ``observations`` list
into the state on every iteration.
"""
from typing import Any, Dict, List, Optional

from ..runtime import AgentLoop, AgentLoopResult
from .models import AgentPlanState
from .decision import AgentDecision


class BaseLoopAgent:
    agent_id = "agent"
    capabilities: tuple = ()
    #: The per-task governed tools this agent may call (plan §21).  Empty means
    #: the agent runs with whatever registry was bound at construction time.
    tool_allowlist: tuple = ()

    def __init__(
        self,
        max_steps: Optional[int] = None,
        timeout_seconds: Optional[int] = None,
        execution_policy=None,
        tools=None,
        bus=None,
        *,
        deep_loop: bool = True,
    ):
        self.agent_loop = AgentLoop(
            max_steps or 4, timeout_seconds or 45,
            execution_policy=execution_policy,
        )
        self.tools = tools  # GovernedToolRegistry or None
        self.bus = bus
        self.deep_loop = deep_loop
        # Structured planning metadata only (never raw chain-of-thought).
        self._plan_records: List[Dict[str, Any]] = []
        # The most recent task so ``build_artifact`` can reference inputs the
        # ``AgentLoopResult`` does not carry (e.g. the findings under critique).
        self._last_task: Dict[str, Any] = {}

    # -- required hooks ------------------------------------------------------
    def build_initial_state(self, task: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def agent_step(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Return an ``AgentLoop`` action: ``{"action": "tool"|"final", ...}``."""
        raise NotImplementedError

    def build_artifact(self, result: AgentLoopResult) -> Dict[str, Any]:
        """Turn the loop result into the agent's structured A2A artifact body."""
        raise NotImplementedError

    # -- optional per-task tool binding --------------------------------------
    def prepare(self, task: Dict[str, Any]):
        """Build a per-task governed registry from the task's own ``diff``.

        Default implementation rebuilds the allow-listed registry from
        ``task.input.diff`` so the same agent object can be served over
        in-process or HTTP A2A.  May return ``None`` to keep the registry bound
        at construction time.
        """
        if not self.tool_allowlist:
            return None
        from .tools import registry_for_task
        try:
            return registry_for_task(
                self.agent_id, task, allowed_tools=list(self.tool_allowlist),
                tool_context_config=getattr(self, "tool_context_config", None))
        except Exception:  # noqa: BLE001 - fall back to the bound registry
            return None

    # -- shared helpers ------------------------------------------------------
    def _plan(self, state: Dict[str, Any]) -> AgentPlanState:
        plan = state.get("plan")
        if isinstance(plan, AgentPlanState):
            return plan
        value = state.get("plan")
        if isinstance(value, dict):
            plan = AgentPlanState.from_dict(value)
            state["plan"] = plan
            return plan
        plan = AgentPlanState(objective=str(state.get("objective", "")))
        state["plan"] = plan
        return plan

    def _record(self, plan: AgentPlanState) -> None:
        self._plan_records.append(plan.to_dict())

    def _observe(self, state: Dict[str, Any]) -> List[Dict[str, Any]]:
        return list(state.get("observations") or [])

    def _last_observation(self, state: Dict[str, Any], index: int = -1) -> Optional[Dict[str, Any]]:
        observations = self._observe(state)
        if not observations:
            return None
        return observations[index]

    def _emit(self, kind: str, **detail) -> None:
        if self.bus is not None and hasattr(self.bus, "send"):
            self.bus.send(self.agent_id, "trace", kind, detail)

    # -- entry point ---------------------------------------------------------
    def run(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """Run the agent's loop and return its artifact + trace metadata."""
        self._plan_records = []
        self._decision_records = []
        self._last_task = dict(task)
        initial = self.build_initial_state(dict(task))
        task_id = str(task.get("task_id", ""))
        tools = self.prepare(dict(task)) or self.tools
        step_fn = self._shallow_step if not self.deep_loop else self._decision_step
        result = self.agent_loop.run(
            step_fn, tools, initial,
            agent_id=self.agent_id, task_id=task_id,
        )
        artifact = self.build_artifact(result)
        return {
            "artifact": artifact,
            "stop_reason": result.stop_reason,
            "steps": result.steps,
            "observations": result.observations,
            "plan": list(self._plan_records),
            "agent_id": self.agent_id,
            "decisions": list(getattr(self, "_decision_records", [])),
        }

    def decide_next_action(self, state: Dict[str, Any]) -> AgentDecision:
        """Normalize every specialist's next action into one decision model."""
        raw = self.agent_step(state)
        action = str(raw.get("action") or "final")
        return AgentDecision(
            action=action,
            tool_name=raw.get("tool") if action == "tool" else None,
            arguments=dict(raw.get("arguments") or {}),
            reason_code=str(raw.get("reason_code") or (
                "OBSERVATION_DRIVEN_TOOL" if action == "tool" else "GOAL_SATISFIED")),
            confidence=float(raw.get("confidence", 0.8)),
        )

    def _decision_step(self, state: Dict[str, Any]) -> Dict[str, Any]:
        raw = self.agent_step(state)
        action = str(raw.get("action") or "final")
        decision = AgentDecision(
            action=action,
            tool_name=raw.get("tool") if action == "tool" else None,
            arguments=dict(raw.get("arguments") or {}),
            reason_code=str(raw.get("reason_code") or (
                "OBSERVATION_DRIVEN_TOOL" if action == "tool" else "GOAL_SATISFIED")),
            confidence=float(raw.get("confidence", 0.8)),
        )
        if not hasattr(self, "_decision_records"):
            self._decision_records = []
        self._decision_records.append(decision.to_dict())
        raw["reason_code"] = decision.reason_code
        raw["confidence"] = decision.confidence
        return raw

    def _shallow_step(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Shallow stepper (plan §4.4, ``deep_loop=False``).

        Runs the agent's normal first gate once; as soon as any observation
        exists the loop finalises instead of pursuing the observation-driven
        deepen tools, so the shallow run genuinely changes tool selection.
        """
        if state.get("observations"):
            self._last_state = state
            return {"action": "final", "agent_id": self.agent_id}
        return self._decision_step(state)

    # -- tool helpers --------------------------------------------------------
    def call_tool(self, name: str, arguments: Dict[str, Any], task_id: str = "") -> Any:
        invoke_as = getattr(self.tools, "invoke_as", None)
        if invoke_as is not None:
            return invoke_as(self.agent_id, name, arguments, task_id=task_id)
        if getattr(self.tools, "invoke", None) is not None:
            return self.tools.invoke(name, arguments)
        raise RuntimeError("no governed tool registry bound to %s" % self.agent_id)


__all__ = ["BaseLoopAgent"]
