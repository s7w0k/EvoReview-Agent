"""Multi-Agent Observability enrichment (plan §11).

The Base loop already emits ``bus.send(agent, 'trace', kind, detail)``.  This
helper builds the standardised *trace context* so every ``trace`` event carries
the cross-cutting identifiers the plan requires:
``planning_id / graph_id / graph_revision / node_id / agent_id / agent_version /
parent_node_id / replan_request_id / loop_step / tool_name / observation_id /
artifact_id / a2a_task_id``.
"""
from typing import Any, Dict, Optional


def build_trace_context(
    *,
    agent_id: str,
    agent_version: str = "",
    planning_id: str = "",
    graph_id: str = "",
    graph_revision: int = 1,
    node_id: str = "",
    parent_node_id: str = "",
    replan_request_id: str = "",
    loop_step: int = 0,
    tool_name: str = "",
    observation_id: str = "",
    artifact_id: str = "",
    a2a_task_id: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ctx = {
        "agent_id": agent_id,
        "agent_version": agent_version or "six-agent-v2",
        "planning_id": planning_id,
        "graph_id": graph_id,
        "graph_revision": int(graph_revision),
        "node_id": node_id,
        "parent_node_id": parent_node_id,
        "replan_request_id": replan_request_id,
        "loop_step": int(loop_step),
        "tool_name": tool_name,
        "observation_id": observation_id,
        "artifact_id": artifact_id,
        "a2a_task_id": a2a_task_id,
    }
    if extra:
        ctx.update(extra)
    return ctx


__all__ = ["build_trace_context"]