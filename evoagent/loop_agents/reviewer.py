"""Six-core-agent reviewer + factory (plan §20, §11).

A :class:`Reviewer` adapter that runs the global Coordinator loop over the five
specialist loop agents and returns the arbiter-accepted :class:`Finding`s.

Two delegation modes (plan §19):
  * ``inprocess`` -- each specialist is served by a :class:`LoopAgentHost`
    bridged to the Coordinator through :class:`InProcessA2ATransport`;
  * ``http``      -- each specialist is served by an :class:`AgentServer` and
    reached through ``HttpJsonRpcA2ATransport``.

Building a fresh ``Delegator`` per ``review()`` call keeps a coordinator run
free of cross-run artifact leakage while the same specialist agent objects are
reused across calls.
"""
import time
from typing import Any, Dict, List, Optional

from ..reviewer import Reviewer
from ..diff_parser import ParsedDiff
from ..models import Finding, Severity

from .base import BaseLoopAgent
from .coordinator import CoordinatorAgent
from .critic import CriticAgent
from .delegator import Delegator
from .fix import FixAgent
from .reliability import ReliabilityAgent
from .security import SecurityAgent
from .service_host import LoopAgentHost
from .verifier import VerifierAgent
from .feature_flags import MultiAgentFeatureFlags

#: (agent_id, instance) builders kept in plan order for the graph.
_SPECIALISTS = (
    SecurityAgent,
    ReliabilityAgent,
    CriticAgent,
    VerifierAgent,
    FixAgent,
)


def _severity(value: Any) -> Severity:
    try:
        return Severity(str(value or "medium"))
    except Exception:
        return Severity.MEDIUM


