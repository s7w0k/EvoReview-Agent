"""Agent Registry + capability discovery (Phase 3).

Replaces the hard-coded ``_enabled_agents`` name-based routing with a registry
that registers from :class:`AgentCard`, matches by capability / domain / health /
version, and lets the Planner express ``required_domains`` /
``required_capabilities``.
"""
import threading
import time
from typing import Dict, List, Optional


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class AgentRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._cards: Dict[str, dict] = {}
        self._last_seen: Dict[str, float] = {}
        self._deployment: Dict[str, str] = {}

    def register(self, card: dict) -> dict:
        """Register (or refresh) an agent from its ``AgentCard`` dict."""
        agent_id = card.get("agent_id")
        if not agent_id:
            raise ValueError("AgentCard.agent_id is required to register")
        copy = dict(card)
        copy["last_seen"] = _now()
        with self._lock:
            self._cards[agent_id] = copy
            self._last_seen[agent_id] = time.monotonic()
            self._deployment[agent_id] = copy.get("deployment", "local")
        return copy

    def register_many(self, cards: List[dict]) -> List[dict]:
        return [self.register(card) for card in cards]

    def unregister(self, agent_id: str) -> None:
        with self._lock:
            self._cards.pop(agent_id, None)
            self._last_seen.pop(agent_id, None)
            self._deployment.pop(agent_id, None)

    def get(self, agent_id: str) -> Optional[dict]:
        with self._lock:
            return dict(self._cards[agent_id]) if agent_id in self._cards else None

    def agents(self) -> List[dict]:
        with self._lock:
            return [dict(item) for item in self._cards.values()]

    def healthy(self) -> List[dict]:
        with self._lock:
            return [
                dict(item) for item in self._cards.values()
                if str(item.get("health_status", "healthy")).lower() != "unhealthy"
            ]

    def mark_unhealthy(self, agent_id: str) -> None:
        with self._lock:
            if agent_id in self._cards:
                self._cards[agent_id]["health_status"] = "unhealthy"

    def mark_healthy(self, agent_id: str) -> None:
        with self._lock:
            if agent_id in self._cards:
                self._cards[agent_id]["health_status"] = "healthy"

    def match(
        self, *, required_domains: Optional[List[str]] = None,
        required_capabilities: Optional[List[str]] = None,
        required_task_type: Optional[str] = None,
        min_version: Optional[str] = None,
    ) -> List[dict]:
        """Select healthy agents satisfying the Planner's routing request."""
        candidates = self.healthy()
        if required_domains:
            domains = set(required_domains)
            candidates = [
                c for c in candidates if domains.intersection(set(c.get("domains", [])))
            ]
        if required_capabilities:
            caps = set(required_capabilities)
            candidates = [
                c for c in candidates if caps.intersection(set(c.get("capabilities", [])))
            ]
        if required_task_type:
            candidates = [
                c for c in candidates
                if required_task_type in set(c.get("supported_task_types", []))
            ]
        if min_version:
            candidates = [
                c for c in candidates
                if self._version_gte(c.get("version", ""), min_version)
            ]
        return candidates

    @staticmethod
    def _version_gte(candidate: str, required: str) -> bool:
        def parts(value: str):
            out = []
            for piece in value.split("."):
                if piece.isdigit():
                    out.append(int(piece))
                else:
                    break
            return out or [0]
        return parts(candidate) >= parts(required)


__all__ = ["AgentRegistry"]