"""Coordinator Agent (plan §2, §11).

Replaces the staged ``MultiAgentCoordinator`` workflow with a real global loop:
``Understand -> Build/Update TaskGraph -> Delegate -> Observe -> Replan ->
Finalize``.

**v2 (``mode="v2"``)** is the Semantic-Dynamic-Planner upgrade from the
Multi-Agent 6-item optimization plan
(``docs/EvoReview-Agent_Multi-Agent_6项深化优化实施计划.md``):

* :func:`SemanticPlanner` derives a structured task DAG from
  ``semantic_change_summary`` + ``profile_risk`` (plan §4);
* the proposal is gated by :class:`TaskGraphValidator` (repair once, then
  :class:`FallbackPlanner`) (plan §4.5);
* :class:`TaskGraphScheduler` decides *which fresh nodes run, how many in
  parallel*, and fail-fast on critical branches (plan §6);
* targeted :class:`ReplanRequest` requests are resolved by
  :class:`ReplanTargetResolver` and insert a **new** node (never reset the
  original) ahead of the downstream consumer, with fingerprint+budget loop
  protection (plan §5);
* :class:`GraphMutator` applies conditional critic/verifier/fix nodes at
  runtime without rewriting completed history (plan §8).

v1 keeps the original behaviour (``legacy``/``six-agent`` path).
"""
import os
import uuid
from typing import Any, Dict, List, Optional

from . import graph_policy
from .base import BaseLoopAgent
from .models import AgentTaskNode, AgentTaskStatus, CoordinatorTaskGraph
from .planning import (
    SemanticPlanner, TaskGraphValidator, build_default_context,
    build_graph_from_tasks, FallbackPlanner,
)
from .replan import (
    ReplanRequest, ReplanTargetResolver, ReplanTracker,
)
from .scheduler import ConcurrencyBudget, TaskGraphScheduler
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

# capability -> node-id prefix used when inserting a targeted recheck node.
_RECHECK_PREFIX = {
    "security": "sec-recheck",
    "reliability": "rel-recheck",
    "dataflow": "df-recheck",
    "verification": "verify-recheck",
    "test": "test-recheck",
}


def _finding_key(finding: Dict[str, Any]) -> str:
    return "%s:%s:%s" % (
        finding.get("rule_id"), finding.get("path"), finding.get("line"))


