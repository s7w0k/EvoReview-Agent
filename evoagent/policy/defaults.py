"""Default system policies for each risk level.

These encode the plan's reference budgets (section 4.5):

* low: 1 agent, no verification
* medium: 2 agents, critic + verifier
* high: 3 agents, critic + evidence + verifier + sandbox
"""
from .models import (
    AgentPolicy,
    ExecutionBudget,
    ExecutionPolicy,
    RetryPolicy,
    ToolPermission,
    VerificationPolicy,
)

# Runtime policy identifiers must match Reviewer.name exactly; otherwise
# MultiAgentCoordinator falls back to all agents and routing flags are inert.
AGENT_RELIABILITY = "reliability-agent"
AGENT_SEMANTIC = "semantic-agent"
AGENT_SECURITY = "security-agent"

# Tools that every policy must be able to execute.  Tighter allow-lists can be
# layered per tenant / repository / task by the resolver.
READ_TOOLS = {
    "read_file": ToolPermission("read_file", allow=True, max_calls=40),
    "search_code": ToolPermission("search_code", allow=True, max_calls=20),
    "find_callers": ToolPermission("find_callers", allow=True, max_calls=10),
    "list_files": ToolPermission("list_files", allow=True, max_calls=10),
    "run_tests": ToolPermission("run_tests", allow=True, max_calls=5, requires_sandbox=True),
    "push_fix": ToolPermission(
        "push_fix", allow=False, max_calls=1, requires_approval=True,
    ),
}


def _policy(
    policy_id: str,
    risk_level: str,
    budget: ExecutionBudget,
    retry: RetryPolicy,
    verification: VerificationPolicy,
    agents: AgentPolicy,
    version: int = 1,
) -> ExecutionPolicy:
    permissions = [
        permission for name, permission in sorted(READ_TOOLS.items())
    ]
    return ExecutionPolicy(
        policy_id=policy_id,
        policy_version=version,
        risk_level=risk_level,
        budget=budget,
        retry=retry,
        verification=verification,
        agents=agents,
        tool_permissions=permissions,
        metadata={"source": "system-default"},
    )


DEFAULT_POLICIES = {
    "low": _policy(
        "system-low",
        "low",
        ExecutionBudget(max_steps=3, max_tool_calls=5, max_wall_time_seconds=60),
        RetryPolicy(max_retries=1, retryable_failures={"MODEL_TIMEOUT", "TOOL_TIMEOUT"}),
        VerificationPolicy(critic_required=False, evidence_required=False,
                           verifier_required=False),
        AgentPolicy(enabled_agents=[AGENT_RELIABILITY], max_parallel_agents=1),
    ),
    "medium": _policy(
        "system-medium",
        "medium",
        ExecutionBudget(max_steps=6, max_tool_calls=12, max_wall_time_seconds=150),
        RetryPolicy(max_retries=2, backoff_seconds=1.0, exponential_backoff=True,
                    retryable_failures={"MODEL_TIMEOUT", "MODEL_RATE_LIMIT", "TOOL_TIMEOUT"}),
        VerificationPolicy(critic_required=True, evidence_required=False,
                           verifier_required=True),
        AgentPolicy(enabled_agents=[AGENT_SECURITY, AGENT_RELIABILITY, AGENT_SEMANTIC],
                    max_parallel_agents=3,
                    fallback_agents=[AGENT_RELIABILITY]),
    ),
    "high": _policy(
        "system-high",
        "high",
        ExecutionBudget(max_steps=10, max_tool_calls=25, max_wall_time_seconds=300),
        RetryPolicy(max_retries=3, backoff_seconds=1.0, exponential_backoff=True,
                    retryable_failures={"MODEL_TIMEOUT", "MODEL_RATE_LIMIT",
                                        "TOOL_TIMEOUT", "TOOL_UNAVAILABLE"}),
        VerificationPolicy(critic_required=True, evidence_required=True,
                           verifier_required=True, sandbox_required=True,
                           minimum_confidence=0.6),
        AgentPolicy(enabled_agents=[AGENT_SECURITY, AGENT_RELIABILITY, AGENT_SEMANTIC],
                    max_parallel_agents=3,
                    fallback_agents=[AGENT_RELIABILITY, AGENT_SEMANTIC]),
    ),
    "critical": _policy(
        "system-critical",
        "critical",
        ExecutionBudget(max_steps=15, max_tool_calls=40, max_wall_time_seconds=600),
        RetryPolicy(max_retries=3, backoff_seconds=1.0, exponential_backoff=True,
                    retryable_failures={"MODEL_TIMEOUT", "MODEL_RATE_LIMIT", "TOOL_TIMEOUT"}),
        VerificationPolicy(critic_required=True, evidence_required=True,
                           verifier_required=True, sandbox_required=True,
                           minimum_confidence=0.8),
        AgentPolicy(enabled_agents=[AGENT_SECURITY, AGENT_RELIABILITY, AGENT_SEMANTIC],
                    max_parallel_agents=3,
                    fallback_agents=[AGENT_RELIABILITY, AGENT_SEMANTIC]),
    ),
}


def default_policy(risk_level: str = "low") -> ExecutionPolicy:
    if risk_level not in DEFAULT_POLICIES:
        raise ValueError("no default policy for risk level: %s" % risk_level)
    return DEFAULT_POLICIES[risk_level]
