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
from .feature_flags import MultiAgentFeatureFlags
from .events import (
    AGENT_COMPLETED, AGENT_FAILED, ARTIFACT_SUPERSEDED,
    CRITIQUE_EMITTED, FINDING_UPDATED, FINDINGS_EMITTED,
    FIX_COMPLETED, FIX_REQUESTED, GRAPH_MUTATED, REPLAN_REQUESTED,
    VERIFICATION_COMPLETED, RuntimeGraphEvent,
)
from .invalidation import invalidate_downstream
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
        "delegate_agent", "delegate_agent_batch",
        "discover_agents", "get_agent_artifacts", "cancel_agent_task",
    )

    def __init__(self, delegator=None, *, max_steps=None, timeout_seconds=None,
                 max_replans: int = 1, execution_policy=None, tools=None, bus=None,
                 mode: str = "v1",
                 feature_flags: Optional[MultiAgentFeatureFlags] = None):
        super().__init__(
            max_steps or 16, timeout_seconds or 60,
            # The task policy governs routing and tools.  Coordinator protocol
            # overhead (semantic summary, risk profile, delegation and final)
            # must not consume the reviewed task's specialist step budget.
            execution_policy=None,
            tools=tools, bus=bus,
        )
        self.delegator = delegator
        self.max_replans = max(0, max_replans)
        self.mode = mode if mode in ("v1", "v2") else "v1"
        self.execution_policy = execution_policy
        self._replan_count = 0
        self._graph_revision = 1
        self._replan_tracker = ReplanTracker()
        self._last_rationale: List[str] = []
        self.flags = feature_flags or MultiAgentFeatureFlags()
        # Expose flags so host/reviewer can read the effective parallelisation
        # budget and the loop depth switch for specialists.
        self.max_parallel_agents = self.flags.effective_max_parallel

    @property
    def _deep_loop(self) -> bool:
        return self.flags.deep_loop

    # -- DAG validation helper (only when the scheduler is enabled) ----------
    @property
    def _v2(self) -> bool:
        # ``mode=v2`` is authoritative.  A second environment gate previously
        # made Evaluation V4 silently execute v1 even when the v2 architecture
        # was requested, producing identical ablation traces.
        return self.mode == "v2"

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
        state["_processed_artifacts"] = []
        state["_runtime_artifacts"] = {}
        state["_runtime_events"] = []
        state["_processed_replans"] = []
        state["_finding_versions"] = {}
        state["_verification_version"] = 0
        state["_fix_stale_inputs"] = 0
        state["feature_flags_snapshot"] = self.flags.to_dict()
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
        # ablation flags: critic / verifier off => remove those stages entirely
        # (plan §4.4) so the flag genuinely changes which agents run.
        self._apply_stage_flags(graph)
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
        planner = SemanticPlanner() if self.flags.planner else FallbackPlanner()
        decision = planner.plan(ctx)
        self._last_rationale = list(getattr(decision, "rationale_codes", []))
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

        # Planner creates specialists only.  Critic/Verifier/Fix are inserted
        # from observed artifacts by ``_apply_runtime_graph_policy``.
        return graph

    def _apply_stage_flags(self, graph: CoordinatorTaskGraph) -> None:
        if self.flags.critic:
            pass  # keep critic
        else:
            self._drop_nodes(graph, ("critique.findings",))
        if not self.flags.verifier:
            self._drop_nodes(graph, ("verify.findings",))

    @staticmethod
    def _drop_nodes(graph: CoordinatorTaskGraph, task_types: tuple) -> None:
        for node in list(graph.nodes.values()):
            if node.task_type in task_types:
                graph.remove(node.node_id)

    def _add_plan_policy(self, mutator, graph) -> None:
        """Re-inforce fix presence when remediation is allowed (plan §8 Fix trig).

        Correct dependency semantics: ``Fix.dependencies = [Verifier]`` -- never
        a self-dependency (plan §3.1, §3.2).
        """
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
        deps = [verifier] if verifier else []
        graph.add(AgentTaskNode(
            node_id="fix", task_type="fix.generate",
            objective="generate a verified repair for the findings",
            dependencies=deps, agent_id="fix-agent"))

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
            state["_semantic_summary"] = summary
            state["_risk_profile"] = risk
            graph = self._build_graph_v2(state, summary, risk)
            state["task_graph"] = graph
            state["_graph_plan"] = {
                "plan_id": uuid.uuid4().hex,
                "graph_id": graph.graph_id,
                "rationale": [],
            }

        # Phase B: reconcile + schedule fresh nodes through the scheduler.
        budget = ConcurrencyBudget(
            max_parallel_agents=self.flags.effective_max_parallel)
        scheduler = TaskGraphScheduler(graph, budget=budget)
        scheduler.reconcile()
        runtime_events = self._sync_runtime_results(state, graph)
        self._apply_runtime_graph_policy(state, graph, runtime_events)
        batch = state.get("_batch") or scheduler.next_batch()
        if batch:
            state["_batch"] = []
            if len(batch) > 1:
                return self._delegate_batch(state, graph, batch, plan)
            return self._delegate(state, graph.nodes[batch[0]], plan)

        # Phase C: graph is terminal only after runtime policy has had a chance
        # to insert downstream or replan nodes from the latest artifacts.
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
            findings = self._findings_for_downstream(state) if self._v2 else (
                self.delegator.specialist_findings() if self.delegator else [])
            if node.task_type == "fix.generate":
                findings = [dict(f, **{
                    "verification_artifact_id": node.metadata.get(
                        "verification_artifact_id", ""),
                    "verification_version": node.metadata.get(
                        "verification_version", 0),
                    "latest_finding_version": node.metadata.get(
                        "finding_versions", {}).get(_finding_key(f), 1),
                }) for f in findings]
        else:
            findings = []
        return tool_action("delegate_agent", {
            "agent_id": node.agent_id or "",
            "task_type": node.task_type,
            "objective": objective,
            "findings": findings,
            "diff": state.get("diff") or "",
        })

    def _delegate_batch(self, state, graph, batch: List[str], plan) -> Dict[str, Any]:
        """Delegate a ready batch concurrently via ``delegate_agent_batch``."""
        tasks = []
        for node_id in batch:
            node = graph.nodes[node_id]
            node.status = AgentTaskStatus.RUNNING
            plan.begin("task:" + node.task_type)
            if node.task_type in ("critique.findings", "verify.findings",
                                  "fix.generate"):
                findings = self._findings_for_downstream(state) if self._v2 else (
                    self.delegator.specialist_findings() if self.delegator else [])
            else:
                findings = []
            tasks.append({
                "node_id": node.node_id, "agent_id": node.agent_id or "",
                "task_type": node.task_type, "objective": str(node.objective),
                "findings": findings, "diff": state.get("diff") or "",
            })
        return tool_action("delegate_agent_batch", {"tasks": tasks})

    # -- finalize (v1) ------------------------------------------------------
    def _finalize(self, state, graph, plan) -> Dict[str, Any]:
        if not state.get("_replan_checked"):
            state["_replan_checked"] = True
            requests = self._critic_replan_requests() if self.flags.targeted_replan else []
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
        self._replan_count = int(state.get("_replan_count", 0))
        self._graph_revision = int(getattr(graph, "revision", 1))
        self._runtime_state = state
        self._runtime_graph = graph
        plan.complete("replan on evidence gaps").complete("finalize")
        return final_action(agent_id=self.agent_id)

    # -- result -> event -> runtime graph policy ---------------------------
    def _sync_runtime_results(self, state, graph) -> List[RuntimeGraphEvent]:
        """Attach newly returned A2A artifacts to graph nodes and version them."""
        if self.delegator is None:
            return []
        processed = set(state.get("_processed_artifacts") or [])
        runtime_artifacts = state.setdefault("_runtime_artifacts", {})
        events: List[RuntimeGraphEvent] = []
        candidates = [
            (task_id, record) for task_id, record in self.delegator.artifacts.items()
            if task_id not in processed
        ]
        for task_id, record in candidates:
            node = next((n for n in graph.nodes.values()
                         if not n.artifact_ids
                         and n.agent_id == record.get("agent_id")
                         and n.task_type == record.get("task_type")
                         and n.status in (AgentTaskStatus.RUNNING,
                                          AgentTaskStatus.COMPLETED)), None)
            if node is None:
                continue
            node.status = (AgentTaskStatus.COMPLETED
                           if record.get("status") == "completed"
                           else AgentTaskStatus.FAILED)
            node.artifact_ids.append(task_id)
            content = record.get("content") or {}
            inputs = [aid for dep in node.dependencies
                      for aid in graph.nodes.get(dep, AgentTaskNode('', '', '')).artifact_ids]
            artifact = {
                "artifact_id": task_id, "producer_node": node.node_id,
                "agent_id": node.agent_id, "task_type": node.task_type,
                "status": node.status, "artifact_version": 1,
                "input_artifact_ids": inputs, "content": content,
            }
            if node.task_type.startswith("review."):
                is_recheck = bool(node.metadata.get("replan_request_id"))
                changed_old: List[str] = []
                for finding in content.get("findings") or []:
                    key = _finding_key(finding)
                    current = int(state["_finding_versions"].get(key, 0))
                    version = current + 1 if is_recheck else max(1, current)
                    state["_finding_versions"][key] = version
                    finding["finding_id"] = finding.get("finding_id") or key
                    finding["finding_version"] = version
                    finding["artifact_id"] = task_id
                    if is_recheck:
                        finding.setdefault("deep_evidence", {})["targeted_recheck"] = True
                        for old in runtime_artifacts.values():
                            if old.get("status") == AgentTaskStatus.SUPERSEDED:
                                continue
                            if old.get("task_type", "").startswith("review.") and any(
                                _finding_key(item) == key for item in
                                (old.get("content") or {}).get("findings") or []
                            ):
                                old["status"] = AgentTaskStatus.SUPERSEDED
                                changed_old.append(old["artifact_id"])
                                events.append(RuntimeGraphEvent(
                                    ARTIFACT_SUPERSEDED,
                                    node_id=str(old.get("producer_node") or ""),
                                    artifact_id=old["artifact_id"],
                                    detail={"replaced_by": task_id},
                                ))
                artifact["finding_versions"] = {
                    _finding_key(f): int(f.get("finding_version", 1))
                    for f in content.get("findings") or []
                }
                events.append(RuntimeGraphEvent(
                    FINDING_UPDATED if is_recheck else FINDINGS_EMITTED,
                    node.node_id, task_id,
                    {"count": len(content.get("findings") or []),
                     "recheck": is_recheck, "changed_artifact_ids": changed_old},
                ))
                if changed_old:
                    events.extend(invalidate_downstream(
                        graph, changed_old, runtime_artifacts))
            elif node.task_type == "critique.findings":
                events.append(RuntimeGraphEvent(
                    CRITIQUE_EMITTED, node.node_id, task_id,
                    {"replan_requests": len(content.get("replan_requests") or [])}))
                for request in content.get("replan_requests") or []:
                    detail = request.to_dict() if isinstance(request, ReplanRequest) else dict(request)
                    events.append(RuntimeGraphEvent(
                        REPLAN_REQUESTED, node.node_id, task_id, detail))
            elif node.task_type == "verify.findings":
                state["_verification_version"] += 1
                artifact["verification_version"] = state["_verification_version"]
                for decision in (content.get("decisions") or {}).values():
                    decision["verification_version"] = state["_verification_version"]
                    source = next((f for f in self._latest_specialist_findings(state)
                                   if _finding_key(f) == decision.get("finding_id")), None)
                    decision["finding_version"] = int(
                        (source or {}).get("finding_version", 1))
                events.append(RuntimeGraphEvent(
                    VERIFICATION_COMPLETED, node.node_id, task_id,
                    {"verification_version": state["_verification_version"]}))
            elif node.task_type == "fix.generate":
                events.append(RuntimeGraphEvent(
                    FIX_COMPLETED, node.node_id, task_id,
                    {"verification_artifact_id": node.metadata.get(
                        "verification_artifact_id", "")}))
            runtime_artifacts[task_id] = artifact
            processed.add(task_id)
            events.append(RuntimeGraphEvent(
                AGENT_COMPLETED if node.status == AgentTaskStatus.COMPLETED
                else AGENT_FAILED, node.node_id, task_id,
                {"agent_id": node.agent_id, "task_type": node.task_type}))
        state["_processed_artifacts"] = sorted(processed)
        state["_runtime_events"].extend(event.to_dict() for event in events)
        return events

    @staticmethod
    def _has_live_node(graph, task_type: str) -> bool:
        return any(n.task_type == task_type and n.status != AgentTaskStatus.SUPERSEDED
                   for n in graph.nodes.values())

    @staticmethod
    def _completed_nodes(graph, prefix: str) -> List[AgentTaskNode]:
        return [n for n in graph.nodes.values()
                if n.task_type.startswith(prefix)
                and n.status == AgentTaskStatus.COMPLETED]

    def _latest_specialist_findings(self, state) -> List[Dict[str, Any]]:
        merged: Dict[str, Dict[str, Any]] = {}
        for artifact in state.get("_runtime_artifacts", {}).values():
            if artifact.get("status") != AgentTaskStatus.COMPLETED:
                continue
            if not str(artifact.get("task_type") or "").startswith("review."):
                continue
            for finding in (artifact.get("content") or {}).get("findings") or []:
                key = _finding_key(finding)
                current = merged.get(key)
                if current is None or int(finding.get("finding_version", 1)) >= int(
                        current.get("finding_version", 1)):
                    merged[key] = finding
        return list(merged.values())

    def _findings_for_downstream(self, state) -> List[Dict[str, Any]]:
        """Use the latest non-superseded Critic decision when one exists."""
        critics = [a for a in state.get("_runtime_artifacts", {}).values()
                   if a.get("task_type") == "critique.findings"
                   and a.get("status") == AgentTaskStatus.COMPLETED]
        if critics:
            return list((critics[-1].get("content") or {}).get(
                "accepted_findings") or [])
        return self._latest_specialist_findings(state)

    def _add_runtime_node(self, state, graph, node: AgentTaskNode,
                          reason: str) -> AgentTaskNode:
        mutator = graph_policy.GraphMutator(graph)
        mutator.add(node, reason=reason)
        state["_runtime_events"].append(RuntimeGraphEvent(
            GRAPH_MUTATED, node.node_id, detail={
                "op": "add", "reason": reason,
                "graph_revision": graph.revision,
            }).to_dict())
        return node

    def _apply_runtime_graph_policy(self, state, graph,
                                    events: List[RuntimeGraphEvent]) -> None:
        """Evaluate artifacts after every completed batch, before scheduling."""
        findings = self._latest_specialist_findings(state)
        summary = state.get("_semantic_summary") or {}
        risk = state.get("_risk_profile") or {}

        # Critic/Verifier replan requests are consumed immediately.  This path
        # runs before any downstream verifier/fix insertion.
        request = self._pick_replan_request(state, graph) \
            if self.flags.targeted_replan else None
        if request is not None and state.get("_replan_count", 0) < self.max_replans:
            fingerprint = request.fingerprint()
            if fingerprint not in state["_processed_replans"]:
                state["_processed_replans"].append(fingerprint)
                state["_replan_count"] += 1
                mutator = graph_policy.GraphMutator(graph)
                new_node = self._insert_recheck(mutator, graph, request)
                state["_runtime_events"].append(RuntimeGraphEvent(
                    GRAPH_MUTATED, new_node.node_id, detail={
                        "op": "targeted_replan", "target": new_node.agent_id,
                        "request_id": request.request_id,
                        "graph_revision": graph.revision,
                    }).to_dict())
                return

        review_nodes = [n for n in graph.nodes.values()
                        if n.task_type.startswith("review.")]
        initial_done = bool(review_nodes) and all(
            n.status in (AgentTaskStatus.COMPLETED, AgentTaskStatus.SUPERSEDED)
            for n in review_nodes if not n.metadata.get("replan_request_id"))
        recheck_completed = any(
            n.metadata.get("replan_request_id")
            and n.status == AgentTaskStatus.COMPLETED for n in review_nodes)
        critics = self._completed_nodes(graph, "critique.")
        verifiers = self._completed_nodes(graph, "verify.")

        if initial_done and not critics and not self._has_live_node(
                graph, "critique.findings") and findings:
            triggered, reason = graph_policy.critic_trigger(summary, risk, findings)
            if self.flags.critic and triggered:
                deps = [n.node_id for n in review_nodes
                        if not n.metadata.get("replan_request_id")]
                self._add_runtime_node(state, graph, AgentTaskNode(
                    node_id="critic", task_type="critique.findings",
                    objective="challenge current findings and evidence",
                    dependencies=deps, agent_id="critic-agent"), reason)
                return

        latest_source = None
        if recheck_completed:
            latest_source = next(n for n in reversed(review_nodes)
                                 if n.metadata.get("replan_request_id")
                                 and n.status == AgentTaskStatus.COMPLETED)
        elif critics:
            latest_source = critics[-1]
        elif initial_done:
            latest_source = review_nodes[-1]

        downstream_findings = self._findings_for_downstream(state)
        if latest_source is not None and downstream_findings and self.flags.verifier \
                and not self._has_live_node(graph, "verify.findings"):
            triggered, reason = graph_policy.verifier_trigger(
                summary, risk, len(downstream_findings), recheck_completed)
            if triggered:
                seq = 1 + sum(n.task_type == "verify.findings"
                              for n in graph.nodes.values())
                self._add_runtime_node(state, graph, AgentTaskNode(
                    node_id="verifier-v%d" % seq,
                    task_type="verify.findings",
                    objective="verify latest finding versions",
                    dependencies=[latest_source.node_id], agent_id="verifier-agent",
                    metadata={"finding_versions": dict(state["_finding_versions"])}),
                    reason)
                return

        if verifiers and not self._has_live_node(graph, "fix.generate"):
            accepted, _ = self._arbitrate(state)
            base_policy = (self.execution_policy.to_dict()
                           if getattr(self.execution_policy, "to_dict", None)
                           else self.execution_policy) or {}
            triggered, reason = graph_policy.fix_trigger(accepted, base_policy)
            if triggered:
                latest = verifiers[-1]
                verification_id = latest.artifact_ids[-1] if latest.artifact_ids else ""
                metadata = {
                    "verification_artifact_id": verification_id,
                    "verification_version": state["_verification_version"],
                    "finding_versions": dict(state["_finding_versions"]),
                }
                state["_runtime_events"].append(RuntimeGraphEvent(
                    FIX_REQUESTED, latest.node_id, verification_id, metadata).to_dict())
                self._add_runtime_node(state, graph, AgentTaskNode(
                    node_id="fix-v%d" % state["_verification_version"],
                    task_type="fix.generate", objective="fix latest verified finding",
                    dependencies=[latest.node_id], agent_id="fix-agent",
                    serial=True, metadata=metadata), reason)

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
        source_node = next((n.node_id for n in reversed(list(graph.nodes.values()))
                            if n.task_type in ("critique.findings",
                                               "verify.findings")
                            and n.status == AgentTaskStatus.COMPLETED), "")
        new_node = AgentTaskNode(
            node_id=node_id, task_type=target_task,
            objective="re-check %s: %s" % (request.finding_id or "", request.reason_summary),
            dependencies=[source_node] if source_node else [],
            agent_id=request.target_agent,
            status=AgentTaskStatus.PENDING,
            metadata={
                "replan_request_id": request.request_id,
                "finding_id": request.finding_id or "",
                "requested_action": request.requested_action,
            })
        new_node.target_capabilities = [d for d in [request.target_capability] if d]
        mutator.add(new_node, reason=request.reason_code)
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
    def _arbitrate(self, state=None) -> tuple:
        if self.delegator is None:
            return [], []
        state = state or getattr(self, "_runtime_state", None)
        specialists = (self._findings_for_downstream(state)
                       if state is not None else self.delegator.specialist_findings())
        decisions: Dict[str, Any] = {}
        if state is not None:
            active_verifiers = [a for a in state.get("_runtime_artifacts", {}).values()
                                if a.get("task_type") == "verify.findings"
                                and a.get("status") == AgentTaskStatus.COMPLETED]
            if active_verifiers:
                latest = max(active_verifiers,
                             key=lambda a: int(a.get("verification_version", 0)))
                decisions.update((latest.get("content") or {}).get("decisions") or {})
        else:
            decisions = self._verifier_decisions()
        accepted: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        for finding in specialists:
            decision = decisions.get(_finding_key(finding))
            # When verification is disabled by policy/ablation, deterministic
            # arbitration preserves specialist findings.  Once a verifier ran,
            # only its latest-version decision is authoritative.
            if not decisions or (decision is not None and decision.get("verified")):
                accepted.append(finding)
            else:
                rejected.append(finding)
        return accepted, rejected

    def build_artifact(self, result) -> Dict[str, Any]:
        state = getattr(self, "_runtime_state", None)
        graph = getattr(self, "_runtime_graph", None)
        accepted, rejected = self._arbitrate(state)
        collaborations: List[str] = []
        if self.delegator is not None:
            for record in self.delegator.artifacts.values():
                agent_id = record.get("agent_id") or ""
                if agent_id and agent_id not in collaborations:
                    collaborations.append(agent_id)
        loop_steps: Dict[str, int] = {}
        if self.delegator is not None:
            for record in self.delegator.artifacts.values():
                metadata = (record.get("content") or {}).get("_a2a_metadata") or []
                if metadata:
                    loop_steps[record.get("agent_id") or ""] = (
                        loop_steps.get(record.get("agent_id") or "", 0)
                        + int(metadata[-1].get("steps") or 0))
        runtime_artifacts = (state or {}).get("_runtime_artifacts", {})
        superseded = [a["artifact_id"] for a in runtime_artifacts.values()
                      if a.get("status") == AgentTaskStatus.SUPERSEDED]
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
            "rationale_codes": list(self._last_rationale),
            "collaborations": collaborations,
            "graph_mutations": list(getattr(self, "_graph_mutations", [])),
            "task_graph": graph.to_dict() if graph is not None else {},
            "graph_shapes": [
                {"node_id": n.node_id, "task_type": n.task_type,
                 "dependencies": list(n.dependencies), "status": n.status,
                 "artifact_ids": list(n.artifact_ids),
                 "metadata": dict(n.metadata)}
                for n in graph.nodes.values()
            ] if graph is not None else [],
            "runtime_events": list((state or {}).get("_runtime_events", [])),
            "runtime_artifacts": list(runtime_artifacts.values()),
            "superseded_artifacts": superseded,
            "feature_flags_snapshot": self.flags.to_dict(),
            "parallel_batches": list(getattr(self.delegator, "batch_traces", []))
            if self.delegator is not None else [],
            "loop_steps_by_agent": loop_steps,
            "tool_calls_by_agent": loop_steps,
            "called_agents": collaborations,
            "replan_targets": [
                e.get("detail", {}).get("target") for e in
                (state or {}).get("_runtime_events", [])
                if e.get("detail", {}).get("op") == "targeted_replan"
            ],
            "verification_version": int((state or {}).get(
                "_verification_version", 0)),
            "finding_versions": dict((state or {}).get("_finding_versions", {})),
            "fix_stale_inputs": int((state or {}).get("_fix_stale_inputs", 0)),
        }


__all__ = ["CoordinatorAgent"]
