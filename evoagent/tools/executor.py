"""Real tool execution with enforced timeout.

Two execution modes:

* *Safe in-process* — read-only, fast handlers (``search_diff``, ``read_file``)
  run synchronously in the caller's process.
* *Potentially blocking* — tools that run user code or external commands
  (``run_tests``) run in a real subprocess so the timeout can terminate them.

The subprocess lifecycle on timeout is: ``start -> wait(timeout) -> terminate ->
grace(window) -> kill``.  Anything that survives past the grace window is
hard-killed so a hung tool can never outlive the policy timeout.
"""
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from ..policy.tool_policy import ToolMetadata
from ..runtime import AgentTool
from .sandbox import SandboxContext, SandboxEnforcer


class ToolTimeoutError(TimeoutError):
    """A blocking tool exceeded its configured timeout window."""


@dataclass
class ToolExecutionResult:
    value: Any = None
    timed_out: bool = False
    duration_seconds: float = 0.0
    exit_code: Optional[int] = None


class ToolExecutor:
    """Executes a tool under the right mode and returns a normalized result."""

    GRACE_SECONDS = 1.0

    def __init__(self, sandbox: Optional[SandboxContext] = None,
                 sandbox_enforcer: Optional[SandboxEnforcer] = None):
        self.sandbox = sandbox
        self.sandbox_enforcer = sandbox_enforcer or SandboxEnforcer()

    def execute(
        self,
        tool: AgentTool,
        arguments: Dict[str, Any],
        metadata: Optional[ToolMetadata],
        execution_context: Optional[Dict[str, Any]] = None,
    ) -> ToolExecutionResult:
        """Dispatch to the correct execution mode for ``metadata``."""
        is_blocking = metadata is not None and metadata.blocking
        if is_blocking:
            # metadata is guaranteed non-None for a blocking subprocess call.
            assert metadata is not None
            return self._run_subprocess(metadata, arguments)
        return self._run_in_process(tool, arguments)

    # -- safe, in-process ----------------------------------------------------

    def _run_in_process(self, tool: AgentTool, arguments: Dict[str, Any]) -> ToolExecutionResult:
        start = time.perf_counter()
        value = tool.handler(**arguments)
        return ToolExecutionResult(
            value=value, duration_seconds=round(time.perf_counter() - start, 4),
        )

    # -- potentially blocking, subprocess ------------------------------------

    def _run_subprocess(
        self, metadata: ToolMetadata, arguments: Dict[str, Any]
    ) -> ToolExecutionResult:
        if not metadata.command:
            raise ValueError(
                "blocking tool %s has no shell command template" % metadata.name
            )
        command = metadata.command
        try:
            command = command.format(**arguments)
        except (KeyError, IndexError, ValueError):
            command = "%s %s" % (
                metadata.command,
                " ".join(str(value) for value in arguments.values()),
            )
        env = self.sandbox_enforcer.build_env(self.sandbox)
        cwd = self.sandbox_enforcer.working_directory(self.sandbox)

        # start -> wait(timeout) -> terminate -> grace -> kill
        process = subprocess.Popen(
            command, shell=True, env=env, cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        start = time.perf_counter()
        try:
            stdout, stderr = process.communicate(timeout=metadata.timeout_seconds)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.communicate(timeout=self.GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                # On Windows/shell the grandchild may hold the pipes open and
                # outlive the immediate child.  Never block forever waiting for
                # it; bound the final wait and move on.
                try:
                    process.communicate(timeout=self.GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    pass
            raise ToolTimeoutError(
                "tool %s exceeded timeout of %.1fs" % (metadata.name, metadata.timeout_seconds)
            )
        duration = round(time.perf_counter() - start, 4)
        stdout_text = (stdout or b"").decode("utf-8", "replace")
        stderr_text = (stderr or b"").decode("utf-8", "replace")
        render = stdout_text
        if stderr_text:
            render += "\n[stderr]\n" + stderr_text
        return ToolExecutionResult(
            value=render, duration_seconds=duration, exit_code=process.returncode,
        )