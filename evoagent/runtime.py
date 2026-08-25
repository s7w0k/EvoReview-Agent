"""EvoAgent's dependency-free durable runtime and bounded agent loop.

The runtime deliberately separates orchestration from agent behaviour:

* ``AgentRuntime`` executes named nodes with budgets, retry policy, cancellation
  checks and application-owned checkpoints.
* ``AgentLoop`` executes model-selected tool actions until the agent returns a
  final result or its step/time budget is exhausted.

Both components are deterministic around side effects.  Persistence remains in
the application store so a worker restart does not depend on a framework-owned
checkpoint format.
"""
from contextlib import nullcontext
from dataclasses import dataclass, field
import json
import time
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple, Type

# Policy wiring is optional so the module keeps its current callers working.
try:
    from .policy.models import ExecutionPolicy as _ExecutionPolicy
except Exception:  # pragma: no cover - only during partial installs
    _ExecutionPolicy = None  # type: ignore[assignment,misc]


class RuntimeBudgetExceeded(RuntimeError):
    """The configured step or wall-clock budget was exhausted."""


class RuntimeCancelled(RuntimeError):
    """The owning task requested cancellation."""


class AgentLoopProtocolError(RuntimeError):
    """An agent returned an invalid loop action."""


class AgentLoopNoProgress(RuntimeError):
    """The agent repeated the same action without making progress."""


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    parameters: Dict[str, Any]
    handler: Callable[..., Any]

    def catalog_entry(self) -> Dict[str, Any]:
        return {
            "name": self.name, "description": self.description,
            "parameters": self.parameters,
        }


class ToolRegistry:
    """Explicit tool catalog with JSON-schema-like argument validation."""

    def __init__(self, tools: Iterable[AgentTool] = ()):
        self._tools: Dict[str, AgentTool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: AgentTool) -> None:
        if not tool.name or tool.name in self._tools:
            raise ValueError("tool names must be non-empty and unique")
        self._tools[tool.name] = tool

    def names(self) -> List[str]:
        return sorted(self._tools)

    def catalog(self) -> List[Dict[str, Any]]:
        return [self._tools[name].catalog_entry() for name in self.names()]

    def invoke(self, name: str, arguments: Dict[str, Any]) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            raise AgentLoopProtocolError("unknown agent tool: %s" % name)
        self._validate(tool.parameters, arguments)
        return tool.handler(**arguments)

    @staticmethod
    def _validate(schema: Dict[str, Any], arguments: Dict[str, Any]) -> None:
        if not isinstance(arguments, dict):
            raise AgentLoopProtocolError("tool arguments must be an object")
        properties = dict(schema.get("properties") or {})
        required = set(schema.get("required") or [])
        missing = required.difference(arguments)
        if missing:
            raise AgentLoopProtocolError(
                "missing required tool arguments: %s" % ", ".join(sorted(missing))
            )
        if schema.get("additionalProperties", False) is False:
            unknown = set(arguments).difference(properties)
            if unknown:
                raise AgentLoopProtocolError(
                    "unknown tool arguments: %s" % ", ".join(sorted(unknown))
                )
        expected_types: Dict[str, Any] = {
            "string": str, "integer": int, "number": (int, float),
            "boolean": bool, "object": dict, "array": list,
        }
        for key, value in arguments.items():
            spec = properties.get(key) or {}
            expected = expected_types.get(str(spec.get("type", "")))
            if expected and (not isinstance(value, expected) or (
                spec.get("type") in {"integer", "number"} and isinstance(value, bool)
            )):
                raise AgentLoopProtocolError(
                    "tool argument %s must be %s" % (key, spec.get("type"))
                )
            if isinstance(value, (int, float)):
                if "minimum" in spec and value < spec["minimum"]:
                    raise AgentLoopProtocolError("tool argument %s is below minimum" % key)
                if "maximum" in spec and value > spec["maximum"]:
                    raise AgentLoopProtocolError("tool argument %s exceeds maximum" % key)


@dataclass(frozen=True)
class RuntimeNode:
    name: str
    handler: Callable[[Dict[str, Any]], Dict[str, Any]]
    retries: Optional[int] = None
    checkpoint: bool = True


@dataclass(frozen=True)
class RuntimeEvent:
    kind: str
    node: str
    step: int
    attempt: int = 0
    detail: Dict[str, Any] = field(default_factory=dict)


