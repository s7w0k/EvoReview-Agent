"""Deterministic AST-based semantic analysis over added lines.

The rule reviewers match single lines; this layer groups the added lines of a
file into a logical snapshot and uses the stdlib ``ast`` module to detect
cross-statement / cross-function issues that a per-line regex cannot see:

- tainted input flowing into ``eval``/``exec`` (SEM-TAINTED-EXEC);
- tainted input or string concatenation flowing into subprocess/os commands
  (SEM-TAINTED-SUBPROCESS);
- ``shell=True`` combined with a variable command (SEM-SHELL-INJECTION);
- file handles opened without ``with`` and never closed (SEM-UNCLOSED-RESOURCE).

Everything is deterministic, side-effect free and uses zero third-party
dependencies.  When a snapshot cannot be parsed (for example the diff only
contains the body of a function), individual statements are analysed instead.
"""
import ast
import textwrap
from typing import Any, Dict, List, Optional


# External input sources: parameter names and read-style calls.
_TAINT_NAMES = frozenset({
    "user_input", "user_input_data", "request", "data", "payload", "cmd",
    "command", "query", "input_data", "user_data", "args", "value",
})
_TAINT_ATTRS = frozenset({
    "environ", "argv", "getenv", "getvalue",
})
_TAINT_CALLS = frozenset({"input", "getenv", "raw_input"})

# Dynamic execution sinks.
_EXEC_SINKS = frozenset({"eval", "exec"})

# Command execution sinks.
_SUBPROCESS_SINKS = frozenset({
    "system", "popen", "call", "run", "check_call", "check_output",
    "Popen", "popen2", "popen3", "popen4",
})
_SUBPROCESS_MODULES = frozenset({"subprocess", "os", "commands"})

# File-open style calls whose result should be used as a context manager.
_RESOURCE_OPENERS = frozenset({"open"})

SEVERITY_MAP = {
    "SEM-TAINTED-EXEC": "critical",
    "SEM-TAINTED-SUBPROCESS": "high",
    "SEM-SHELL-INJECTION": "high",
    "SEM-UNCLOSED-RESOURCE": "medium",
}

_EXPLANATIONS = {
    "SEM-TAINTED-EXEC": (
        "外部输入经过赋值传播后流入动态执行函数；当输入可被外部影响时，"
        "攻击者可能执行任意代码。"
    ),
    "SEM-TAINTED-SUBPROCESS": (
        "外部输入或拼接字符串被用作系统命令；包含空格、分号或命令替换字符时"
        "可能产生命令注入。"
    ),
    "SEM-SHELL-INJECTION": (
        "shell=True 同时命令包含变量；shell 会解释拼接内容，扩大注入风险。"
    ),
    "SEM-UNCLOSED-RESOURCE": (
        "文件句柄以赋值方式打开且未使用 with，也没有显式 close；异常路径可能"
        "泄漏句柄。"
    ),
}

_FIXES = {
    "SEM-TAINTED-EXEC": "移除动态执行；使用显式解析器、命令映射表或严格白名单。",
    "SEM-TAINTED-SUBPROCESS": "改用参数数组并保持 shell=False；对允许值白名单校验。",
    "SEM-SHELL-INJECTION": "使用参数数组调用并保持 shell=False。",
    "SEM-UNCLOSED-RESOURCE": "使用 with 语句管理资源生命周期，或确保 finally 中关闭。",
}

_TESTS = {
    "SEM-TAINTED-EXEC": "加入含恶意表达式与边界输入的测试，断言输入不被当作代码执行。",
    "SEM-TAINTED-SUBPROCESS": "加入含空格、分号与命令替换字符的输入测试。",
    "SEM-SHELL-INJECTION": "加入包含空格、分号与命令替换字符的输入测试。",
    "SEM-UNCLOSED-RESOURCE": "加入触发异常的路径测试，断言句柄被可靠释放。",
}


