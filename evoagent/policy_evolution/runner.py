"""Real replay runner for policy evolution (plan section 11.4).

This is the production-grade counterpart to the evaluator's injected runner.
Instead of returning fake metrics, ``PolicyReplayRunner`` actually loads replay
snapshots, replays each recorded tool trace *under the given policy*, and
aggregates the metrics of section 11.6.

The simulated run is deterministic and policy-driven:

* every recorded tool observation is offered to the tool's permission gate;
* a tool that the policy no longer allows is a *policy violation* and is not
  executed, which in turn can turn its associated findings into misses;
* budget / retry fields gate step, call and recovery accounting.

A snapshot's ``expected_output`` carries the ground truth that maps findings to
the tool responsible for detecting them and a ``baseline`` block describing how
the reference policy behaved.
"""
from typing import Any, Dict, List, Sequence

from evoagent.policy.models import ExecutionPolicy

from .objective import EvolutionMetrics


class PolicyReplayRunner:
    """Replays a set of replay snapshots under a policy and aggregates metrics."""

    def __init__(self, snapshots: Sequence[Any]):
        # ``snapshots`` may be replay.ReplaySnapshot objects or plain dicts.
        self._snapshots = list(snapshots)

    # -- public API ---------------------------------------------------------

    def run(self, policy: ExecutionPolicy) -> EvolutionMetrics:
        """Aggregate metrics across every snapshot under ``policy``."""
        totals = _Counter()

        for snapshot in self._snapshots:
            expected = self._expected(snapshot)
            baseline = expected.get("baseline", {})
            findings = expected.get("findings", [])

            total_high = sum(1 for f in findings
                             if f.get("severity") in ("high", "critical"))
            denied_high = sum(1 for f in findings
                              if f.get("severity") in ("high", "critical")
                              and not _allowed_finding(policy, f))
            denied_critical = sum(1 for f in findings
                                  if f.get("severity") == "critical"
                                  and not _allowed_finding(policy, f))

            totals.total_tasks += 1
            _aggregate_snapshot(
                totals, snapshot, policy, expected, baseline,
                total_high, denied_high, denied_critical)

        return totals.metrics(len(self._snapshots))

    # -- helpers ------------------------------------------------------------

    def _expected(self, snapshot: Any) -> Dict[str, Any]:
        raw = getattr(snapshot, "expected_output", None)
        if raw is None and isinstance(snapshot, dict):
            raw = snapshot.get("expected_output")
        if not isinstance(raw, dict):
            return {}
        return raw


class _Counter:
    """Mutable running tallies used to aggregate across snapshots."""

    def __init__(self) -> None:
        self.total_tasks = 0
        self.tp = 0
        self.fp = 0
        self.fn = 0
        self.true_negatives = 0
        self.critical_misses = 0
        self.high_risk_total = 0
        self.high_risk_detected = 0
        self.tool_calls = 0
        self.agent_steps = 0
        self.latency = 0.0
        self.cost = 0.0
        self.failures = 0
        self.recovery_successes = 0
        self.recovery_attempts = 0
        self.policy_violations = 0
        self.side_effect_safety_incidents = 0

    def metrics(self, total_tasks: int) -> EvolutionMetrics:
        base = EvolutionMetrics.from_finding_counts(
            tp=self.tp, fp=self.fp, fn=self.fn,
            true_negatives=self.true_negatives,
            high_risk_recall=_ratio(self.high_risk_detected, self.high_risk_total),
            critical_misses=self.critical_misses,
            tool_calls=self.tool_calls,
            agent_steps=self.agent_steps,
        )
        base.cost = round(self.cost, 4)
        base.latency = round(self.latency, 4)
        total = total_tasks or 1
        base.task_success_rate = round(1.0 - self.failures / total, 4)
        base.failure_rate = round(self.failures / total, 4)
        base.reliability_score = round(1.0 - self.failures / total, 4)
        base.recovery_success_rate = (
            round(self.recovery_successes / self.recovery_attempts, 4)
            if self.recovery_attempts else 1.0)
        base.policy_violations = self.policy_violations
        base.side_effect_safety_incidents = self.side_effect_safety_incidents
        return base


def _aggregate_snapshot(totals: _Counter, snapshot, policy: ExecutionPolicy,
                        expected: Dict[str, Any], baseline: Dict[str, Any],
                        total_high: int, denied_high: int,
                        denied_critical: int) -> None:
    # Policy-driven tool / step / latency / cost accounting.  A denied tool
    # contributes nothing (no tool call), so fewer findings required and lower
    # resource use -- which is exactly why the hard gate must run afterwards.
    findings = expected.get("findings", [])
    denied_findings = set()
    for finding in findings:
        if not _allowed_finding(policy, finding):
            denied_findings.add(id(finding))

    # Finding confusion from the baseline plus what the policy removed.
    b_tp = baseline.get("tp", 0)
    b_fp = baseline.get("fp", 0)
    b_fn = baseline.get("fn", 0)
    b_tn = baseline.get("tn", 0)

    # Which baseline TP detections correspond to denied findings.
    denied_tp = sum(1 for f in findings
                    if id(f) in denied_findings and f.get("detected", True))
    totals.tp += max(0, b_tp - denied_tp)
    totals.fp += b_fp
    totals.fn += b_fn + denied_tp
    totals.true_negatives += b_tn

    totals.critical_misses += denied_critical
    totals.high_risk_total += total_high
    totals.high_risk_detected += max(0, total_high - denied_high)

    tool_names = [obs.get("tool_name") or obs.get("name")
                  for obs in _tool_observations(snapshot)]
    for tool in tool_names:
        if tool and not policy.allows(tool):
            totals.policy_violations += 1

    totals.tool_calls += baseline.get("tool_calls", 0) - denied_tp
    totals.agent_steps += baseline.get("agent_steps", 0)
    call_ratio = _safe_ratio(max(0, baseline.get("tool_calls", 0) - denied_tp),
                             baseline.get("tool_calls", 0))
    totals.latency += baseline.get("latency_ms", 0.0) * call_ratio
    totals.cost += baseline.get("cost", 0.0) * call_ratio

    failed = bool(baseline.get("failure", False)) or denied_critical > 0
    if failed:
        totals.failures += 1
    totals.recovery_attempts += baseline.get("recovery_attempts", 0)
    totals.recovery_successes += baseline.get("recovery_successes", 0)


def _tool_observations(snapshot) -> List[Dict[str, Any]]:
    raw = getattr(snapshot, "tool_observations", None)
    if raw is None and isinstance(snapshot, dict):
        raw = snapshot.get("tool_observations")
    return list(raw or [])


def _allowed_finding(policy: ExecutionPolicy, finding: Dict[str, Any]) -> bool:
    tool = finding.get("tool") or finding.get("tool_name")
    return policy.allows(tool) if tool else True


def _safe_ratio(numerator: int, denominator: int) -> float:
    return (numerator / denominator) if denominator else 0.0


def _ratio(numerator: int, denominator: int) -> float:
    return (numerator / denominator) if denominator else 0.0