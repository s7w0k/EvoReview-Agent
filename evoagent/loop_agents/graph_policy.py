"""Truly Dynamic Collaboration Graph (plan §8).

The static ``specialists -> critic -> verifier -> fix`` chain is replaced by a
set of *condition predicates* that decide, from accumulated artifacts/risk,
whether a downstream node (critic / verifier / fix / a specialist recheck) must
actually be added -- and a :class:`GraphMutator` that applies the resulting
graph changes at runtime *(add / remove / replace / change-dependency /
cancel-branch)* *without rewriting the already-completed history*.
"""
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from .models import AgentTaskNode, AgentTaskStatus, CoordinatorTaskGraph


# ---------------------------------------------------------------------------
# condition predicates (plan §8.2).  Each returns (triggered, rationale_code)
# ---------------------------------------------------------------------------

def critic_trigger(summary: Dict[str, Any], risk: Dict[str, Any],
                   findings: List[Dict[str, Any]]) -> tuple:
    level = str(risk.get("level") or "")
    change_types = set(summary.get("change_types") or [])
    if level == "high":
        return True, "HIGH_RISK"
    if len(change_types) >= 3:
        return True, "MULTI_DOMAIN_CHANGE"
    if len(findings or []) >= 3:
        return True, "LOW_CONFIDENCE"
    if any(float(f.get("confidence", 0.0)) < 0.95 for f in findings or []):
        return True, "LOW_CONFIDENCE"
    return False, ""


def verifier_trigger(summary: Dict[str, Any], risk: Dict[str, Any],
                     expected_findings: Any, new_inputs: bool) -> tuple:
    level = str(risk.get("level") or "")
    if level == "high":
        return True, "HIGH_RISK"
    if bool(new_inputs):
        return True, "EXTERNAL_INPUT"
    if int(expected_findings or 0) > 0:
        return True, "EXPECTED_FINDINGS"
    return False, ""


def fix_trigger(accepted_findings: List[Dict[str, Any]],
                execution_policy: Dict[str, Any]) -> tuple:
    if not accepted_findings:
        return False, ""
    remediation = execution_policy.get("remediation")
    fix_policy = execution_policy.get("fix_policy")
    repo_permission = execution_policy.get("repo_permission")
    if remediation or fix_policy:
        if repo_permission is False:
            return False, "NO_REPO_PERMISSION"
        return True, "VERIFIED_AND_ACTIONABLE"
    return False, ""


# ---------------------------------------------------------------------------
# runtime graph mutator (plan §8.3)
# ---------------------------------------------------------------------------

class GraphMutator:
    """Applies safe, history-preserving mutations to the current graph."""

    def __init__(self, graph: CoordinatorTaskGraph):
        self.graph = graph
        self._applied: List[Dict[str, Any]] = []

    def _record(self, change: Dict[str, Any]) -> None:
        self.graph.revision += 1
        change = dict(change)
        change["graph_revision"] = self.graph.revision
        self._applied.append(change)
        self.graph.mutation_history.append(change)

    def add(self, node: AgentTaskNode, after: Optional[Iterable[str]] = None,
            reason: str = "") -> str:
        self.graph.add(node)
        self._record({"op": "add", "node": node.node_id,
                      "reason": reason})
        return node.node_id

    def remove(self, node_id: str, reason: str = "") -> str:
        # Completed nodes may still be removed from *future* scheduling as long
        # as we never rewrite their history -- but standard policy: only pending
        # or failed branches are removable without corruption.
        node = self.graph.nodes.get(node_id)
        if node is None:
            return node_id
        self.graph.remove(node_id)
        self._record({"op": "remove", "node": node_id, "reason": reason})
        return node_id

    def replace(self, node: AgentTaskNode, reason: str = "") -> str:
        self.graph.replace(node)
        self._record({"op": "replace", "node": node.node_id,
                      "reason": reason})
        return node.node_id

    def change_dependency(self, node_id: str, dependencies: List[str],
                      *, append: bool = True, reason: str = "") -> str:
        node = self.graph.nodes.get(node_id)
        if node is None:
            return node_id
        # A node can never depend on itself (plan §3.1 / SELF_DEPENDENCY).
        additions = [d for d in dependencies if d and d != node_id]
        base = [d for d in node.dependencies if d != node_id]
        if append:
            merged = base + [d for d in additions if d not in base]
        else:
            merged = [d for d in base if d not in dependencies]
        node.dependencies = merged
        self._record({"op": "change_dependency", "node": node_id,
                      "reason": reason})
        return node_id

    def cancel_branch(self, node_ids: Iterable[str], reason: str = "") -> List[str]:
        cancelled: List[str] = []
        for nid in node_ids:
            node = self.graph.nodes.get(nid)
            if node is not None and node.status == AgentTaskStatus.PENDING:
                node.status = AgentTaskStatus.REJECTED
                cancelled.append(nid)
        if cancelled:
            self._record({"op": "cancel_branch", "nodes": list(cancelled),
                          "reason": reason})
        return cancelled

    @property
    def applied(self) -> List[Dict[str, Any]]:
        return list(self._applied)


__all__ = [
    "critic_trigger", "verifier_trigger", "fix_trigger", "GraphMutator",
]
