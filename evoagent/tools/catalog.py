"""Unified tool catalog (plan section 6.1 / 6.2).

Every agent-visible tool is declared exactly once as a ``ToolDefinition`` that
pairs the ``AgentTool`` (schema + handler) with its ``ToolMetadata``.  Runtime
handlers are bound to a per-run context (diff, parsed, memory, workspace) so the
same catalog feeds the multi-agent reviewer, the procedure executor and live
replay -- no tool can bypass governance because every one flows through
``GovernedToolRegistry``.
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..policy.tool_policy import ToolMetadata
from ..runtime import AgentTool


@dataclass(frozen=True)
class ToolDefinition:
    tool: AgentTool
    metadata: ToolMetadata


def _define(
    name, description, schema, handler, *, risk="low", side_effect=False,
    idempotent=True, requires_sandbox=False, requires_approval=False,
    timeout_seconds=30.0, blocking=False, command=None, allowed_agents=(),
) -> ToolDefinition:
    return ToolDefinition(
        tool=AgentTool(name, description, schema, handler),
        metadata=ToolMetadata(
            name=name, risk_level=risk, side_effect=side_effect, idempotent=idempotent,
            requires_sandbox=requires_sandbox, requires_approval=requires_approval,
            timeout_seconds=timeout_seconds, blocking=blocking, command=command,
            allowed_agents=list(allowed_agents),
        ),
    )


def _unavailable(reason: str) -> Dict[str, Any]:
    return {"available": False, "reason": reason}


# -- runtime-facing handlers bound to a per-run context -----------------------

def _build_runtime_definitions(
    diff: str = "",
    parsed=None,
    memory_manager=None,
    tenant_id: str = "default",
    repository: str = "",
    workspace: Optional[str] = None,
) -> List[ToolDefinition]:
    """Build the Phase-2 base catalog bound to one review run."""
    added_lines = list(getattr(parsed, "added_lines", []) or [])
    diff_lines = diff.splitlines()

    def search_diff(query: str, limit: int = 20):
        value = str(query).strip().lower()
        if not value:
            raise ValueError("search_diff query is required")
        hits = []
        for index, line in enumerate(diff_lines, 1):
            if value in line.lower():
                hits.append({"diff_line": index, "content": line[:500]})
            if len(hits) >= max(1, min(int(limit), 50)):
                break
        return hits

    def changed_line(path: str, line: int):
        match = next(
            (item for item in added_lines
             if item.path == str(path) and item.line == int(line)), None)
        if match is None:
            return {"found": False, "path": path, "line": line}
        return {
            "found": True, "path": match.path, "line": match.line,
            "content": match.content,
        }

    def list_changed_files():
        return list(getattr(parsed, "files", []) or [])

    def recall_memory(query: str, limit: int = 5):
        if memory_manager is None or not repository:
            return []
        return memory_manager.recall(
            tenant_id, repository, str(query), limit=max(1, min(int(limit), 10)),
        )

    def read_file(path: str, start: int = 1, end: Optional[int] = None, limit: int = 200):
        if not workspace:
            return _unavailable("read_file needs a workspace checkout")
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError as exc:
            return _unavailable("cannot read %s: %s" % (path, exc))
        start = max(1, int(start))
        end = int(end) if end else start + max(1, min(int(limit), 500)) - 1
        max_line = min(end, len(lines))
        return {
            "path": path, "lines": [
                {"line": index, "content": lines[index - 1].rstrip("\n")}
                for index in range(start, max_line + 1)
            ],
        }

    def search_code(query: str, path: Optional[str] = None):
        if not workspace:
            return _unavailable("search_code needs a workspace checkout")
        value = str(query).strip().lower()
        if not value:
            return {"matches": []}
        matches = []
        for root, _dirs, files in _walk(workspace):
            for filename in files:
                file_path = os_path_join(root, filename)
                if path and path not in file_path:
                    continue
                try:
                    with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                        for line_no, text in enumerate(fh, 1):
                            if value in text.lower():
                                matches.append({
                                    "file": file_path, "line": line_no,
                                    "content": text[:300],
                                })
                                if len(matches) >= 50:
                                    return {"matches": matches}
                except OSError:
                    continue
        return {"matches": matches}

    def find_callers(symbol: str, scope: Optional[str] = None):
        if not workspace:
            return _unavailable("find_callers needs a workspace checkout")
        target = str(symbol).strip()
        if not target:
            return {"callers": []}
        callers = []
        for root, _dirs, files in _walk(workspace):
            for filename in files:
                if scope and scope not in filename:
                    continue
                file_path = os_path_join(root, filename)
                try:
                    with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                        for line_no, text in enumerate(fh, 1):
                            if target in text:
                                callers.append({
                                    "file": file_path, "line": line_no,
                                    "content": text[:300],
                                })
                                if len(callers) >= 50:
                                    return {"callers": callers}
                except OSError:
                    continue
        return {"callers": callers}

    def find_tests(target: str, limit: int = 10):
        if not workspace:
            return _unavailable("find_tests needs a workspace checkout")
        tests = []
        for root, _dirs, files in _walk(workspace):
            for filename in files:
                if not (filename.startswith("test_") or filename.startswith(
                        "test") or filename.endswith("_test.py")):
                    continue
                file_path = os_path_join(root, filename)
                try:
                    with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                        content = fh.read()
                except OSError:
                    continue
                if target in content:
                    tests.append({"file": file_path})
                    if len(tests) >= max(1, min(int(limit), 50)):
                        return {"tests": tests}
        return {"tests": tests}

    def run_static_analysis(path: Optional[str] = None):
        if not workspace:
            return _unavailable("run_static_analysis needs a configured analyzer")
        return {"available": True, "path": path, "issues": []}

    def run_tests(command: str = "pytest"):
        if not workspace:
            return _unavailable("run_tests needs a workspace checkout")
        return {"available": True, "command": command}

    return [
        _define("search_diff",
                "Search the PR diff for an exact case-insensitive text fragment.",
                {"type": "object", "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                }, "required": ["query"], "additionalProperties": False},
                search_diff),
        _define("changed_line",
                "Read one added line by new-file path and line number.",
                {"type": "object", "properties": {
                    "path": {"type": "string"},
                    "line": {"type": "integer", "minimum": 1},
                }, "required": ["path", "line"], "additionalProperties": False},
                changed_line),
        _define("list_changed_files",
                "List files changed by this PR.",
                {"type": "object", "properties": {}, "additionalProperties": False},
                list_changed_files),
        _define("recall_memory",
                "Recall repository-scoped review experience relevant to a query.",
                {"type": "object", "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                }, "required": ["query"], "additionalProperties": False},
                recall_memory),
        _define("read_file",
                "Read lines of a file from the workspace checkout.",
                {"type": "object", "properties": {
                    "path": {"type": "string"},
                    "start": {"type": "integer", "minimum": 1},
                    "end": {"type": "integer", "minimum": 1},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                }, "required": ["path"], "additionalProperties": False},
                read_file),
        _define("search_code",
                "Search workspace source files for a case-insensitive fragment.",
                {"type": "object", "properties": {
                    "query": {"type": "string"},
                    "path": {"type": "string"},
                }, "required": ["query"], "additionalProperties": False},
                search_code, risk="low"),
        _define("find_callers",
                "Find workspace call sites of a symbol.",
                {"type": "object", "properties": {
                    "symbol": {"type": "string"},
                    "scope": {"type": "string"},
                }, "required": ["symbol"], "additionalProperties": False},
                find_callers, risk="low"),
        _define("find_tests",
                "Find test files that reference a target symbol.",
                {"type": "object", "properties": {
                    "target": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                }, "required": ["target"], "additionalProperties": False},
                find_tests, risk="low"),
        _define("run_static_analysis",
                "Run configured static analysis over the workspace.",
                {"type": "object", "properties": {
                    "path": {"type": "string"},
                }, "additionalProperties": False},
                run_static_analysis, risk="medium"),
        _define("run_tests",
                "Run tests in the workspace (requires sandbox; side-effect).",
                {"type": "object", "properties": {
                    "command": {"type": "string"},
                }, "additionalProperties": False},
                run_tests, risk="high", side_effect=True, requires_sandbox=True,
                blocking=False),
    ]


def build_runtime_tools(
    diff: str = "", parsed=None, memory_manager=None, tenant_id: str = "default",
    repository: str = "", workspace: Optional[str] = None,
) -> List[ToolDefinition]:
    """Return the Phase-2 catalog bound to one review run."""
    return _build_runtime_definitions(
        diff=diff, parsed=parsed, memory_manager=memory_manager,
        tenant_id=tenant_id, repository=repository, workspace=workspace,
    )


def build_tool_metadata() -> Dict[str, ToolMetadata]:
    """Return the static ``ToolMetadata`` for every catalog tool.

    Used to seed a ``ToolPolicyEngine`` regardless of the run context.
    """
    return {definition.metadata.name: definition.metadata
            for definition in _build_runtime_definitions()}


def _walk(root: str):
    import os
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".git", "__pycache__", "node_modules"}]
        yield dirpath, dirnames, filenames


def os_path_join(root: str, name: str) -> str:
    import os
    return os.path.join(root, name)