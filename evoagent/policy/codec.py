"""Policy serialization codec (plan section 5.3).

A single place that rounds-trips an |ExecutionPolicy| to/from a plain dict and
produces a stable identity hash.  SQLite / Postgres / JSON adapters must never
maintain their own policy serialization; they delegate here so ordering,
``retryable_failures`` set normalization and version identity stay consistent.
"""
import hashlib
import json
from typing import Any, Dict

from .models import ExecutionPolicy


def policy_to_dict(policy: ExecutionPolicy) -> Dict[str, Any]:
    """Serialize a policy to a storable dict (order-stable)."""
    return policy.to_dict()


def policy_from_dict(value: Dict[str, Any]) -> ExecutionPolicy:
    """Rehydrate an |ExecutionPolicy| from a dict produced by ``policy_to_dict``."""
    return ExecutionPolicy.from_dict(value)


def canonical_json(policy: ExecutionPolicy) -> str:
    """Render a policy to canonical JSON for identity / diff hashing."""
    return json.dumps(policy.to_dict(), sort_keys=True, separators=(",", ":"))


def policy_signature(policy: ExecutionPolicy) -> str:
    """A stable hash of a policy's full serialized content."""
    return hashlib.sha256(canonical_json(policy).encode("utf-8")).hexdigest()


__all__ = [
    "canonical_json",
    "policy_from_dict",
    "policy_signature",
    "policy_to_dict",
]