"""Regression attribution for a failed/poor evolution candidate.

Even when overall quality improves, a candidate may regress in one slice
(a language, repository, rule, severity, or procedure).  Per the plan
(section 10.5): a segment regression below a threshold must gate-reject the
change even if the global F1 went up.
"""
from dataclasses import dataclass, field
from typing import Dict, List

# Dimensions along which a regression can be attributed.
REGRESSION_DIMENSIONS = ("language", "repository", "rule", "severity", "procedure")


@dataclass
class RegressionSegment:
    """One slice with its baseline vs candidate metric."""

    dimension: str
    segment: str
    baseline_score: float
    candidate_score: float
    tolerance: float = 0.06

    @property
    def delta(self) -> float:
        return self.candidate_score - self.baseline_score

    @property
    def regressed(self) -> bool:
        return self.delta < -self.tolerance


@dataclass
class RegressionAttribution:
    """Result of locating regressions across segments."""

    regression_segments: List[RegressionSegment] = field(default_factory=list)

    @property
    def regressed(self) -> bool:
        return any(segment.regressed for segment in self.regression_segments)

    def offending(self) -> List[RegressionSegment]:
        return [segment for segment in self.regression_segments if segment.regressed]

    def summary(self) -> str:
        if not self.regression_segments:
            return "no segment regression"
        return "; ".join(
            f"{s.dimension}/{s.segment}: {s.delta:+.4f}" for s in self.offending())


class RegressionLocator:
    """Compares per-segment metrics and flags slice-level regressions."""

    def __init__(self, tolerance: float = 0.06):
        self._tolerance = tolerance

    def locate(
        self,
        baseline_by_segment: Dict[str, Dict[str, float]],
        candidate_by_segment: Dict[str, Dict[str, float]],
    ) -> RegressionAttribution:
        """``baseline_by_segment`` maps dimension -> {segment: score}."""
        segments: List[RegressionSegment] = []
        for dimension, baseline_map in baseline_by_segment.items():
            candidate_map = candidate_by_segment.get(dimension, {})
            for segment, baseline_score in baseline_map.items():
                candidate_score = candidate_map.get(segment, baseline_score)
                segments.append(RegressionSegment(
                    dimension=dimension,
                    segment=segment,
                    baseline_score=baseline_score,
                    candidate_score=candidate_score,
                    tolerance=self._tolerance,
                ))
        return RegressionAttribution(regression_segments=segments)