class AgentRuntime:
    """Execute a bounded node graph without a third-party orchestration engine."""

    def __init__(
        self, max_steps: int = 8, timeout_seconds: int = 120,
        node_retries: int = 0, execution_policy: Optional["_ExecutionPolicy"] = None,
        recovery_manager=None,
    ):
        if execution_policy is not None:
            max_steps = execution_policy.budget.max_steps
            timeout_seconds = int(execution_policy.budget.max_wall_time_seconds)
            node_retries = execution_policy.retry.max_retries
        if max_steps < 1:
            raise ValueError("runtime max_steps must be at least 1")
        if timeout_seconds < 1:
            raise ValueError("runtime timeout_seconds must be at least 1")
        if node_retries < 0:
            raise ValueError("runtime node_retries cannot be negative")
        self.max_steps = max_steps
        self.timeout_seconds = timeout_seconds
        self.node_retries = node_retries
        self.execution_policy = execution_policy
        self.recovery_manager = recovery_manager

    def execute(
        self, initial_state: Dict[str, Any], nodes: Iterable[RuntimeNode],
        task_id: str = "", checkpoint_store=None,
        cancel_check: Optional[Callable[[], bool]] = None,
        event_sink: Optional[Callable[[RuntimeEvent], None]] = None,
        span_factory: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
        non_retryable: Tuple[Type[BaseException], ...] = (
            ValueError, RuntimeCancelled, RuntimeBudgetExceeded),
    ) -> Dict[str, Any]:
        state = dict(initial_state)
        started = time.monotonic()
        steps = 0
        checkpoints = (
            checkpoint_store.load_checkpoints(task_id)
            if checkpoint_store is not None and task_id else {}
        )

        def emit(kind: str, node: str, attempt: int = 0, **detail) -> None:
            if event_sink:
                event_sink(RuntimeEvent(kind, node, steps, attempt, detail))

        def guard(node: str) -> None:
            if cancel_check and cancel_check():
                emit("cancelled", node)
                raise RuntimeCancelled("Task was cancelled")
            if steps >= self.max_steps or time.monotonic() - started > self.timeout_seconds:
                emit("budget_exhausted", node)
                raise RuntimeBudgetExceeded("task execution budget exceeded")

        for node in nodes:
            cached = checkpoints.get(node.name) if node.checkpoint else None
            if cached and cached.get("status") == "completed":
                output = dict(cached.get("state") or {})
                state.update(output)
                emit("checkpoint_restored", node.name, int(cached.get("attempt", 0)))
                continue

            retries = self.node_retries if node.retries is None else node.retries
            previous_attempt = int((cached or {}).get("attempt", 0))
            last_error: Optional[Exception] = None
            for offset in range(1, retries + 2):
                guard(node.name)
                steps += 1
                attempt = previous_attempt + offset
                emit("node_started", node.name, attempt)
                try:
                    attrs = {
                        "task_id": task_id, "node": node.name,
                        "attempt": attempt, "runtime_step": steps,
                    }
                    context = (
                        span_factory("runtime.%s" % node.name, attrs)
                        if span_factory else nullcontext()
                    )
                    with context:
                        output = node.handler(state) or {}
                    if not isinstance(output, dict):
                        raise TypeError("runtime node %s must return a dict" % node.name)
                    state.update(output)
                    if checkpoint_store is not None and task_id and node.checkpoint:
                        checkpoint_store.save_checkpoint(
                            task_id, node.name, output, "completed", attempt
                        )
                    emit("node_completed", node.name, attempt, output_keys=sorted(output))
                    last_error = None
                    break
                except non_retryable:
                    raise
                except Exception as exc:
                    last_error = exc
                    if checkpoint_store is not None and task_id and node.checkpoint:
                        checkpoint_store.save_checkpoint(
                            task_id, node.name, {}, "failed", attempt, str(exc)
                        )
                    emit(
                        "node_failed", node.name, attempt,
                        error=str(exc)[:1000], will_retry=offset <= retries,
                    )
                    # Recovery (plan section 7.2): classify + plan + execute the
                    # failure instead of an unconditional naive retry.
                    if self.recovery_manager is not None:
                        outcome = self.recovery_manager.handle(
                            exc,
                            {"node": node.name, "attempt": attempt},
                            state,
                            node.name,
                            agent_id="runtime",
                            tool_context={"node_name": node.name},
                        )
                        state.update(outcome.updates or {})
                        if not outcome.recoverable or outcome.should_abort:
                            break
                        # RETRY / RETRY_WITH_BACKOFF / SWITCH_MODEL / COMPRESS ...
                        continue
            if last_error is not None:
                raise last_error
        return state


@dataclass
class AgentLoopResult:
    output: Any
    steps: int
    observations: List[Dict[str, Any]]
    stop_reason: str


