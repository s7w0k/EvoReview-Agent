"""Failure Injection (plan §10).

A deterministic catalogue of failure scenarios the Coordinator (or a test
harness) can inject, plus a single :func:`inject` entry point.  Each failure has
a unique code and a "where" it applies (planning / graph / delegate / replan /
loop).  This lets Evaluation V4 and the observability suite prove the system
degrades safely.
"""
from typing import Any, Dict, List, Optional

# plan §10 catalogue: code -> (domain, description)
FAILURE_CATALOG: Dict[str, Dict[str, str]] = {
    "COORD_PLANNING_FAILURE": ("planning", "semantic change analysis errors out"),
    "INVALID_GRAPH": ("graph", "proposed task graph fails validation"),
    "GRAPH_CYCLE": ("graph", "task graph contains a dependency cycle"),
    "TIMEOUT": ("delegate", "a delegated A2A call times out"),
    "AGENT_UNAVAILABLE": ("delegate", "target specialist is unavailable"),
    "MALFORMED_REPLAN": ("replan", "critic emits a malformed replan request"),
    "LOW_CONFIDENCE_LOOP": ("loop", "verifier confidence never crosses threshold"),
    "REPEATED_PATCH_FAILURE": ("fix", "steady-state patch failures in Fix"),
    "DUPLICATE_REPLAN": ("replan", "duplicate replan fingerprint observed"),
    "TASK_LOST": ("delegate", "a delegated task is lost mid-flight"),
    "STALE_ARTIFACT": ("delegate", "an agent produced a stale artifact"),
    "CORRELATION_MISMATCH": ("a2a", "a2a correlation id does not match"),
    "PARALLEL_BRANCH_FAILURE": ("scheduler", "one parallel branch fails"),
}


class FailureInjector:
    """Deterministic failure injection knob."""

    def __init__(self, enabled_codes: Optional[List[str]] = None,
                 seed: int = 0):
        self.enabled = set(enabled_codes or [])
        self.seed = seed
        self.injected: List[str] = []

    def activate(self, codes: List[str]) -> None:
        for code in codes:
            if code in FAILURE_CATALOG:
                self.enabled.add(code)

    def should_inject(self, domain: str) -> bool:
        return any(
            FAILURE_CATALOG.get(code, ("?", ""))[0] == domain
            for code in self.enabled
        )

    @staticmethod
    def _domain(code: str) -> str:
        return FAILURE_CATALOG.get(code, ("?", ""))[0]

    def inject(self, domain: str) -> Optional[str]:
        """Return a triggered failure code for a domain (or None)."""
        code = next((c for c in sorted(self.enabled)
                     if FAILURE_CATALOG[c][0] == domain), None)
        if code is not None:
            self.injected.append(code)
        return code


def inject(
    injector: Optional[FailureInjector], domain: str,
    else_result: Any = None,
) -> Any:
    """Run a domain check: return the injected failure code or ``else_result``."""
    if injector is not None and injector.enabled:
        code = injector.inject(domain)
        if code is not None:
            return {"injected": code, "domain": domain}
    return else_result


__all__ = ["FAILURE_CATALOG", "FailureInjector", "inject"]