"""Reliability Agent -- standalone A2A service.

Wraps the deterministic ``ReliabilityRuleReviewer`` and exposes it over FastAPI:
``GET /health``, ``GET /a2a/agent-card``, ``POST /a2a`` (JSON-RPC 2.0).
"""
import os

from evoagent.a2a.governance import ArtifactSanitizer, verify_token
from evoagent.a2a.models import PROTOCOL_VERSION, AgentCard
from evoagent.a2a.service import AgentServiceHost, TaskStore
from evoagent.reviewer import ReliabilityRuleReviewer

AGENT_ID = "reliability-agent"


def build_host() -> AgentServiceHost:
    review = ReliabilityRuleReviewer()
    card = {
        "agent_id": AGENT_ID,
        "name": "Reliability Review Agent",
        "version": os.getenv("EVOAGENT_A2A_VERSION", "1.0.0"),
        "endpoint": os.getenv("EVOAGENT_A2A_ENDPOINT", f"http://{AGENT_ID}:8002/a2a"),
        "capabilities": ["code-review", "reliability-review"],
        "domains": ["reliability", "correctness", "regression"],
        "supported_task_types": ["review-assignment"],
        "protocol_version": PROTOCOL_VERSION,
        "health_status": "healthy",
        "deployment": "http",
    }
    return AgentServiceHost(
        review,
        AgentCard.from_dict(card),
        token=os.getenv("EVOAGENT_A2A_TOKEN", ""),
        sanitizer=ArtifactSanitizer(),
        store=TaskStore(),
        delay_seconds=float(os.getenv("EVOAGENT_A2A_DELAY_SECONDS", "0") or 0),
    )


def require_token(presented: str) -> bool:
    return verify_token(os.getenv("EVOAGENT_A2A_TOKEN", ""), presented)