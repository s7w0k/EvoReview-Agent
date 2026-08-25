"""Sandbox enforcement for tool execution.

A blocking tool (e.g. ``run_tests``) executes in an isolated subprocess whose
environment is built from a :class:`SandboxContext`.  The sandbox whitelists
environment variables, disables network egress when ``network_enabled`` is off,
and runs inside a restricted working directory so a runaway tool cannot touch
the host lightly.
"""
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True)
class SandboxContext:
    """Identify which sandbox a tool call ran under."""

    task_id: str
    repository: str = ""
    commit_sha: str = ""
    workspace: str = ""
    env_allowlist: List[str] = field(default_factory=list)
    network_enabled: bool = False


class SandboxNotConfigured(Exception):
    """A sandboxed tool ran with no sandbox context supplied."""


class SandboxEnforcer:
    """Build a restricted environment + command prefix for a sandbox."""

    def __init__(self, allowlist: Optional[List[str]] = None):
        self.default_allowlist = allowlist or [
            "PATH", "HOME", "TMPDIR", "TEMP", "TMP", "USER", "HOSTNAME", "LANG",
        ]

    def build_env(
        self, context: Optional[SandboxContext], extra: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """Return an environment that never carries secrets and, unless enabled,
        blocks network egress."""
        allow = list(self.default_allowlist)
        if context is not None:
            allow.extend(context.env_allowlist)
        env = {key: value for key, value in os.environ.items() if key in allow}
        env.update(extra or {})
        # A write-only proxy blocks egress: subprocesses inherit these and any
        # outbound connect fails unless network is explicitly enabled.
        if context is not None and not context.network_enabled:
            env["NO_PROXY"] = env.get("NO_PROXY", "")
            env["ALL_PROXY"] = "http://127.0.0.1:9"  # unroutable loopback
            env["HTTP_PROXY"] = "http://127.0.0.1:9"
            env["HTTPS_PROXY"] = "http://127.0.0.1:9"
        return env

    def working_directory(
        self, context: Optional[SandboxContext],
    ) -> Optional[str]:
        if context is None:
            return None
        wd = context.workspace or None
        if wd and not os.path.isdir(wd):
            # A sandboxed working dir that does not exist yet is created lazily.
            os.makedirs(wd, exist_ok=True)
        return wd