class CoordinatorAgent(BaseLoopAgent):
    agent_id = "coordinator"
    capabilities = ("orchestration", "planning", "scheduling", "routing")
    task_type = "review.coordinate"
    tool_allowlist = (
        "inspect_diff", "profile_risk", "semantic_change_summary",
        "evaluate_coverage", "compare_findings",
    )

    def __init__(self, delegator=None, *, max_steps=None, timeout_seconds=None,
                 max_replans: int = 1, execution_policy=None, tools=None, bus=None,
                 mode: str = "v1"):
        super().__init__(
            max_steps or 16, timeout_seconds or 60,
            execution_policy=execution_policy, tools=tools, bus=bus,
        )
        self.delegator = delegator
        self.max_replans = max(0, max_replans)
        self.mode = mode if mode in ("v1", "v2") else "v1"
        self.execution_policy = execution_policy
        self._replan_count = 0
        self._graph_revision = 1
        self._replan_tracker = ReplanTracker()

    # -- DAG validation helper (only when the scheduler is enabled) ----------
    @property
    def _v2(self) -> bool:
        enabled = os.getenv("EVOAGENT_V2_SCHEDULING", "0") == "1"
        return self.mode == "v2" and enabled

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
        state["_batch"] = []
        state["_replan_checked"] = False
        state["_replan_count"] = 0
        state["_graph_plan"] = {}
        return state

    # -- graph construction (v1: keyword risk driven) -------------------------
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

    # -- graph construction (v2: planner + validator + fallback) --------------
    def _build_graph_v2(self, state: Dict[str, Any], summary: Dict[str, Any],
                        risk: Dict[str, Any]) -> CoordinatorTaskGraph:
        agents_available = self._available_agents()
        base_policy = (self.execution_policy.to_dict()
                       if getattr(self.execution_policy, "to_dict", None)
                       else self.execution_policy)
        ctx = build_default_context(
            state.get("diff") or "", semantic_summary=summary,
            risk_profile=risk,
            available_agents=[{"agent_id": a} for a in agents_available],
            execution_policy=dict(base_policy or {})
            if base_policy else
            {"remediation": True, "fix_policy": True, "repo_permission": True},
        )
        planner = SemanticPlanner()
        decision = planner.plan(ctx)
        gid = uuid.uuid4().hex
        graph = build_graph_from_tasks(decision.tasks, gid)

        validator = TaskGraphValidator(
            available_agents=set(agents_available))
        errors = validator.validate(graph)
        if errors:
            # one deterministic repair: drop unknown-agent nodes
            known = set(agents_available)
            for node in list(graph.nodes.values()):
                if node.agent_id not in known:
                    graph.remove(node.node_id)
            errors = validator.validate(graph)
            if errors:
                # fall back to the deterministic repair planner
                fallback = FallbackPlanner()
                decision = fallback.plan(ctx)
                graph = build_graph_from_tasks(
                    decision.tasks, gid + "_fb")

        # apply the conditional collaboration-graph policy on top (plan §8)
        mutator = graph_policy.GraphMutator(graph)
        self._add_plan_policy(mutator, graph)
        return graph

    def _add_plan_policy(self, mutator, graph) -> None:
        """Re-inforce fix presence when remediation is allowed (plan §8 Fix trig)."""
        if "fix" in graph.nodes:
            return
        # only add a fix stage if remediation was requested
        base_policy = (self.execution_policy.to_dict()
                       if getattr(self.execution_policy, "to_dict", None)
                       else self.execution_policy) or {}
        if not (base_policy.get("remediation") or base_policy.get("fix_policy")):
            return
        verifier = next(
            (n for n in graph.nodes if n.startswith("verifier")), None)
        if verifier is None:
            graph.add(AgentTaskNode(
                node_id="fix", task_type="fix.generate",
                objective="generate a verified repair for the findings",
                dependencies=[], agent_id="fix-agent"))
        else:
            mutator.change_dependency(
                verifier, [verifier], reason="fix-after-verify")

    def _available_agents(self) -> List[str]:
        names = list(_AGENT_TASK_TYPE)
        if self.delegator is None:
            return names
        try:
            cards = self.delegator.discover()
        except Exception:  # noqa: BLE001
            return names
        known = set()
        for card in (cards or []):
            if isinstance(card, dict):
                known.add(card.get("agent_id") or "")
        if known:
            return [a for a in names if a in known] or names
        return names

    # -- loop ---------------------------------------------------------------
    def agent_step(self, state: Dict[str, Any]) -> Dict[str, Any]:
        plan = PlanTracker(state, str(state.get("objective")), [
            "profile risk and plan agents", "run the specialist agents",
            "route to critic / verifier / fix", "replan on evidence gaps",
            "finalize"],
            confidence=0.9)
        if self._v2:
            return self._agent_step_v2(state, plan)
        return self._agent_step_v1(state, plan)

    # -- v2 loop ------------------------------------------------------------
    def _agent_step_v2(self, state, plan) -> Dict[str, Any]:
        obs = observations(state)
        graph = state.get("task_graph")

        # Phase A: gather semantic context, then build the planner-driven graph.
        if graph is None:
            if not tool_results(state, "semantic_change_summary"):
                plan.begin("semantic_change_summary")
                return tool_action("semantic_change_summary", {})
            if not tool_results(state, "profile_risk"):
                plan.begin("profile_risk")
                return tool_action("profile_risk", {})
            summary_results = tool_results(state, "semantic_change_summary")
            risk_results = tool_results(state, "profile_risk")
            summary = dict(summary_results[-1]) if summary_results else {}
            risk = dict(risk_results[-1]) if risk_results else {}
            graph = self._build_graph_v2(state, summary, risk)
            state["task_graph"] = graph
            state["_graph_plan"] = {
                "plan_id": uuid.uuid4().hex,
                "graph_id": graph.graph_id,
                "rationale": [],
            }

        # Phase B: reconcile + schedule fresh nodes through the scheduler.
        scheduler = TaskGraphScheduler(graph)
        scheduler.reconcile()
        batch = state.get("_batch") or []
        if not batch:
            batch = scheduler.next_batch()
            state["_batch"] = batch

        if batch:
            node_id = batch[0]
            state["_batch"] = batch[1:]
            scheduler.claim(node_id)
            return self._delegate(state, graph.nodes[node_id], plan)

        # Phase C: finalize / targeted replan.
        return self._finalize_v2(state, graph, plan)

    # -- v1 loop (unchanged contract) ---------------------------------------
    def _agent_step_v1(self, state, plan) -> Dict[str, Any]:
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

    # -- finalize (v1) ------------------------------------------------------
    def _finalize(self, state, graph, plan) -> Dict[str, Any]:
        if not state.get("_replan_checked"):
            state["_replan_checked"] = True
            requests = self._critic_replan_requests()
            if requests and state.get("_replan_count", 0) < self.max_replans:
                state["_replan_count"] = state.get("_replan_count", 0) + 1
                first = requests[0]
                reason = getattr(first, "reason_summary", None) or (
                    first.get("reason") if isinstance(first, dict) else None)
                target = self._replan_target(graph)
                if target is not None:
                    target.status = AgentTaskStatus.PENDING
                    graph.replace(target)
                    plan.revise(
                        ["re-run a specialist to gather missing evidence"],
                        str(reason or "evidence gap"))
                    state["pending"] = [target.node_id]
                    return self._delegate(state, target, plan)
        self._replan_count = int(state.get("_replan_count", 0))
        self._graph_revision = int(getattr(graph, "revision", 1))
        plan.complete("replan on evidence gaps").complete("finalize")
        return final_action(agent_id=self.agent_id)

    # -- finalize (v2) ------------------------------------------------------
    def _finalize_v2(self, state, graph, plan) -> Dict[str, Any]:
        if not state.get("_replan_checked"):
            state["_replan_checked"] = True
            request = self._pick_replan_request(state, graph)
            if request is not None and state.get("_replan_count", 0) < self.max_replans:
                state["_replan_count"] = state.get("_replan_count", 0) + 1
                mutator = graph_policy.GraphMutator(graph)
                new_node = self._insert_recheck(mutator, graph, request)
                plan.revise(
                    ["insert targeted recheck for %s" % new_node.node_id],
                    request.reason_summary)
                state["pending"] = [new_node.node_id]
                return self._delegate(state, new_node, plan)
        self._replan_count = int(state.get("_replan_count", 0))
        self._graph_revision = int(getattr(graph, "revision", 1))
        plan.complete("replan on evidence gaps").complete("finalize")
        return final_action(agent_id=self.agent_id)

    def _pick_replan_request(self, state, graph) -> Optional[ReplanRequest]:
        """Resolve the highest-priority critic request to a new node."""
        requests = self._critic_replan_requests()
        if not requests:
            requests = self._verifier_replan_requests()
        if not requests:
            return None
        available = [a for a in _AGENT_TASK_TYPE]  # planner's rosetta
        resolver = ReplanTargetResolver(available)
        requests.sort(key=lambda r: -getattr(r, "priority", 5) if isinstance(
            r, ReplanRequest) else -int(r.get("priority", 5)))
        for raw in requests:
            request = raw if isinstance(raw, ReplanRequest) else ReplanRequest.from_dict(raw)
            target = resolver.resolve(request)
            if target is None:
                continue
            request.target_agent = target
            if not self._replan_tracker.accept(request):
                continue
            return request
        return None

    def _insert_recheck(self, mutator, graph, request: ReplanRequest) -> AgentTaskNode:
        """Insert a BRAND-NEW node (never reset the original) ahead of the
        downstream consumer that needs the evidence (plan §5.4)."""
        prefix = _RECHECK_PREFIX.get(request.target_capability or "",
                                     "recheck")
        base_id = "%s-%s" % (prefix, (request.finding_id or "F")[:8] or "F0")
        node_id = base_id
        seq = 1
        while node_id in graph.nodes:
            node_id = "%s-%d" % (base_id, seq)
            seq += 1
        target_task = _AGENT_TASK_TYPE.get(request.target_agent or "", "review.reliability")
        new_node = AgentTaskNode(
            node_id=node_id, task_type=target_task,
            objective="re-check %s: %s" % (request.finding_id or "", request.reason_summary),
            dependencies=[d for d in graph.nodes if d != node_id],
            agent_id=request.target_agent,
            status=AgentTaskStatus.PENDING)
        new_node.target_capabilities = [d for d in [request.target_capability] if d]
        # point consumers (critic/verifier) at the new evidence node
        mutator.add(new_node, reason=request.reason_code)
        for node in graph.nodes.values():
            if node.node_id == node_id:
                continue
            if node.task_type in ("critique.findings", "verify.findings",
                                  "fix.generate") and not node.dependencies:
                mutator.change_dependency(node.node_id, [node_id], reason="replan-evidence")
        graph.revision += 1
        return new_node

    # -- gather structured replan requests ----------------------------------
    def _critic_replan_requests(self) -> List[Any]:
        if self.delegator is None:
            return []
        requests: List[Any] = []
        for record in self.delegator.artifacts.values():
            if record.get("task_type") != "critique.findings":
                continue
            content = record.get("content") or {}
            for item in (content.get("replan_requests") or []):
                if isinstance(item, ReplanRequest):
                    requests.append(item)
                elif isinstance(item, dict):
                    requests.append(ReplanRequest.from_dict(item))
        return requests

    def _verifier_replan_requests(self) -> List[Any]:
        # Verifier emits low-confidence requests (plan §8.2 Verifier Trigger).
        if self.delegator is None:
            return []
        requests: List[Any] = []
        for record in self.delegator.artifacts.values():
            if record.get("task_type") != "verify.findings":
                continue
            content = record.get("content") or {}
            for item in (content.get("replan_requests") or []):
                requests.append(item if isinstance(item, ReplanRequest)
                               else ReplanRequest.from_dict(item))
        return requests

    def _replan_target(self, graph: CoordinatorTaskGraph) -> Optional[AgentTaskNode]:
        for node in graph.nodes.values():
            if node.task_type.startswith("review."):
                return node
        if graph.nodes:
            return next(iter(graph.nodes.values()))
        return None

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
            "architecture": "six-agent-v2" if self._v2 else "six-agent",
        }


__all__ = ["CoordinatorAgent"]