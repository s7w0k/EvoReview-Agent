"""Six-core-Agent Loop Engineering package (plan §10-§19).

Reusable building blocks for the 6 core agents (Coordinator, Security,
Reliability, Critic, Verifier, Fix) each with its own ``BaseLoopAgent`` loop,
governed per-agent tool registries, A2A task types, the Coordinator-side
:class:`Delegator` and the :class:`LoopAgentHost` that serves a ``BaseLoopAgent``
over in-process or HTTP A2A.
"""
from .base import BaseLoopAgent
from .coordinator import CoordinatorAgent
from .critic import CriticAgent
from .delegator import Delegator
from .fix import FixAgent
from .models import (
    AgentPlanState,
    AgentTaskNode,
    AgentTaskStatus,
    CoordinatorTaskGraph,
    TASK_TYPES,
)
from .reliability import ReliabilityAgent
from .security import SecurityAgent
from .service_host import LoopAgentHost, LoopTaskRecord, LoopTaskStore
from .stepper import (
    PlanTracker,
    final_action,
    last_observation,
    last_tool,
    observations,
    result_findings,
    tool_action,
    tool_results,
)
from .tools import (
    AGENT_SPECS,
    ExpertContext,
    build_agent_policy,
    build_delegate_handlers,
    build_expert_context,
    build_expert_definitions,
    build_loop_registry,
    finding_key,
    registry_for_task,
)
from .verifier import VerifierAgent

__all__ = [
    "AgentPlanState",
    "AgentTaskNode",
    "AgentTaskStatus",
    "AGENT_SPECS",
    "BaseLoopAgent",
    "CoordinatorAgent",
    "CoordinatorTaskGraph",
    "CriticAgent",
    "Delegator",
    "ExpertContext",
    "FixAgent",
    "LoopAgentHost",
    "LoopTaskRecord",
    "LoopTaskStore",
    "PlanTracker",
    "ReliabilityAgent",
    "SecurityAgent",
    "TASK_TYPES",
    "VerifierAgent",
    "build_agent_policy",
    "build_delegate_handlers",
    "build_expert_context",
    "build_expert_definitions",
    "build_loop_registry",
    "final_action",
    "finding_key",
    "last_observation",
    "last_tool",
    "observations",
    "registry_for_task",
    "result_findings",
    "tool_action",
    "tool_results",
]