class AgentLoop:
    """Run a model/tool loop with strict action, time and observation budgets."""

    def __init__(
        self, max_steps: int = 4, timeout_seconds: int = 45,
        max_observation_chars: int = 4000,
        execution_policy: Optional["_ExecutionPolicy"] = None,
        no_progress_detector=None,
    ):
        self.max_tool_calls: Optional[int] = None
        if execution_policy is not None:
            max_steps = execution_policy.budget.max_steps
            timeout_seconds = int(execution_policy.budget.max_wall_time_seconds)
            self.max_tool_calls = execution_policy.budget.max_tool_calls
        else:
            self.max_tool_calls = None
        if max_steps < 1:
            raise ValueError("agent loop max_steps must be at least 1")
        if timeout_seconds < 1:
            raise ValueError("agent loop timeout_seconds must be at least 1")
        self.max_steps = max_steps
        self.timeout_seconds = timeout_seconds
        self.max_observation_chars = max(256, max_observation_chars)
        if no_progress_detector is None:
            from .recovery.no_progress import NoProgressDetector
            no_progress_detector = NoProgressDetector()
        self.no_progress_detector = no_progress_detector

    def run(
        self, stepper: Callable[[Dict[str, Any]], Dict[str, Any]],
        tools: Any, initial_state: Dict[str, Any],
        event_sink: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        agent_id: str = "", task_id: str = "",
    ) -> AgentLoopResult:
        state = dict(initial_state)
        observations = list(state.get("observations") or [])
        started = time.monotonic()
        actions: List[Dict[str, Any]] = []

        def emit(kind: str, **detail) -> None:
            if event_sink:
                event_sink(kind, detail)

        from .recovery.classifier import FailureClassifier
        from .recovery.failures import RETRYABLE_FOR_BACKOFF
        _classifier = FailureClassifier()

        for step in range(1, self.max_steps + 1):
            if time.monotonic() - started > self.timeout_seconds:
                emit("agent_loop_budget_exhausted", step=step, budget="time")
                raise RuntimeBudgetExceeded("agent loop time budget exceeded")
            state["loop_step"] = step
            state["observations"] = list(observations)
            action = stepper(state)
            if not isinstance(action, dict):
                raise AgentLoopProtocolError("agent loop action must be an object")
            kind = str(action.get("action", "")).strip().lower()
            emit("agent_loop_action", step=step, action=kind)
            if kind == "final":
                return AgentLoopResult(
                    action.get("findings", action.get("output")), step,
                    observations, "final",
                )
            if kind != "tool":
                raise AgentLoopProtocolError("unsupported agent loop action: %s" % kind)
            if self.max_tool_calls is not None and len(observations) >= self.max_tool_calls:
                emit("agent_loop_budget_exhausted", step=step, budget="tool_calls")
                raise RuntimeBudgetExceeded("agent loop tool-call budget exceeded")
            tool_name = str(action.get("tool", "")).strip()
            arguments = action.get("arguments") or {}
            if not isinstance(arguments, dict):
                raise AgentLoopProtocolError("tool arguments must be an object")
            # No-progress detection (plan section 7.5): the same tool+args
            # repeated long enough is AGENT_NO_PROGRESS, not a recoverable loop.
            actions.append(
                {"action": "tool", "tool": tool_name, "arguments": arguments}
            )
            if self.no_progress_detector.detect(actions):
                emit("agent_loop_no_progress", step=step, tool=tool_name)
                raise AgentLoopNoProgress(
                    "agent repeated the same tool without making progress: %s" % tool_name
                )
            try:
                invoke_as = getattr(tools, "invoke_as", None)
                if invoke_as is not None and agent_id:
                    value = invoke_as(agent_id, tool_name, arguments, task_id=task_id)
                elif isinstance(tools, ToolRegistry):
                    value = tools.invoke(tool_name, arguments)
                else:
                    tool = tools.get(tool_name)
                    if tool is None:
                        raise AgentLoopProtocolError("unknown agent tool: %s" % tool_name)
                    value = tool(**arguments)
                rendered = (
                    json.dumps(value, ensure_ascii=False, sort_keys=True)
                    if isinstance(value, (dict, list, tuple)) else str(value)
                )
                observation = {
                    "step": step, "tool": tool_name, "ok": True,
                    "result": rendered[:self.max_observation_chars],
                }
            except AgentLoopNoProgress:
                raise
            except Exception as exc:
                # Tool failure classification (plan section 7.4): distinguish
                # recoverable runtime failures from normal observation failures.
                failure_type = _classifier.classify(exc)
                observation = {
                    "step": step, "tool": tool_name, "ok": False,
                    "error": str(exc)[:1000],
                    "failure_type": failure_type.value,
                    "recoverable": failure_type in RETRYABLE_FOR_BACKOFF,
                }
            observations.append(observation)
            emit("agent_loop_observation", **observation)
        emit("agent_loop_budget_exhausted", step=self.max_steps, budget="steps")
        raise RuntimeBudgetExceeded("agent loop step budget exceeded")
