"""Record live tool calls into a replay snapshot and replay them deterministically."""
import threading
from typing import Any, Dict, List, Optional

from ..runtime import AgentLoopProtocolError, ToolRegistry
from .models import ReplayLevel, ReplayObservationIndex, ReplaySnapshot, fingerprint


class ReplayRecorder(ToolRegistry):
    """Wraps a real tool registry and records every observation into a snapshot.

    Used during a capture run to build the deterministic replay input.
    """

    def __init__(self, wrapped: ToolRegistry):
        super().__init__(wrapped._tools.values())
        self.wrapped = wrapped
        self._observations: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def invoke(self, name: str, arguments: Dict[str, Any]) -> Any:
        try:
            value = self.wrapped.invoke(name, arguments)
            self._record(name, arguments, ok=True, observation=value)
            return value
        except Exception as exc:
            self._record(name, arguments, ok=False, error=str(exc))
            raise

    def _record(self, name, arguments, ok, observation=None, error=""):
        with self._lock:
            self._observations.append({
                "tool": name,
                "arguments": dict(arguments or {}),
                "observation": observation,
                "ok": ok,
                "error": error,
                "fingerprint": fingerprint(name, arguments),
            })

    def observations(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._observations)

    def to_snapshot(self, **kwargs) -> ReplaySnapshot:
        return ReplaySnapshot(tool_observations=self.observations(), **kwargs)


class ReplayToolRegistry(ToolRegistry):
    """Deterministic replay adapter.

    In deterministic mode every tool request returns the recorded observation
    instead of re-invoking the real tool, so the same snapshot + candidate always
    produces identical observations.
    """

    def __init__(
        self,
        snapshot: ReplaySnapshot,
        live_registry: Optional[ToolRegistry] = None,
        read_only_tools: Optional[List[str]] = None,
        mode: str = "deterministic",
        replay_level: Optional[ReplayLevel] = None,
    ):
        super().__init__((live_registry._tools.values() if live_registry else []))
        self.snapshot = snapshot
        self.live_registry = live_registry
        self.read_only_tools = set(read_only_tools or ["read_file", "search_code", "list_files"])
        self.mode = mode
        self.replay_level = replay_level or ReplayLevel(
            snapshot.replay_level or ReplayLevel.L1_TOOL.value
        )
        # Ordered, occurrence-aware index (plan section 8.4): repeated tool+args
        # calls consume each observation exactly once, in original order.
        self._index = ReplayObservationIndex(snapshot.tool_observations)

    def invoke(self, name: str, arguments: Dict[str, Any]) -> Any:
        if self.mode == "deterministic":
            recorded = self._index.take(name, arguments)
            if recorded is not None:
                return recorded
            raise AgentLoopProtocolError(
                "no recorded observation for deterministic replay: %s"
                % fingerprint(name, arguments)
            )
        # Live mode re-invokes read-only tools only, via a governed live
        # registry so a side-effect tool is denied by policy (fail-closed).
        registry = self.live_registry
        if registry is None:
            raise AgentLoopProtocolError("no live registry for %s" % name)
        invoke_as = getattr(registry, "invoke_as", None)
        if invoke_as is not None:
            return invoke_as(
                "replay-agent", name, arguments, task_id=self.snapshot.task_id,
            )
        if name not in self.read_only_tools:
            raise AgentLoopProtocolError(
                "side-effect tool %s not allowed in live replay" % name
            )
        return registry.invoke(name, arguments)