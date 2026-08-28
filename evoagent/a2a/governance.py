"""A2A governance (Phase 8): token auth, execution policy checks and artifact
sanitisation.

Remote agents require a shared service token; the coordinator authorises each
target Agent *before* invoking it (identity / capability / tenant).  Remote
artifacts are schema- and content-validated, then sanitised, before they may
enter the main Harness.
"""

import hmac
import re
from typing import Any, Dict, List, Optional

from .models import A2AArtifact

_DANGEROUS = re.compile(
    r"(?i)(<script\s*>|</script\s*>|javascript:|onload=|onerror="
    r"|system\(|os\.system|__import__|pickle\.loads|yaml\.unsafe"
    r"|eval\s*\(|exec\s*\()"
)


class AuthorizationPolicy:
    """Per-agent authorization: which identity may use which capability for
    which tenant."""

    def __init__(
        self, agent_id: str, *, allowed_capabilities: Optional[List[str]] = None,
        tenant_scope: Optional[List[str]] = None,
    ):
        self.agent_id = agent_id
        self.allowed_capabilities = set(allowed_capabilities or [])
        self.tenant_scope = set(tenant_scope or ["default"])

    def allows(self, capability: str, tenant_id: str = "default") -> bool:
        if self.allowed_capabilities and capability not in self.allowed_capabilities:
            return False
        if self.tenant_scope and tenant_id not in self.tenant_scope:
            return False
        return True


def verify_token(expected: str, provided: str) -> bool:
    if expected == "":
        return True  # token auth disabled (non-production)
    return hmac.compare_digest(expected, provided or "")


class ArtifactSanitizer:
    """Schema/content/finding validation + observation sanitisation.

    ``validate`` raises on a malformed artifact; ``sanitize`` strips risky text
    from string fields so a hostile remote cannot smuggle markup/instructions
    into the Harness.
    """

    def __init__(self, max_findings: int = 500, max_field: int = 4000):
        self.max_findings = max_findings
        self.max_field = max_field

    def validate(self, artifact: Dict[str, Any]) -> A2AArtifact:
        obj = A2AArtifact.from_dict(artifact)
        items = (obj.content or {}).get("findings", [])
        if not isinstance(items, list):
            raise ValueError("artifact content.findings must be a list")
        if len(items) > self.max_findings:
            raise ValueError("artifact exceeds max_findings")
        seen = set()
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("artifact findings must be objects")
            for field in ("path", "line", "rule_id", "title"):
                if field not in item:
                    raise ValueError("artifact finding missing %r" % field)
            identity = (item.get("path"), item.get("line"), item.get("rule_id"))
            if identity in seen:
                raise ValueError("duplicate finding identity: " + repr(identity))
            seen.add(identity)
        return obj

    def sanitize(self, artifact: A2AArtifact) -> A2AArtifact:
        items = (artifact.content or {}).get("findings", [])
        cleaned = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            entry = dict(item)
            for key in ("title", "explanation", "evidence", "fix", "test"):
                if key in entry and isinstance(entry[key], str):
                    entry[key] = self._clean(entry[key])
            cleaned.append(entry)
        content = dict(artifact.content or {})
        content["findings"] = cleaned
        return A2AArtifact(
            artifact_id=artifact.artifact_id,
            task_id=artifact.task_id,
            artifact_type=artifact.artifact_type,
            producer=artifact.producer,
            content=content,
            metadata=dict(artifact.metadata or {}),
        )

    def _clean(self, value: str) -> str:
        text = value[: self.max_field]
        text = _DANGEROUS.sub("[sanitized]", text)
        # Strip newlines/control chars from one-line evidence fields.
        return " ".join(text.splitlines())


__all__ = ["AuthorizationPolicy", "verify_token", "ArtifactSanitizer"]