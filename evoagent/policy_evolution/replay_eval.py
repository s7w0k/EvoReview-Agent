"""Replay-based evaluation of a candidate policy vs its baseline.

Per the plan (section 9.6), the *only* valid way to judge a runtime-policy
candidate is to replay the same historical tasks under both policies and compare
a fixed set of metrics (Finding F1, High-risk Recall, False Positive, Cost,
Latency, Failure Rate, Tool Calls, Agent Steps).
"""
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .objective import EvolutionMetrics, evolution_utility

# A runner executes one policy across the held-out tasks and returns metrics.
PolicyRunner = Callable[[object], EvolutionMetrics]


@dataclass
class ReplayComparison:
    """Side-by-side metrics for baseline and candidate after replay."""

    baseline: EvolutionMetrics
    candidate: EvolutionMetrics
    baseline_utility: float = 0.0
    utility: float = 0.0
    improvement: float = 0.0
    deltas: List[str] = field(default_factory=list)

    def is_improvement(self, min_improvement: float = 0.0) -> bool:
        """True when the candidate beats the baseline by ``min_improvement``."""
        return self.improvement >= min_improvement

    def summary(self, min_improvement: float = 0.0) -> str:
        accepted = "PROMOTE" if self.is_improvement(min_improvement) else "REJECT"
        lines = [
            f"{accepted}: baseline_utility={self.baseline_utility:.4f} "
            f"utility={self.utility:.4f} improvement={self.improvement:+.4f}",
            f"candidate quality {self.baseline.quality_score:.3f} "
            f"-> {self.candidate.quality_score:.3f}",
            f"candidate recall {self.baseline.high_risk_recall:.3f} "
            f"-> {self.candidate.high_risk_recall:.3f}",
            f"candidate cost {self.baseline.cost:.3f} "
            f"-> {self.candidate.cost:.3f}",
        ]
        return "\n".join(lines)


class PolicyReplayEvaluator:
    """Runs baseline + candidate over the same tasks via an injected runner."""

    def __init__(self, runner: PolicyRunner):
        self._runner = runner

    def evaluate(
        self,
        baseline: object,
        candidate: object,
        weights: Optional[Dict[str, float]] = None,
        min_improvement: float = 0.0,
    ) -> ReplayComparison:
        """Replay both policies and produce a comparison."""
        base_metrics = self._runner(baseline)
        cand_metrics = self._runner(candidate)

        baseline_utility = evolution_utility(
            base_metrics, weights=weights, reference=base_metrics)
        candidate_utility = evolution_utility(
            cand_metrics, weights=weights, reference=base_metrics)
        improvement = candidate_utility - baseline_utility

        deltas = _metric_deltas(base_metrics, cand_metrics)
        return ReplayComparison(
            baseline=base_metrics,
            candidate=cand_metrics,
            baseline_utility=round(baseline_utility, 4),
            utility=round(candidate_utility, 4),
            improvement=round(improvement, 4),
            deltas=deltas,
        )


def _metric_deltas(baseline: EvolutionMetrics, candidate: EvolutionMetrics) -> List[str]:
    rows = [
        ("quality", baseline.quality_score, candidate.quality_score),
        ("high_risk_recall", baseline.high_risk_recall, candidate.high_risk_recall),
        ("failure_rate", baseline.failure_rate, candidate.failure_rate),
        ("cost", baseline.cost, candidate.cost),
        ("latency", baseline.latency, candidate.latency),
        ("tool_calls", baseline.tool_calls, candidate.tool_calls),
        ("agent_steps", baseline.agent_steps, candidate.agent_steps),
    ]
    return [f"{name}: {b:.3f} -> {c:.3f}" for name, b, c in rows]