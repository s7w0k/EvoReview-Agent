"""Multi-objective scoring for runtime-policy evolution.

A policy candidate is judged on several axes, not just finding quality.  The
plan's objective blends Quality, Cost, Latency, Reliability and Safety into one
utility score, while *hard safety gates* always take precedence (see
``policy_evolution.gate``).
"""
from dataclasses import dataclass
from typing import Dict, Optional

# Default utility weights (see plan section 9.4).  Cost / latency are penalties.
DEFAULT_WEIGHTS: Dict[str, float] = {
    "quality": 0.40,          # finding F1 / overall quality
    "high_risk_recall": 0.20,  # recall on high / critical risk findings
    "reliability": 0.15,       # how often the policy completes without failure
    "cost": 0.15,              # penalty weight on normalized cost
    "latency": 0.10,           # penalty weight on normalized latency
}


@dataclass
class EvolutionMetrics:
    """Deterministic summary of a single policy run across tasks."""

    # -- finding-quality (plan section 11.6) --
    quality_score: float = 0.0       # finding F1 (0..1)
    finding_f1: float = 0.0          # explicit alias of quality_score
    high_risk_recall: float = 0.0    # 0..1
    critical_misses: int = 0         # must stay 0 under hard safety
    false_positive_rate: float = 0.0  # fp / (fp + tn), 0..1
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    # -- reliability / throughput --
    task_success_rate: float = 0.0   # succeeded tasks / all tasks, 0..1
    failure_rate: float = 0.0        # 0..1
    reliability_score: float = 1.0   # 0..1 (1 = always completes)
    recovery_success_rate: float = 1.0  # successful recoveries / attempts, 0..1
    # -- cost / resource --
    cost: float = 0.0                # relative cost units
    latency: float = 0.0             # relative latency units
    tool_calls: int = 0
    agent_steps: int = 0
    # -- safety governance --
    policy_violations: int = 0
    side_effect_safety_incidents: int = 0

    @classmethod
    def from_finding_counts(cls, tp, fp, fn, high_risk_recall=0.0,
                            critical_misses=0, **extra) -> "EvolutionMetrics":
        """Build metrics from the raw confusion-matrix counts."""
        denominator = tp + fn
        quality = (tp / denominator) if denominator else 0.0
        precision_denominator = tp + fp
        precision = (tp / precision_denominator) if precision_denominator else 0.0
        recall = quality
        if precision + recall > 0:
            f1 = 2 * precision * recall / (precision + recall)
        else:
            f1 = 0.0
        false_positive_rate = _fpr(fp, tn=extra.pop("true_negatives", 0),
                                   tp=tp)
        return cls(
            quality_score=round(f1, 4),
            finding_f1=round(f1, 4),
            high_risk_recall=high_risk_recall,
            critical_misses=critical_misses,
            false_positive_rate=false_positive_rate,
            true_positives=tp,
            false_positives=fp,
            false_negatives=fn,
            **extra,
        )


def _fpr(fp, *, tn, tp):
    denominator = fp + tn
    return (fp / denominator) if denominator else (0.0 if fp == 0 else 1.0)


def evolution_utility(
    metrics: EvolutionMetrics,
    weights: Optional[Dict[str, float]] = None,
    reference: Optional[EvolutionMetrics] = None,
) -> float:
    """Compute the blended utility for ``metrics``.

    When ``reference`` is provided, cost / latency are normalised into 0..1
    relative to that baseline (bounded via a logistic-style clamp) so the three
    linear terms are comparable.  The returned value is unitless; higher-better.
    """
    w = DEFAULT_WEIGHTS if weights is None else weights
    score = w.get("quality", 0.40) * _clamp01(metrics.quality_score)
    score += w.get("high_risk_recall", 0.20) * _clamp01(metrics.high_risk_recall)
    score += w.get("reliability", 0.15) * _clamp01(metrics.reliability_score)
    score -= w.get("cost", 0.15) * _norm_ratio(metrics.cost, reference.cost if reference else None)
    score -= w.get("latency", 0.10) * _norm_ratio(metrics.latency,
                                                  reference.latency if reference else None)
    return round(max(0.0, score), 4)


def _norm_ratio(value: float, ref: Optional[float]) -> float:
    ref = ref if (ref is not None and ref > 0) else 1.0
    return _clamp01(value / ref)


def _clamp01(value: float) -> float:
    return 0.0 if value < 0 else (1.0 if value > 1 else value)