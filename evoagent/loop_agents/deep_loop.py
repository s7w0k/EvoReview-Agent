"""Deeper Local Agent Loops (plan §7).

Every agent's loop is extended with:
* a deterministic :func:`evaluate_stop_condition` over a declared stop-condition
  spec (goal_satisfied / confidence_threshold_met / budget_exhausted /
  no_progress / tool_unavailable / policy_blocked); and
* a :class:`VerificationStrategySelector` that picks the strongest verification
  strategy from the rule signature / semantic reproduction / targeted test /
  compile check / cross-check / context-inspection ladder.

These are pure deterministic helpers -- no raw chain-of-thought is recorded.
"""
from typing import Any, Dict, List, Optional

STOP_CODES = (
    "goal_satisfied",
    "confidence_threshold_met",
    "budget_exhausted",
    "no_progress",
    "tool_unavailable",
    "policy_blocked",
)

#: Representative reasoning labels for observability/attribution (not CoT).
STOP_REASON_LABEL = {
    "goal_satisfied": "GOAL_SATISFIED",
    "confidence_threshold_met": "CONFIDENCE_THRESHOLD_MET",
    "budget_exhausted": "BUDGET_EXHAUSTED",
    "no_progress": "NO_PROGRESS",
    "tool_unavailable": "TOOL_UNAVAILABLE",
    "policy_blocked": "POLICY_BLOCKED",
}


def evaluate_stop_condition(
    spec: Dict[str, Any],
    *,
    goal_satisfied: Optional[bool] = None,
    confidence: Optional[float] = None,
    steps: int = 0,
    max_steps: int = 0,
    tool_calls: int = 0,
    max_tool_calls: int = 0,
    progress_made: bool = True,
    tool_unavailable: bool = False,
    policy_blocked: bool = False,
) -> Dict[str, Any]:
    """Evaluate a stop-condition spec and return ``{stop, code, label}``."""
    spec = spec or {}

    if policy_blocked:
        return _stop("policy_blocked")
    if tool_unavailable:
        return _stop("tool_unavailable")
    if goal_satisfied is True:
        return _stop("goal_satisfied")
    threshold = spec.get("confidence_threshold_met")
    if threshold is not None and confidence is not None and confidence >= float(threshold):
        return _stop("confidence_threshold_met")
    if max_steps and steps >= max_steps:
        return _stop("budget_exhausted")
    if max_tool_calls and tool_calls >= max_tool_calls:
        return _stop("budget_exhausted")
    if not progress_made:
        return _stop("no_progress")
    return {"stop": False, "code": "", "label": "CONTINUE"}


def _stop(code: str) -> Dict[str, Any]:
    return {"stop": True, "code": code, "label": STOP_REASON_LABEL.get(code, code)}


# ---------------------------------------------------------------------------
# Verification strategy selector (plan §7 VerificationStrategySelector)
# ---------------------------------------------------------------------------

VERIFICATION_STRATEGIES = (
    "rule_signature",
    "semantic_reproduction",
    "targeted_test",
    "compile_check",
    "cross_check",
    "context_inspection",
)


def pick_verification_strategy(
    finding: Dict[str, Any],
    *,
    evidence: str = "",
    has_rule_signature: bool = True,
    has_semantic_repro: bool = True,
    has_sandbox: bool = False,
) -> str:
    """Select the strongest verification strategy for a finding (plan §7).

    Prioritised (strongest first): targeted test (real execution) > rule
    signature > semantic reproduction > cross check > context inspection.
    Falls back to ``context_inspection`` when nothing else can produce
    independent evidence.
    """
    finding = finding or {}
    evidence = evidence or str(finding.get("evidence") or "")
    rule_id = str(finding.get("rule_id") or "")
    is_rule_based = rule_id.startswith(("SEC-", "REL-"))
    has_semantic_repro = bool(has_semantic_repro and evidence)

    # A sandboxed targeted test beats static reproduction when we have a clue
    # of the risk location and a rule prefix.
    if has_sandbox and has_rule_signature and is_rule_based and evidence:
        return "targeted_test"
    if has_rule_signature and is_rule_based and not has_semantic_repro:
        return "rule_signature"
    if has_semantic_repro:
        return "semantic_reproduction"
    if is_rule_based:
        return "cross_check"
    return "context_inspection"


def select_verifier_strategy_for(
    findings: List[Dict[str, Any]], *, has_sandbox: bool = False,
) -> Dict[str, str]:
    """Map each finding key to its chosen strategy (deterministic)."""
    return {_key(f): pick_verification_strategy(f, has_sandbox=has_sandbox)
            for f in findings}


def _key(f: Dict[str, Any]) -> str:
    return "%s:%s:%s" % (f.get("rule_id"), f.get("path"), f.get("line"))


__all__ = [
    "STOP_CODES", "STOP_REASON_LABEL", "evaluate_stop_condition",
    "VERIFICATION_STRATEGIES", "pick_verification_strategy",
    "select_verifier_strategy_for",
]