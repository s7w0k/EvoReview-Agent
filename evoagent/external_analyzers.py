"""Optional external static analyzers (Bandit / Ruff) as subprocess adapters.

These tools are optional: when the executable is not installed the caller falls
back to the stdlib AST reviewer and logs a warning.  Output is converted into
the same finding-dict shape produced by :mod:`evoagent.ast_analysis`.
"""
import json
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List

from .models import Severity

_ANALYZER_COMMANDS = {
    "bandit": ["bandit", "-q", "-f", "json", "<files>"],
    "ruff": ["ruff", "check", "--output-format", "json", "<files>"],
}


class ToolUnavailable(RuntimeError):
    pass


def is_available(name: str) -> bool:
    return shutil.which(name) is not None


def _write_snapshots(files_by_path: Dict[str, str]) -> str:
    """Write per-file snapshots into a temp dir and return its path."""
    directory = tempfile.mkdtemp(prefix="evoagent-analyze-")
    for path, source in files_by_path.items():
        safe = path.replace("\\", "/").split("/")[-1] or "snapshot.py"
        with open(directory + "/" + safe, "w", encoding="utf-8") as handle:
            handle.write(source)
    return directory


def _findings_from_bandit(payload: dict) -> List[Dict[str, Any]]:
    findings = []
    severity_map = {
        "LOW": "low", "MEDIUM": "medium", "HIGH": "high", "UNDEFINED": "medium",
    }
    for result in payload.get("results", []):
        code = result.get("test_name", "") or ""
        if not code:
            continue
        line = int(result.get("line_number", 0) or 0)
        findings.append({
            "rule_id": "BANDIT-%s" % code,
            "severity": severity_map.get(result.get("issue_severity", ""), "medium"),
            "title": "Bandit: %s" % code,
            "explanation": str(result.get("issue_text", ""))[:500],
            "path": result.get("filename", ""),
            "line": line,
            "evidence": (result.get("code") or "").strip()[:240],
            "fix": "请人工评估该静态分析告警并修复根因。",
            "test": "加入覆盖该风险路径的回归测试。",
            "analyzer": "bandit",
        })
    return findings


def _findings_from_ruff(payload: list) -> List[Dict[str, Any]]:
    findings = []
    for result in payload or []:
        code = str(result.get("code", "") or "")
        if not code:
            continue
        location = result.get("location", {}) or {}
        line = int(location.get("row", 0) or 0)
        findings.append({
            "rule_id": "RUFF-%s" % code,
            "severity": "low",
            "title": "Ruff: %s" % code,
            "explanation": str(result.get("message", ""))[:500],
            "path": result.get("filename", ""),
            "line": line,
            "evidence": "",
            "fix": "请按 lint 规则修复。",
            "test": "",
            "analyzer": "ruff",
        })
    return findings


def run_analyzer(name: str, files_by_path: Dict[str, str]) -> List[Dict[str, Any]]:
    """Run an optional external analyzer over file snapshots.

    Raises ToolUnavailable when the executable is missing so the caller can
    degrade gracefully.
    """
    if not is_available(name):
        raise ToolUnavailable("external analyzer %r is not installed" % name)
    command = _ANALYZER_COMMANDS[name]
    template = command.index("<files>")
    directory = _write_snapshots(files_by_path)
    try:
        cmd = command[:template] + [directory] + command[template + 1:]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        if proc.returncode not in (0, 1):  # ruff/bandit use non-zero when findings exist
            return []
        payload = json.loads(proc.stdout or "{}" if name == "bandit" else proc.stdout or "[]")
        if name == "bandit":
            return _findings_from_bandit(payload)
        return _findings_from_ruff(payload)
    except (json.JSONDecodeError, subprocess.TimeoutExpired, OSError):
        return []
    finally:
        import shutil as _shutil
        _shutil.rmtree(directory, ignore_errors=True)


def snapshot_source(added_lines) -> Dict[str, str]:
    """Build {path: joined added-line snapshot} for a parsed diff."""
    by_path: Dict[str, str] = {}
    for item in added_lines:
        by_path.setdefault(item.path, []).append(item.content)
    return {path: "\n".join(lines) for path, lines in by_path.items()}


def severity_of(value: str) -> Severity:
    try:
        return Severity(value)
    except ValueError:
        return Severity.MEDIUM
