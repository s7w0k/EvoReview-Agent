"""Factory: turn A2A endpoints into remote :class:`Reviewer` objects and route
them through the Agent Registry (Phase 3/4/5).

The publisher does not hard-code a Remote Agent Python object: discover its
:class:`AgentCard`, register it in the registry, and hand the coordinator a
:class:`RemoteReviewerAdapter` -- a plain :class:`Reviewer`.
"""
import os
from typing import Dict, List, Optional

from .adapters import RemoteReviewerAdapter, build_a2a_task
from .http_transport import HttpJsonRpcA2ATransport
from .inprocess_transport import InProcessA2ATransport
from .models import PROTOCOL_VERSION
from .registry import AgentRegistry

#: mapping of well-known agent id -> (name, capabilities, domains, task_type)
_KNOWN = {
    "security-agent": {
        "name": "Security Review Agent",
        "capabilities": ["code-review", "security-review"],
        "domains": ["security", "authorization"],
        "task_type": "review-assignment",
    },
    "reliability-agent": {
        "name": "Reliability Review Agent",
        "capabilities": ["code-review", "reliability-review"],
        "domains": ["reliability", "correctness", "regression"],
        "task_type": "review-assignment",
    },
    "critic-agent": {
        "name": "Critic Review Agent",
        "capabilities": ["finding-critique", "conflict-detection", "review-reflection"],
        "domains": ["quality"],
        "task_type": "critique.findings",
    },
    "verifier-agent": {
        "name": "Verifier Review Agent",
        "capabilities": ["finding-verification", "evidence-validation"],
        "domains": ["verification"],
        "task_type": "verify.findings",
    },
    "fix-agent": {
        "name": "Fix Agent",
        "capabilities": ["patch-generation", "repair-verification", "safe-fix"],
        "domains": ["remediation"],
        "task_type": "fix.generate",
    },
}


def known_agent_meta(agent_id: str) -> Dict[str, str]:
    """Return well-known routing metadata, or sensible defaults."""
    return dict(_KNOWN.get(agent_id, {
        "name": agent_id.replace("-", " ").title(),
        "capabilities": ["code-review"],
        "domains": ["security", "reliability"],
        "task_type": "review-assignment",
    }))


def build_agent_card(
    agent_id: str, endpoint: str, *, deployment: str = "http",
    capabilities: Optional[List[str]] = None, domains: Optional[List[str]] = None,
    supported_task_types: Optional[List[str]] = None, version: str = "1.0.0",
) -> dict:
    meta = known_agent_meta(agent_id)
    return {
        "agent_id": agent_id,
        "name": meta["name"],
        "version": version,
        "endpoint": endpoint,
        "protocol_version": PROTOCOL_VERSION,
        "capabilities": list(capabilities or meta["capabilities"]),
        "domains": list(domains or meta["domains"]),
        "supported_task_types": list(
            supported_task_types or [meta["task_type"]]),
        "health_status": "healthy",
        "deployment": deployment,
    }


def build_http_transport(token: str = "", timeout_seconds: float = 10.0) -> HttpJsonRpcA2ATransport:
    return HttpJsonRpcA2ATransport(token=token, timeout_seconds=timeout_seconds)


def build_remote_reviewers(
    endpoints: List[str], *, token: str = "", timeout_seconds: float = 10.0,
    registry: Optional[AgentRegistry] = None, discover: bool = True,
    local_fallback=None,
) -> list:
    """Discover + register each HTTP endpoint and build a remote Reviewer.

    Returns ``(reviewers, registry)``.  When ``discover=False`` the endpoint is
    used verbatim without a round-trip (useful when the agent is still starting).
    """
    registry = registry or AgentRegistry()
    transport = build_http_transport(token, timeout_seconds)
    reviewers = []
    for endpoint in endpoints:
        meta = _card_from_endpoint(endpoint, token, timeout_seconds, discover)
        registry.register(meta)
        reviewers.append(RemoteReviewerAdapter(
            meta, transport, local_fallback=local_fallback,
            task_type="review-assignment",
            timeout_seconds=timeout_seconds,
        ))
    return reviewers, registry


def build_remote_reviewers_typed(
    endpoints: List[str], *, token: str = "", timeout_seconds: float = 10.0,
    registry: Optional[AgentRegistry] = None, discover: bool = True,
    local_fallbacks: Optional[dict] = None,
) -> list:
    """Build remote reviewers with a per-agent local fallback.

    Unlike :func:`build_remote_reviewers` (which accepts a single
    ``local_fallback``), this accepts ``local_fallbacks`` keyed by ``agent_id``
    so each remote specialist can fall back to its own domain reviewer.  The
    adapter's ``name`` is pinned to its ``agent_id`` (``security-agent`` /
    ``reliability-agent``) so the coordinator route and registry health lookup
    match even when the endpoint advertises a friendly display name.

    Returns ``(reviewers, registry)``.
    """
    from .adapters import RemoteReviewerAdapter  # local import avoids cycle

    registry = registry or AgentRegistry()
    transport = build_http_transport(token, timeout_seconds)
    fallbacks = local_fallbacks or {}
    reviewers = []
    for endpoint in endpoints:
        meta = _card_from_endpoint(endpoint, token, timeout_seconds, discover)
        agent_id = str(meta.get("agent_id") or meta.get("name", "remote-agent"))
        meta["name"] = agent_id  # coordinator route + registry health by agent_id
        registry.register(meta)
        reviewers.append(RemoteReviewerAdapter(
            meta, transport, local_fallback=fallbacks.get(agent_id),
            task_type="review-assignment", timeout_seconds=timeout_seconds,
        ))
    return reviewers, registry


def build_inprocess_reviewers(
    hosts: list, *, registry: Optional[AgentRegistry] = None, local_fallback=None,
) -> list:
    """Build remote reviewers over in-process transports (no network)."""
    registry = registry or AgentRegistry()
    reviewers = []
    for host in hosts:
        meta = host.card.to_dict()
        meta["deployment"] = "local"
        registry.register(meta)
        reviewers.append(RemoteReviewerAdapter(
            meta, InProcessA2ATransport(host), local_fallback=local_fallback,
            task_type="review-assignment",
        ))
    return reviewers, registry


def _card_from_endpoint(endpoint: str, token: str, timeout_seconds: float,
                        discover: bool) -> dict:
    if not discover:
        agent_id = endpoint.rsplit("/", 1)[-1].split(":")[-1] or "remote-agent"
        return build_agent_card(agent_id, endpoint)
    transport = build_http_transport(token, timeout_seconds)
    card = transport.discover(endpoint)
    card["endpoint"] = endpoint
    card["deployment"] = "http"
    return card


def a2a_endpoints_from_env() -> List[str]:
    """Read ``EVOAGENT_A2A_ENDPOINTS`` (comma-separated) from the environment."""
    value = os.getenv("EVOAGENT_A2A_ENDPOINTS", "")
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


__all__ = [
    "build_agent_card", "build_http_transport", "build_remote_reviewers",
    "build_remote_reviewers_typed", "build_inprocess_reviewers",
    "a2a_endpoints_from_env", "known_agent_meta",
    "build_a2a_task",
]