"""Harness gate for proposed task graphs (plan §4.5, §15).

A proposed :class:`CoordinatorTaskGraph` is validated *before* execution.  An
invalid proposal is rejected; the Coordinator may attempt one repair, then falls
back to :class:`FallbackPlanner`.
"""
from typing import Any, Dict, List, Set

from ..models import AgentTaskNode, CoordinatorTaskGraph

_TASK_TO_AGENT = {
    "review.security": "security-agent",
    "review.reliability": "reliability-agent",
    "critique.findings": "critic-agent",
    "verify.findings": "verifier-agent",
    "fix.generate": "fix-agent",
}


class TaskGraphValidator:
    def __init__(self, *, available_agents: Set[str], max_nodes: int = 12,
                 max_depth: int = 6, max_parallel_width: int = 4,
                 known_task_types=None):
        self.available_agents = set(available_agents)
        self.max_nodes = max_nodes
        self.max_depth = max_depth
        self.max_parallel_width = max_parallel_width
        self.known_task_types = set(known_task_types or _TASK_TO_AGENT)

    # -- individual checks --------------------------------------------------
    def validate_agents_exist(self, graph: CoordinatorTaskGraph) -> List[str]:
        errors: List[str] = []
        for node in graph.nodes.values():
            agent = node.agent_id or _TASK_TO_AGENT.get(node.task_type, "")
            if not agent:
                errors.append("node %s: no agent resolved for %s" % (
                    node.node_id, node.task_type))
            elif agent not in self.available_agents:
                errors.append("node %s: unknown agent %r" % (node.node_id, agent))
        return errors

    def validate_task_types(self, graph: CoordinatorTaskGraph) -> List[str]:
        errors: List[str] = []
        for node in graph.nodes.values():
            if node.task_type not in self.known_task_types:
                errors.append("node %s: unknown task_type %r" % (
                    node.node_id, node.task_type))
        return errors

    def validate_dependencies(self, graph: CoordinatorTaskGraph) -> List[str]:
        errors: List[str] = []
        for node in graph.nodes.values():
            for dep in node.dependencies:
                if dep not in graph.nodes:
                    errors.append("node %s: dependency %r does not exist" % (
                        node.node_id, dep))
        return errors

    def validate_no_cycle(self, graph: CoordinatorTaskGraph) -> List[str]:
        # DFS cycle detection over the dependency edges.
        WHITE, GREY, BLACK = 0, 1, 2
        color = {nid: WHITE for nid in graph.nodes}

        def visit(nid: str) -> bool:
            color[nid] = GREY
            for dep in graph.nodes[nid].dependencies:
                if color[dep] == GREY:
                    return True  # back edge -> cycle
                if color[dep] == WHITE and visit(dep):
                    return True
            color[nid] = BLACK
            return False

        if any(visit(nid) for nid in graph.nodes):
            return ["task graph contains a dependency cycle"]
        return []

    def validate_budget(self, graph: CoordinatorTaskGraph) -> List[str]:
        errors: List[str] = []
        node_count = len(graph.nodes)
        if node_count > self.max_nodes:
            errors.append("task graph exceeds max_nodes (%d > %d)" % (
                node_count, self.max_nodes))
        depth = self._max_depth(graph)
        if depth > self.max_depth:
            errors.append("task graph exceeds max_depth (%d > %d)" % (
                depth, self.max_depth))
        width = self._max_width(graph)
        if width > self.max_parallel_width:
            errors.append("task graph exceeds max_parallel_width (%d > %d)" % (
                width, self.max_parallel_width))
        return errors

    def validate(self, graph: CoordinatorTaskGraph) -> List[str]:
        errors: List[str] = []
        errors += self.validate_task_types(graph)
        errors += self.validate_agents_exist(graph)
        errors += self.validate_dependencies(graph)
        errors += self.validate_no_cycle(graph)
        errors += self.validate_budget(graph)
        return errors

    def is_valid(self, graph: CoordinatorTaskGraph) -> bool:
        return not self.validate(graph)

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _max_depth(graph: CoordinatorTaskGraph) -> int:
        memo: Dict[str, int] = {}

        def depth(node_id: str) -> int:
            if node_id in memo:
                return memo[node_id]
            node = graph.nodes[node_id]
            if not node.dependencies:
                memo[node_id] = 1
                return 1
            memo[node_id] = 1 + max(depth(d) for d in node.dependencies)
            return memo[node_id]

        if not graph.nodes:
            return 0
        return max(depth(nid) for nid in graph.nodes)

    @staticmethod
    def _max_width(graph: CoordinatorTaskGraph) -> int:
        # The widest parallel fan-out is the largest set of nodes sharing the
        # same longest-path depth (i.e. all independently schedulable together).
        layers: Dict[int, int] = {}
        memo: Dict[str, int] = {}

        def depth(node_id: str) -> int:
            if node_id in memo:
                return memo[node_id]
            node = graph.nodes[node_id]
            memo[node_id] = 1 + max((depth(d) for d in node.dependencies), default=0)
            return memo[node_id]

        for nid in graph.nodes:
            layers[depth(nid)] = layers.get(depth(nid), 0) + 1
        return max(layers.values(), default=1)


def build_graph_from_tasks(tasks, graph_id: str) -> CoordinatorTaskGraph:
    """Materialise a validated :class:`CoordinatorTaskGraph` from PlannedTasks."""
    graph = CoordinatorTaskGraph(graph_id=graph_id)
    for task in tasks:
        graph.add(AgentTaskNode(
            node_id=task.task_id, task_type=task.task_type,
            objective=task.objective, dependencies=list(task.dependencies),
            agent_id=task.agent_id,
            target_capabilities=list(getattr(task, "target_capabilities", [])),
            serial=bool(getattr(task, "serial", False))))
    return graph


__all__ = ["TaskGraphValidator", "build_graph_from_tasks", "_TASK_TO_AGENT"]