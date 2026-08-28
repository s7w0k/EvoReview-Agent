"""Coordinator Agent (plan §2, §11).

Replaces the staged ``MultiAgentCoordinator`` workflow with a real global loop:
``Understand -> Build/Update TaskGraph -> Delegate -> Observe -> Replan ->
Finalize``.  The graph is built *dynamically* from a ``profile_risk`` result
(never hard-coded to all specialists) and later **revised based on agent
results** -- e.g. Critic's ``replan_requests`` trigger a specialist re-run with
a bumped graph revision -- not merely on specialist failure (plan §5, §3.1).

Delegation is a normal governed ``delegate_agent`` tool action (plan §4.2): the
Loop Contract keeps actions to ``tool``/``final`` while the A2A round-trip
happens inside the tool via a :class:`Delegator`.  All six agents are reached
through the Hub-and-Spoke topology (§6).
"""
import uuid
from typing import Any, Dict, List, Optional

from .base import BaseLoopAgent
from .models import AgentTaskNode, AgentTaskStatus, CoordinatorTaskGraph
from .stepper import (
    PlanTracker, final_action, observations, tool_action, tool_results,
)

_AGENT_TASK_TYPE = {
    "security-agent": "review.security",
    "reliability-agent": "review.reliability",
    "critic-agent": "critique.findings",
    "verifier-agent": "verify.findings",
    "fix-agent": "fix.generate",
}


def _finding_key(finding: Dict[str, Any]) -> str:
    return "%s:%s:%s" % (
        finding.get("rule_id"), finding.get("path"), finding.get("line"))