class SixAgentReviewer(Reviewer):
    name = "six-agent"

    def __init__(
        self,
        mode: str = "inprocess",
        *,
        specialists: Optional[List[BaseLoopAgent]] = None,
        coordinator_kwargs: Optional[Dict[str, Any]] = None,
        architecture: str = "six-agent",
        http_timeout_seconds: float = 10.0,
        http_token: str = "",
        feature_flags: Optional[MultiAgentFeatureFlags] = None,
        tool_context_config: Optional[Dict[str, Any]] = None,
    ):
        self.mode = (mode or "inprocess").strip().lower()
        self.architecture = architecture
        self.flags = feature_flags or MultiAgentFeatureFlags()
        self.tool_context_config = dict(tool_context_config or {})
        self.last_runtime_artifact: Dict[str, Any] = {}
        # build specialists honouring the runtime's loop-depth switch so a
        # ``deep_loop=False`` reviewer genuinely runs a shallow stepper (§4.4)
        self.specialists = list(specialists) if specialists else [
            ctor(deep_loop=self.flags.deep_loop) for ctor in _SPECIALISTS
        ]
        if len(self.specialists) < 5:
            raise ValueError("six-agent reviewer requires all five specialists")
        self.coordinator_kwargs = dict(coordinator_kwargs or {})
        if architecture not in ("six-agent", "six-agent-v1", "six-agent-v2"):
            architecture = "six-agent"
        if architecture == "six-agent-v2":
            self.coordinator_kwargs.setdefault("mode", "v2")
        self.coordinator_kwargs.setdefault(
            "feature_flags", self.flags)
        self.http_timeout_seconds = http_timeout_seconds
        self.http_token = http_token
        self._servers: List[Any] = []

    # -- delegation mode helpers -------------------------------------------
    def _inprocess_delegator(self, diff: str) -> Delegator:
        delegator = Delegator()
        delegator.diff = diff
        for agent in self.specialists:
            host = LoopAgentHost(agent)
            from ..a2a.inprocess_transport import InProcessA2ATransport
            delegator.add_agent(
                agent.agent_id, host.card.to_dict(),
                InProcessA2ATransport(host), _task_type(agent),
            )
        return delegator

    def _http_delegator(self, diff: str) -> Delegator:
        from ..a2a.factory import build_http_transport
        from ..a2a.server import AgentServer

        delegator = Delegator()
        delegator.diff = diff
        # Start (or reuse) one threaded server per specialist.
        servers = self._servers or [None] * len(self.specialists)
        for index, agent in enumerate(self.specialists):
            if servers[index] is None:
                servers[index] = AgentServer(LoopAgentHost(agent)).start()
            server = servers[index]
            card = server.card().to_dict()
            card["endpoint"] = server.endpoint  # point the card at its server
            delegator.add_agent(
                agent.agent_id, card,
                build_http_transport(self.http_token, self.http_timeout_seconds),
                _task_type(agent),
            )
        self._servers = servers
        return delegator

    # -- Reviewer -----------------------------------------------------------
    def review(self, diff: str, parsed: ParsedDiff) -> List[Finding]:
        self._ensure_running()
        runtime_tool_context = dict(self.tool_context_config)
        runtime_tool_context["skill_invocations"] = {}
        for agent in self.specialists:
            agent.tool_context_config = runtime_tool_context
        delegator = (self._http_delegator if self.mode == "http"
                     else self._inprocess_delegator)(diff)
        coordinator = CoordinatorAgent(delegator, **self.coordinator_kwargs)
        task: Dict[str, Any] = {
            "task_id": self._run_id(),
            "task_type": "review.coordinate",
            "objective": "coordinate a multi-agent code review",
            "input": {"diff": diff, "objective": "coordinate a multi-agent code review"},
        }
        outcome = coordinator.run(task)
        artifact = outcome.get("artifact") or {}
        artifact["architecture"] = self.architecture
        artifact["skill_invocations"] = dict(
            runtime_tool_context.get("skill_invocations") or {})
        self.last_runtime_artifact = dict(artifact)
        accepted = list(artifact.get("accepted_findings") or [])
        return [self._to_finding(item) for item in accepted]

    def runtime_summary(self) -> Dict[str, Any]:
        return dict(self.last_runtime_artifact or {})

    # -- helpers ------------------------------------------------------------
    def _ensure_running(self) -> None:
        if self.mode != "http":
            return
        for server in self._servers:
            if server is None:
                return
        if len([s for s in self._servers if s is not None]) == len(self.specialists):
            return
        # (Re)start lazily inside _http_delegator; nothing to do here.

    def close(self) -> None:
        for server in self._servers:
            if server is not None:
                server.stop()
        self._servers = []

    @staticmethod
    def _run_id() -> str:
        return "%032x" % int(time.time() * 1000000)

    @staticmethod
    def _to_finding(item: Dict[str, Any]) -> Finding:
        return Finding(
            rule_id=str(item.get("rule_id") or "RULE"),
            severity=_severity(item.get("severity")),
            title=str(item.get("title") or item.get("rule_id") or "finding"),
            explanation=str(item.get("explanation") or ""),
            path=str(item.get("path") or ""),
            line=int(item.get("line") or 0),
            evidence=str(item.get("evidence") or ""),
            fix=str(item.get("fix") or ""),
            test=str(item.get("test") or ""),
            confidence=float(item.get("confidence", 0.8)),
            source_skill=item.get("source_skill"),
            analyzer=item.get("analyzer"),
        )


def _task_type(agent: BaseLoopAgent) -> str:
    return getattr(agent, "task_type", "review.reliability")


def build_six_agent_reviewer(
    mode: str = "inprocess", *,
    specialists: Optional[List[BaseLoopAgent]] = None,
    coordinator_kwargs: Optional[Dict[str, Any]] = None,
    architecture: str = "six-agent",
    http_timeout_seconds: float = 10.0,
    http_token: str = "",
    tool_context_config: Optional[Dict[str, Any]] = None,
) -> Reviewer:
    """Build a :class:`SixAgentReviewer` (plan §20)."""
    return SixAgentReviewer(
        mode, specialists=specialists,
        coordinator_kwargs=coordinator_kwargs,
        architecture=architecture,
        http_timeout_seconds=http_timeout_seconds,
        http_token=http_token,
        tool_context_config=tool_context_config,
    )


__all__ = ["SixAgentReviewer", "build_six_agent_reviewer"]
