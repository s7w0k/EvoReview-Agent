"""Six-core-Agent Loop Engineering package (plan §10-§19).

Reusable building blocks for the 6 core agents (Coordinator, Security,
Reliability, Critic, Verifier, Fix) each with its own ``BaseLoopAgent`` loop, governed per-agent tool registries, A2A task types, the Coordinator-side
:class:`Delegator` and the :class:`LoopAgentHost` that serves a ``BaseLoopAgent``
over in-process or HTTP A2A.
"""
import os

from .attribution import attribute_failure, emit_attribution, explain
from .base import BaseLoopAgent
from .coordinator import CoordinatorAgent
from .critic import CriticAgent
from .deep_loop import (
    STOP_CODES, evaluate_stop_condition, pick_verification_strategy,
    select_verifier_strategy_for,
)
from .delegator import Delegator
from .failure_injection import FAILURE_CATALOG, FailureInjector, inject
from .feature_flags import (
    MultiAgentFeatureFlags, ablation_variant, flags_from_dict,
)
from .fix import FixAgent
from .graph_policy import GraphMutator
from .observability import build_trace_context
from .replan import (
    CAPABILITY_AGENT, REASON_CODES, ReplanBudget, ReplanRequest,
    ReplanTargetResolver, ReplanTracker, emit_replan_request,
)
from .scheduler import ConcurrencyBudget, TaskGraphScheduler

# Feature flag: enables the v2 Parallel TaskGraph Scheduler / Semantic Dynamic
# Planner path in the Coordinator (plan §4, §6).  Disabled by default so the
# production ``six-agent`` path is byte-for-byte unchanged.
scheduling_enabled = os.getenv("EVOAGENT_V2_SCHEDULING", "0") == "1"
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

__all__ = ["AgentPlanState",
    "AgentTaskNode",
    "AgentTaskStatus",
    "AGENT_SPECS",
    "BaseLoopAgent",
    "CAPABILITY_AGENT",
    "CoordinatorAgent",
    "CoordinatorTaskGraph",
    "CriticAgent",
    "ConcurrencyBudget",
    "Delegator",
    "ExpertContext",
    "FAILURE_CATALOG",
    "FixAgent",
    "FailureInjector",
    "GraphMutator",
    "LoopAgentHost",
    "LoopTaskRecord",
    "LoopTaskStore",
    "MultiAgentFeatureFlags",
    "PlanTracker",
    "REASON_CODES",
    "ReplanBudget",
    "ReplanRequest",
    "ReplanTargetResolver",
    "ReplanTracker",
    "ReliabilityAgent",
    "SecurityAgent",
    "STOP_CODES",
    "TASK_TYPES",
    "TaskGraphScheduler",
    "VerifierAgent",
    "attribute_failure",
    "ablation_variant",
    "build_agent_policy",
    "build_delegate_handlers",
    "build_expert_context",
    "build_expert_definitions",
    "build_loop_registry",
    "build_trace_context",
    "emit_attribution",
    "emit_replan_request",
    "evaluate_stop_condition",
    "explain",
    "final_action",
    "finding_key",
    "inject",
    "flags_from_dict",
    "last_observation",
    "last_tool",
    "observations",
    "pick_verification_strategy",
    "registry_for_task",
    "result_findings",
    "scheduling_enabled",
    "select_verifier_strategy_for",
    "tool_action",
    "tool_results",
]