"""Runtime policy version repository.

Phase 1 needs a light store that records every policy version a review actually
ran under, so a later replay reads the *historical* policy instead of
the current active one.  This in-memory implementation is the seam that Phase 5
replaces with SQLite tables (``runtime_policy_versions`` / ``deployments``).
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .models import ExecutionPolicy


@dataclass
class PolicyDeploymentRow:
    """How a policy is being served for a tenant/repo/risk scope."""

    policy_id: str
    version: int
    status: str = "active"           # active | shadow | canary | promoted | rolled_back
    tenant_id: str = "default"
    repository: str = "*"
    risk_level: str = "*"
    created_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class RuntimePolicyRepository:
    """Versioned runtime-policy store with active-policy resolution."""

    def __init__(self):
        self._policies: Dict[str, ExecutionPolicy] = {}
        self._versions: Dict[str, int] = {}     # policy_id -> max version
        self._deployments: List[PolicyDeploymentRow] = []
        self._seq = 0

    # -- definition ----------------------------------------------------------

    def register(
        self, policy: ExecutionPolicy, *, status: str = "active",
        tenant_id: str = "default", repository: str = "*", risk_level: str = "*",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Register ``policy`` and return the concrete version number."""
        self._seq += 1
        self._policies[policy.policy_id] = policy
        version = max(self._versions.get(policy.policy_id, 0),
                      policy.policy_version)
        self._versions[policy.policy_id] = version
        self._deployments.append(PolicyDeploymentRow(
            policy_id=policy.policy_id, version=version, status=status,
            tenant_id=tenant_id, repository=repository, risk_level=risk_level,
            created_at="",
            metadata=dict(metadata or {}),
        ))
        return version

    # -- read ----------------------------------------------------------------

    def get_policy(self, policy_id: str) -> Optional[ExecutionPolicy]:
        return self._policies.get(policy_id)

    def policy_version(self, policy_id: str) -> Optional[int]:
        return self._versions.get(policy_id)

    def active_policy(
        self, tenant_id: str = "default", repository: str = "*",
        risk_level: str = "*",
    ) -> Optional[ExecutionPolicy]:
        """Return the most-specific active policy row.
        Exact risk scope wins; otherwise the row that matches with ``*``.
        """
        active = [
            row for row in self._deployments
            if row.status == "active"
            and (row.tenant_id == tenant_id or row.tenant_id == "*")
            and (row.repository in (repository, "*"))
        ]
        if not active:
            return None
        scoped = [row for row in active if row.risk_level == risk_level]
        pool = scoped if scoped else active
        best = max(pool, key=lambda r: self._versions.get(r.policy_id, 0))
        return self._policies.get(best.policy_id)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "deployments": [
                {
                    "policy_id": row.policy_id, "version": row.version,
                    "status": row.status, "tenant_id": row.tenant_id,
                    "repository": row.repository, "risk_level": row.risk_level,
                    "metadata": dict(row.metadata),
                }
                for row in self._deployments
            ],
            "policy_count": len(self._policies),
        }