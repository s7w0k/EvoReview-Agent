"""Runner and fixtures for deterministic / live replay."""
from typing import Any, Callable, Dict, List, Optional

from ..runtime import AgentLoopProtocolError, ToolRegistry
from .models import ReplayLevel, ReplaySnapshot
from .recorder import ReplayToolRegistry


class ReplayRunner:
    """Execute a stepper against a snapshot in deterministic or live mode."""

    def __init__(
        self,
        live_registry: Optional[ToolRegistry] = None,
        read_only_tools: Optional[List[str]] = None,
    ):
        self.live_registry = live_registry
        self.read_only_tools = read_only_tools or [
            "read_file", "search_code", "list_files", "find_callers",
        ]

    def run(
        self,
        snapshot: ReplaySnapshot,
        stepper: Callable[[Dict[str, Any]], Dict[str, Any]],
        mode: str = "deterministic",
        state: Optional[Dict[str, Any]] = None,
        replay_level: Optional[ReplayLevel] = None,
    ) -> Dict[str, Any]:
        tools = ReplayToolRegistry(
            snapshot, self.live_registry, self.read_only_tools, mode=mode,
            replay_level=replay_level,
        )
        runtime_state = dict(state or {})
        runtime_state["observations"] = []
        steps = 0
        for step in range(1, snapshot.context_snapshot.get("max_steps", 20) + 1):
            steps = step
            runtime_state["loop_step"] = step
            action = stepper(runtime_state)
            if not isinstance(action, dict):
                raise AgentLoopProtocolError("replay action must be an object")
            kind = str(action.get("action", "")).strip().lower()
            if kind == "final":
                return {
                    "output": action.get("findings", action.get("output")),
                    "steps": steps,
                    "observations": list(runtime_state.get("observations") or []),
                    "stop_reason": "final",
                }
            if kind != "tool":
                raise AgentLoopProtocolError("unsupported replay action: %s" % kind)
            tool_name = str(action.get("tool", "")).strip()
            arguments = action.get("arguments") or {}
            try:
                value = tools.invoke(tool_name, arguments)
                obs = {"step": step, "tool": tool_name, "ok": True, "result": value}
            except Exception as exc:
                obs = {"step": step, "tool": tool_name, "ok": False,
                       "error": str(exc)[:1000]}
            runtime_state["observations"].append(obs)
        return {
            "output": None, "steps": steps,
            "observations": list(runtime_state.get("observations") or []),
            "stop_reason": "budget",
        }

    def run_and_measure(
        self, snapshot: ReplaySnapshot, stepper: Callable[[Dict[str, Any]], Dict[str, Any]],
        metrics_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
        mode: str = "deterministic",
    ) -> Dict[str, Any]:
        result = self.run(snapshot, stepper, mode=mode)
        metrics = metrics_fn(result)
        metrics["agent_steps"] = int(result.get("steps", 0))
        metrics["tool_calls"] = len(result.get("observations", []))
        return metrics


def build_snapshot(diff: str, **kwargs) -> ReplaySnapshot:
    """Fixture helper: build a snapshot from a unified diff string."""
    import hashlib
    return ReplaySnapshot(diff_hash=hashlib.sha256(diff.encode("utf-8")).hexdigest(), **kwargs)