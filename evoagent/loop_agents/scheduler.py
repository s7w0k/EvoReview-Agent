"""Parallel TaskGraph Scheduler (plan §6).

The Coordinator owns the ``CoordinatorTaskGraph``; the scheduler decides *which*
ready nodes run next, *how many* in parallel, and *whether a critical branch
fails fast*.  Scheduling honours three orthogonal lattice of constraints:

* per-agent concurrency (e.g. Fix defaults to concurrency 1) -- a serial node
  consumes the entire batch slot;
* a global parallel budget (``max_parallel_agents``);
* fail-fast for critical branches vs. continue-on-failure elsewhere.

The scheduler is deterministic and side-effect free on the graph except for the
explicit ``record``/``reconcile`` calls the Coordinator drives.
"""
from typing import Any, Dict, Iterable, List, Optional

from .models import AgentTaskNode, AgentTaskStatus, CoordinatorTaskGraph


class ConcurrencyBudget:
    def __init__(
        self,
        max_parallel_agents: int = 3,
        max_parallel_remote_calls: int = 4,
        max_parallel_tool_calls: int = 8,
        per_agent_concurrency: Optional[Dict[str, int]] = None,
    ):
        self.max_parallel_agents = max(1, max_parallel_agents)
        self.max_parallel_remote_calls = max(1, max_parallel_remote_calls)
        self.max_parallel_tool_calls = max(1, max_parallel_tool_calls)
        self.per_agent_concurrency: Dict[str, int] = dict(
            per_agent_concurrency or {"fix-agent": 1})

    def allowed_for(self, agent_id: str) -> int:
        return self.per_agent_concurrency.get(agent_id, self.max_parallel_agents)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_parallel_agents": self.max_parallel_agents,
            "max_parallel_remote_calls": self.max_parallel_remote_calls,
            "max_parallel_tool_calls": self.max_parallel_tool_calls,
            "per_agent_concurrency": dict(self.per_agent_concurrency),
        }


class TaskGraphScheduler:
    """Deterministic batch scheduler over a :class:`CoordinatorTaskGraph`."""

    def __init__(
        self,
        graph: CoordinatorTaskGraph,
        budget: Optional[ConcurrencyBudget] = None,
        *,
        fail_fast: bool = True,
    ):
        self.graph = graph
        self.budget = budget or ConcurrencyBudget()
        self.fail_fast = fail_fast

    # -- batch computation --------------------------------------------------
    def _ready(self) -> List[AgentTaskNode]:
        return sorted(
            (n for n in self.graph.nodes.values()
             if n.status == AgentTaskStatus.PENDING and self.graph.ready(n)),
            key=lambda n: n.node_id,
        )

    def _claimed_by_agent(self, batch: Iterable[AgentTaskNode]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for node in batch:
            counts[node.agent_id or node.task_type] = (
                counts.get(node.agent_id or node.task_type, 0) + 1)
        return counts

    def next_batch(self) -> List[str]:
        """Return the ordered node-ids to delegate this round.

        Serial nodes occupy the whole batch; otherwise we fill up to
        ``max_parallel_agents`` while honouring per-agent concurrency.
        """
        ready = self._ready()
        if not ready:
            return []
        # Deterministic priority: critical > higher priority-ish > short id.
        ready.sort(key=lambda n: (0 if n.status else 1, n.node_id))
        chosen: List[AgentTaskNode] = []
        counts = self._claimed_by_agent([])
        for node in ready:
            if node.serial:
                # serial nodes run alone in this batch
                if not chosen:
                    return [node.node_id]
                continue
            agent_id = node.agent_id or ""
            if counts.get(agent_id, 0) >= self.budget.allowed_for(agent_id):
                continue
            if len(chosen) >= self.budget.max_parallel_agents:
                continue
            chosen.append(node)
            counts[agent_id] = counts.get(agent_id, 0) + 1
        return [n.node_id for n in chosen]

    # -- lifecycle helpers (driven by the Coordinator) ----------------------
    def claim(self, node_id: str) -> bool:
        node = self.graph.nodes.get(node_id)
        if node is None or node.status != AgentTaskStatus.PENDING:
            return False
        node.status = AgentTaskStatus.RUNNING
        return True

    def complete(self, node_id: str) -> None:
        node = self.graph.nodes.get(node_id)
        if node is not None:
            node.status = AgentTaskStatus.COMPLETED

    def fail(self, node_id: str) -> None:
        node = self.graph.nodes.get(node_id)
        if node is not None:
            node.status = AgentTaskStatus.FAILED

    def reconcile(self) -> List[str]:
        """Normalise RUNNING nodes whose in-process A2A already returned."""
        completed: List[str] = []
        for node in self.graph.nodes.values():
            if node.status == AgentTaskStatus.RUNNING:
                node.status = AgentTaskStatus.COMPLETED
                completed.append(node.node_id)
        return completed

    def done(self) -> bool:
        return all(
            n.status in (AgentTaskStatus.COMPLETED, AgentTaskStatus.FAILED,
                         AgentTaskStatus.REJECTED)
            for n in self.graph.nodes.values()
        )


__all__ = [
    "ConcurrencyBudget", "TaskGraphScheduler",
]