class CoordinatorAgent(BaseLoopAgent):
    agent_id = "coordinator"
    capabilities = ("orchestration", "planning", "scheduling", "routing")
    task_type = "review.coordinate"
    tool_allowlist = (
        "inspect_diff", "profile_risk", "evaluate_coverage", "compare_findings",
    )

    def __init__(self, delegator=None, *, max_steps=None, timeout_seconds=None,
                 max_replans: int = 1, execution_policy=None, tools=None, bus=None):
        super().__init__(
            max_steps or 16, timeout_seconds or 60,
            execution_policy=execution_policy, tools=tools, bus=bus,
        )
        self.delegator = delegator
        self.max_replans = max(0, max_replans)
        self._replan_count = 0
        self._graph_revision = 1

    # -- task tool binding ---------------------------------------------------
    def prepare(self, task: Dict[str, Any]):
        if self.delegator is None:
            return super().prepare(task)
        from ..diff_parser import parse_unified_diff
        from .tools import (
            build_delegate_handlers, build_expert_context, build_loop_registry,
        )
        diff = (task.get("input") or {}).get("diff") or ""
        parsed = parse_unified_diff(diff)
        ctx = build_expert_context(diff, parsed)
        handlers = build_delegate_handlers(self.delegator)
        return build_loop_registry(
            self.agent_id, ctx, allowed_tools=list(self.tool_allowlist),
            delegate_handlers=handlers,
        )

    # -- state ---------------------------------------------------------------
    def build_initial_state(self, task: Dict[str, Any]) -> Dict[str, Any]:
        state = dict(task)
        state["objective"] = str(task.get("objective")
                                 or "coordinate a multi-agent code review")
        state["diff"] = (task.get("input") or {}).get("diff") or ""
        state["plan"] = None
        state["task_graph"] = None
        state["pending"] = []
        state["_replan_checked"] = False
        state["_replan_count"] = 0
        return state

    # -- graph construction (dynamic, result-driven) --------------------------
    def _build_graph(self, risk: Dict[str, Any]) -> CoordinatorTaskGraph:
        graph = CoordinatorTaskGraph(graph_id=uuid.uuid4().hex)
        requested = list(risk.get("agents") or [])
        if not requested:
            requested = ["reliability-agent"]
        specialist_names: List[str] = []
        index = 0
        for agent_id in requested:
            if self.delegator is not None and not self.delegator.has(agent_id):
                continue
            task_type = _AGENT_TASK_TYPE.get(agent_id, "review.reliability")
            node = AgentTaskNode(
                node_id="spec%d" % index, task_type=task_type,
                objective="review changed lines for %s" % agent_id,
                target_capabilities=[agent_id], agent_id=agent_id,
            )
            graph.add(node)
            specialist_names.append(node.node_id)
            index += 1

        if specialist_names:
            graph.add(AgentTaskNode(
                node_id="critic", task_type="critique.findings",
                objective="challenge and reflect on the collected findings",
                dependencies=list(specialist_names), agent_id="critic-agent",
            ))
            graph.add(AgentTaskNode(
                node_id="verifier", task_type="verify.findings",
                objective="independently verify the findings",
                dependencies=["critic"], agent_id="verifier-agent",
            ))
            graph.add(AgentTaskNode(
                node_id="fix", task_type="fix.generate",
                objective="generate a verified repair for the findings",
                dependencies=["verifier"], agent_id="fix-agent",
            ))
        return graph

    # -- loop ----------------------------------------------------------------
    def agent_step(self, state: Dict[str, Any]) -> Dict[str, Any]:
        plan = PlanTracker(state, str(state.get("objective")), [
            "profile risk and plan agents", "run the specialist agents",
            "route to critic / verifier / fix", "replan on evidence gaps",
            "finalize"],
            confidence=0.9)
        obs = observations(state)
        graph = state.get("task_graph")

        if graph is None:
            if not obs:
                plan.begin("profile_risk")
                return tool_action("profile_risk", {})
            risk: Dict[str, Any] = {}
            results = tool_results(state, "profile_risk")
            if results:
                risk = dict(results[-1])
            graph = self._build_graph(risk)
            state["task_graph"] = graph

        # Reconcile delegations issued in the previous step (they are
        # synchronous in-process A2A calls, so they are complete by now).
        for node in graph.nodes.values():
            if node.status == AgentTaskStatus.RUNNING:
                node.status = AgentTaskStatus.COMPLETED

        pending = state.get("pending") or []
        if not pending:
            pending = [
                n.node_id for n in graph.next_ready()
                if n.status == AgentTaskStatus.PENDING
            ]
            state["pending"] = pending

        if pending:
            node_id = pending[0]
            state["pending"] = pending[1:]
            return self._delegate(state, graph.nodes[node_id], plan)

        return self._finalize(state, graph, plan)

    def _delegate(self, state, node: AgentTaskNode, plan) -> Dict[str, Any]:
        node.status = AgentTaskStatus.RUNNING
        plan.begin("task:" + node.task_type)
        objective = str(node.objective)
        if node.task_type in ("critique.findings", "verify.findings", "fix.generate"):
            findings = self.delegator.specialist_findings() if self.delegator else []
        else:
            findings = []
        return tool_action("delegate_agent", {
            "agent_id": node.agent_id or "",
            "task_type": node.task_type,
            "objective": objective,
            "findings": findings,
            "diff": state.get("diff") or "",
        })

    def _finalize(self, state, graph, plan) -> Dict[str, Any]:
        # Result-driven replan (plan §3.1, §5): Critic requesting more evidence
        # re-runs a specialist and bumps the graph revision instead of merely
        # swapping a failed node.
        if not state.get("_replan_checked"):
            state["_replan_checked"] = True
            requests = self._critic_replan_requests()
            if requests and state.get("_replan_count", 0) < self.max_replans:
                state["_replan_count"] = state.get("_replan_count", 0) + 1
                target = self._replan_target(graph)
                if target is not None:
                    target.status = AgentTaskStatus.PENDING
                    graph.replace(target)
                    plan.revise(
                        ["re-run a specialist to gather missing evidence"],
                        str(requests[0].get("reason", "evidence gap")))
                    state["pending"] = [target.node_id]
                    return self._delegate(state, target, plan)
        self._replan_count = int(state.get("_replan_count", 0))
        self._graph_revision = int(getattr(graph, "revision", 1))
        plan.complete("replan on evidence gaps").complete("finalize")
        return final_action(agent_id=self.agent_id)

    def _replan_target(self, graph: CoordinatorTaskGraph) -> Optional[AgentTaskNode]:
        for node in graph.nodes.values():
            if node.task_type.startswith("review."):
                return node
        if graph.nodes:
            return next(iter(graph.nodes.values()))
        return None

    def _critic_replan_requests(self) -> List[Dict[str, Any]]:
        if self.delegator is None:
            return []
        requests: List[Dict[str, Any]] = []
        for record in self.delegator.artifacts.values():
            if record.get("task_type") != "critique.findings":
                continue
            content = record.get("content") or {}
            requests.extend(content.get("replan_requests") or [])
        return requests

    def _verifier_decisions(self) -> Dict[str, Any]:
        if self.delegator is None:
            return {}
        decisions: Dict[str, Any] = {}
        for record in self.delegator.artifacts.values():
            if record.get("task_type") != "verify.findings":
                continue
            content = record.get("content") or {}
            decisions.update(content.get("decisions") or {})
        return decisions

    # -- deterministic FindingArbiter (plan §25) ------------------------------
    def _arbitrate(self) -> tuple:
        if self.delegator is None:
            return [], []
        specialists = self.delegator.specialist_findings()
        decisions = self._verifier_decisions()
        accepted: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        for finding in specialists:
            decision = decisions.get(_finding_key(finding))
            if decision is not None and decision.get("verified"):
                accepted.append(finding)
            else:
                rejected.append(finding)
        return accepted, rejected

    def build_artifact(self, result) -> Dict[str, Any]:
        accepted, rejected = self._arbitrate()
        return {
            "task_type": self.task_type,
            "agent_id": self.agent_id,
            "accepted_findings": accepted,
            "rejected_findings": rejected,
            "count": len(accepted),
            "rejected_count": len(rejected),
            "graph_revision": self._graph_revision,
            "replan_count": self._replan_count,
            "delegated_tasks": len(self.delegator.artifacts) if self.delegator else 0,
        }


__all__ = ["CoordinatorAgent"]