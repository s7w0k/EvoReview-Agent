"""Safety, permission and dangerous-evolution interception (closed-loop WP8).

Pure, deterministic guards that run before any candidate may be materialized:
- agent-created skills stay declarative (no eval/import/network/shell/arbitrary file);
- ``permissions`` must be empty;
- procedure/tool proposals that request new permissions require manual review;
- artifact hash/signature/parent/source must be consistent;
- production runtime must be a real sandbox, not a bare ``python -I`` subprocess.
"""
import hashlib
import json
from typing import Any, Dict, List, Optional

FORBIDDEN_TOKENS = (
    "eval(", "exec(", "__import__", "import ", "subprocess", "os.system",
    "open(", "socket", "urllib", "requests", "http://", "shell=True",
    "base64.b64decode", "pickle.loads",
)


def dangerous_artifact_reasons(artifact: Dict[str, Any]) -> List[str]:
    """Return safety violations for an agent-created declarative artifact."""
    reasons: List[str] = []
    if artifact.get("permissions"):
        reasons.append("agent-created skill must have empty permissions")
    for rule in artifact.get("rules", []):
        for field in ("match", "explanation", "fix", "test", "title"):
            text = str(rule.get(field, "") or "")
            lowered = text.lower()
            for token in FORBIDDEN_TOKENS:
                if token in lowered:
                    reasons.append(
                        "rule %s %s contains forbidden construct %r"
                        % (rule.get("rule_id", "?"), field, token)
                    )
    return reasons


def check_artifact_integrity(
    artifact: Dict[str, Any],
    sha256: str,
    *,
    parent_version: Optional[int] = None,
    provenance: Optional[Dict[str, Any]] = None,
    runtime_version: Optional[str] = None,
) -> List[str]:
    """Verify an artifact's hash, parent version, source and runtime fingerprint."""
    reasons: List[str] = []
    canonical = json.dumps(artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    computed = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if computed != sha256:
        reasons.append("artifact hash mismatch")
    if parent_version is not None and int(parent_version) < 0:
        reasons.append("invalid parent version")
    provenance = provenance or {}
    if not provenance.get("origin"):
        reasons.append("missing provenance origin")
    if runtime_version is not None and provenance.get("runtime_version") != runtime_version:
        reasons.append("runtime version fingerprint mismatch")
    return reasons


def requires_manual_code_review(change_type: str, permissions: List[str]) -> bool:
    """Procedure/Tool proposals with new permissions require human code review."""
    if change_type in {"procedure_proposal", "tool_proposal"} and permissions:
        return True
    return False


def sandbox_adequate(runtime: Optional[Dict[str, Any]]) -> bool:
    """A production sandbox must be a container with no network and a read-only FS.

    A bare ``python -I`` subprocess is explicitly not production-grade.
    """
    runtime = runtime or {}
    if runtime.get("isolation") != "container":
        return False
    if runtime.get("network") is not False:
        return False
    if runtime.get("filesystem") != "read-only":
        return False
    return True
