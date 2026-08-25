"""Gradual canary rollout for a policy candidate.

Per the plan (section 9.7): after a candidate passes replay, route a small
traffic share (e.g. 5%) to it while the rest stays on baseline, then monitor
quality / failure / cost / latency.  Continuous degradation triggers automatic
rollback (see ``policy_evolution.rollback``).
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional

from .objective import EvolutionMetrics


class CanaryVerdict(str, Enum):
    PROMOTE = "PROMOTE"
    HOLD = "HOLD"
    ROLLBACK = "ROLLBACK"


@dataclass
class CanaryConfig:
    traffic_share: float = 0.05         # candidate share of traffic
    promotion_threshold: float = 0.02    # utility improvement required to promote
    max_run_steps: int = 100             # steps before forcing a decision
    sample_size: Optional[int] = None    # optional hard cap on observations


@dataclass
class CanaryDecision:
    verdict: CanaryVerdict
    observed: int = 0
    reasons: List[str] = field(default_factory=list)


class PolicyCanary:
    """Collects candidate / baseline observations and emits a canary verdict."""

    def __init__(self, config: Optional[CanaryConfig] = None,
                 rollback_callback: Optional[Callable] = None):
        self._config = config or CanaryConfig()
        self._rollback_callback = rollback_callback
        self._baseline_agg = _MetricAccumulator()
        self._candidate_agg = _MetricAccumulator()

    def record(self, stream: str, metrics: EvolutionMetrics) -> None:
        """Record one task's metrics; ``stream`` is "baseline" or "candidate"."""
        stream = stream.lower()
        if stream == "baseline":
            self._baseline_agg.add(metrics)
        elif stream == "candidate":
            self._candidate_agg.add(metrics)
        else:
            raise ValueError(f"unknown canary stream: {stream!r}")

    @property
    def observed(self) -> int:
        return self._candidate_agg.count

    def decide(self) -> CanaryDecision:
        candidate = self._candidate_agg.mean()
        if self._candidate_agg.is_empty():
            return CanaryDecision(verdict=CanaryVerdict.HOLD, observed=0)

        # 1) hard failure: candidate becomes less reliable than baseline
        if candidate.failure_rate > 0.0:
            decision = CanaryDecision(
                verdict=CanaryVerdict.ROLLBACK, observed=self.observed,
                reasons=["candidate stream has non-zero failure rate"])
            self._notify_rollback(decision)
            return decision

        baseline = self._baseline_agg.mean()
        utility = self._utility(candidate, baseline)
        improvement = utility - self._utility(baseline, baseline)

        if self.observed >= (self._config.sample_size or 0) and \
                self._config.sample_size is not None:
            if improvement > 0:
                return CanaryDecision(
                    verdict=CanaryVerdict.PROMOTE, observed=self.observed,
                    reasons=[f"utility improved by {improvement:.4f}"])
            return CanaryDecision(
                verdict=CanaryVerdict.HOLD, observed=self.observed,
                reasons=["sample reached but no clear improvement"])

        if improvement >= self._config.promotion_threshold:
            return CanaryDecision(
                verdict=CanaryVerdict.PROMOTE, observed=self.observed,
                reasons=[f"utility improved by {improvement:.4f}"])
        if improvement <= -self._config.promotion_threshold:
            decision = CanaryDecision(
                verdict=CanaryVerdict.ROLLBACK, observed=self.observed,
                reasons=[f"utility degraded by {-improvement:.4f}"])
            self._notify_rollback(decision)
            return decision
        return CanaryDecision(verdict=CanaryVerdict.HOLD, observed=self.observed)

    def _utility(self, metrics, reference) -> float:
        from .objective import evolution_utility

        return evolution_utility(metrics, reference=reference)

    def _notify_rollback(self, decision: CanaryDecision) -> None:
        if self._rollback_callback is not None:
            self._rollback_callback(decision)


class _MetricAccumulator:
    """Sum-based accumulator that returns an ``EvolutionMetrics`` mean."""

    def __init__(self):
        self._sums: Dict[str, float] = {
            "quality_score": 0.0, "high_risk_recall": 0.0,
            "reliability_score": 0.0, "cost": 0.0, "latency": 0.0,
            "failure_rate": 0.0, "tool_calls": 0.0, "agent_steps": 0.0,
            "critical_misses": 0.0, "true_positives": 0.0,
            "false_positives": 0.0, "false_negatives": 0.0,
        }
        self.count = 0

    def add(self, metrics: EvolutionMetrics) -> None:
        for key in self._sums:
            self._sums[key] += getattr(metrics, key, 0.0)
        self.count += 1

    def is_empty(self) -> bool:
        return self.count == 0

    def mean(self) -> EvolutionMetrics:
        count = max(1, self.count)
        return EvolutionMetrics(
            quality_score=self._sums["quality_score"] / count,
            high_risk_recall=self._sums["high_risk_recall"] / count,
            critical_misses=int(self._sums["critical_misses"]),
            reliability_score=self._sums["reliability_score"] / count,
            cost=self._sums["cost"] / count,
            latency=self._sums["latency"] / count,
            failure_rate=self._sums["failure_rate"] / count,
            tool_calls=int(self._sums["tool_calls"]),
            agent_steps=int(self._sums["agent_steps"]),
            true_positives=int(self._sums["true_positives"]),
            false_positives=int(self._sums["false_positives"]),
            false_negatives=int(self._sums["false_negatives"]),
        )