def _func_name(node) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parts = []
        current = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        return ".".join(reversed(parts))
    return None


def _is_taint_source(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id in _TAINT_NAMES
    if isinstance(node, ast.Attribute):
        name = _func_name(node)
        return bool(name) and name.split(".")[-1] in _TAINT_ATTRS
    if isinstance(node, ast.Call):
        return _func_name(node.func) in _TAINT_CALLS
    return False


def _references(expr: ast.AST, names: set) -> bool:
    """True if any Name referenced by expr is in names."""
    return any(
        isinstance(node, ast.Name) and node.id in names
        for node in ast.walk(expr)
    )


def _collect_tainted(module: ast.Module) -> set:
    """Fixed-point taint propagation over assignments in the snapshot."""
    tainted: set = set()
    changed = True
    while changed:
        changed = False
        for node in ast.walk(module):
            targets = []
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                if isinstance(node, ast.AnnAssign):
                    targets = [node.target]
                else:
                    targets = list(node.targets)
            elif isinstance(node, ast.AugAssign):
                targets = [node.target]
            else:
                continue
            if value is None:
                continue
            source = _is_taint_source(value) or _references(value, tainted)
            if not source:
                continue
            for target in targets:
                names = {
                    item.id for item in ast.walk(target)
                    if isinstance(item, ast.Name)
                }
                for name in names - tainted:
                    tainted.add(name)
                    changed = True
    return tainted


def _shell_true(call: ast.Call) -> bool:
    for keyword in call.keywords:
        if keyword.arg == "shell" and isinstance(keyword.value, ast.Constant):
            return bool(keyword.value.value)
    return False


def _has_concatenation(call: ast.Call) -> bool:
    args = [item for item in call.args if not isinstance(item, ast.Starred)]
    for arg in args:
        if isinstance(arg, (ast.BinOp, ast.JoinedStr, ast.FormattedValue)):
            return True
        if isinstance(arg, ast.BinOp) or (
            isinstance(arg, ast.Call) and _func_name(arg.func) in {"format"}
        ):
            return True
    return False


def _has_variable_arg(call: ast.Call, tainted: set) -> bool:
    args = [item for item in call.args if not isinstance(item, ast.Starred)]
    for arg in args:
        if _references(arg, tainted):
            return True
        if isinstance(arg, (ast.Name, ast.Attribute, ast.Subscript)):
            return True
    return False


def _command_arg(call: ast.Call):
    """First positional argument of a subprocess/os command call."""
    for item in call.args:
        if isinstance(item, ast.Starred):
            continue
        return item
    return None


def _unclosed_resources(module: ast.Module) -> List[ast.AST]:
    findings_nodes = []

    def scan_scope(body: list) -> None:
        closed = {
            item.attr
            for item in ast.walk(ast.Module(body=body, type_ignores=[]))
            if isinstance(item, ast.Attribute) and item.attr == "close"
        }
        for node in body:
            for sub in ast.walk(node):
                if not (isinstance(sub, ast.Assign) and len(sub.targets) == 1):
                    continue
                target = sub.targets[0]
                if not isinstance(target, ast.Name):
                    continue
                if not (isinstance(sub.value, ast.Call)
                        and _func_name(sub.value.func) == "open"):
                    continue
                if target.id in closed:
                    continue
                findings_nodes.append(sub)
            if isinstance(node, ast.FunctionDef):
                scan_scope(node.body)

    scan_scope(module.body)
    return findings_nodes


def _iter_sink_calls(module: ast.Module, tainted: set) -> List[Dict[str, Any]]:
    issues = []
    for node in ast.walk(module):
        if not isinstance(node, ast.Call):
            continue
        func = _func_name(node.func) or ""
        base = func.split(".")[-1]
        module_part = func.split(".")[0] if "." in func else ""
        if module_part not in _SUBPROCESS_MODULES and base not in _EXEC_SINKS:
            continue
        args = [item for item in node.args if not isinstance(item, ast.Starred)]
        if base in _EXEC_SINKS:
            arg = _command_arg(node)
            if arg is not None and (_references(arg, tainted) or _is_taint_source(arg)):
                issues.append({
                    "rule_id": "SEM-TAINTED-EXEC", "node": node,
                    "sink": func,
                })
            continue
        # subprocess / os system-family call.
        if _shell_true(node) and _has_variable_arg(node, tainted):
            issues.append({
                "rule_id": "SEM-SHELL-INJECTION", "node": node, "sink": func,
            })
        if (
            _references(node, tainted) or _has_concatenation(node)
            or any(_is_taint_source(arg) for arg in args)
        ):
            issues.append({
                "rule_id": "SEM-TAINTED-SUBPROCESS", "node": node, "sink": func,
            })
    return issues


def _snapshot_of(added_lines) -> tuple:
    """Build (source, real_line_numbers, content_by_line) for one file."""
    parts = []
    real_lines = []
    content_by_line = {}
    for item in added_lines:
        parts.append(item.content)
        real_lines.append(item.line)
        content_by_line[item.line] = item.content
    return "\n".join(parts), real_lines, content_by_line


def _make_finding(rule_id: str, path: str, line: int, evidence: str) -> Dict[str, Any]:
    severity = SEVERITY_MAP.get(rule_id, "medium")
    return {
        "rule_id": rule_id,
        "severity": severity,
        "title": rule_id,
        "explanation": _EXPLANATIONS.get(rule_id, ""),
        "path": path,
        "line": line,
        "evidence": evidence[:240],
        "fix": _FIXES.get(rule_id, ""),
        "test": _TESTS.get(rule_id, ""),
        "analyzer": "ast",
    }


def _analyze_snapshot(path: str, added_lines) -> List[Dict[str, Any]]:
    source, real_lines, content_by_line = _snapshot_of(added_lines)
    if not source.strip():
        return []
    findings: List[Dict[str, Any]] = []

    def emit(rule_id: str, snapshot_line: int) -> None:
        idx = max(0, min(snapshot_line - 1, len(real_lines) - 1))
        real = real_lines[idx]
        evidence = content_by_line.get(real, "").strip()
        findings.append(_make_finding(rule_id, path, real, evidence))

    try:
        module = ast.parse(source)
    except SyntaxError:
        module = None

    if module is not None:
        tainted = _collect_tainted(module)
        for issue in _iter_sink_calls(module, tainted):
            emit(issue["rule_id"], issue["node"].lineno)
        for node in _unclosed_resources(module):
            emit("SEM-UNCLOSED-RESOURCE", node.lineno)
        return findings

    # Snapshot did not parse (e.g. only a function body): analyse each added
    # line as an independent statement after dedenting.
    for item in added_lines:
        try:
            statement = ast.parse(textwrap.dedent(item.content), mode="exec")
        except (SyntaxError, IndentationError):
            continue
        tainted = _collect_tainted(statement)
        for issue in _iter_sink_calls(statement, tainted):
            findings.append(_make_finding(
                issue["rule_id"], path, item.line, item.content.strip(),
            ))
    return findings


def analyze_added_lines(added_lines) -> List[Dict[str, Any]]:
    """Analyse parsed diff added lines and return semantic findings.

    ``added_lines`` items must expose ``path``, ``line`` and ``content``.
    The result is a list of finding dicts compatible with ``Finding``.
    """
    findings: List[Dict[str, Any]] = []
    seen = set()
    by_path: Dict[str, list] = {}
    for item in added_lines:
        if item.path.endswith((".lock", ".min.js", ".map")):
            continue
        by_path.setdefault(item.path, []).append(item)
    for path, lines in by_path.items():
        for finding in _analyze_snapshot(path, lines):
            key = (finding["rule_id"], finding["path"], finding["line"])
            if key in seen:
                continue
            seen.add(key)
            findings.append(finding)
    return findings
