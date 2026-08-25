"""Compare baseline and candidate replay metrics and decide."""

METRIC_SIGN = {
    "finding_precision": +1, "finding_recall": +1, "finding_f1": +1,
    "high_risk_recall": +1, "verification_pass_rate": +1,
    "tool_calls": -1, "agent_steps": -1, "token_usage": -1, "estimated_cost": -1,
    "latency_ms": -1, "failure_rate": -1, "recovery_count": -1,
}


class ReplayComparator:
    """Compute baseline vs candidate deltas and a binary decision."""

    def __init__(self, min_quality_improvement: float = 0.01,
                 max_regression: float = 0.0,
                 high_risk_recall_regression: float = -0.01):
        self.min_quality_improvement = min_quality_improvement
        self.max_regression = max_regression
        self.high_risk_recall_regression = high_risk_recall_regression

    def compare(self, baseline: dict, candidate: dict) -> dict:
        keys = sorted(set(baseline) | set(candidate))
        deltas = {}
        passes = {}
        for key in keys:
            b = baseline.get(key, 0.0)
            c = candidate.get(key, 0.0)
            if isinstance(b, (int, float)) and isinstance(c, (int, float)):
                delta = round(c - b, 6)
                deltas[key] = delta
                passes[key] = self._metric_pass(key, delta)
            else:
                deltas[key] = None
                passes[key] = True
        return {"deltas": deltas, "passes": passes}

    def _metric_pass(self, key: str, delta: float) -> bool:
        sign = METRIC_SIGN.get(key, 0)
        if sign == 0:
            return True
        if sign > 0:
            # Improvement metric: must not regress beyond tolerance.
            return delta >= -self.max_regression
        # Cost / latency / steps: must not regress beyond tolerance.
        return delta <= self.max_regression

    def decide(self, baseline: dict, candidate: dict) -> dict:
        report = self.compare(baseline, candidate)
        quality_sufficient = candidate.get("finding_f1", 0.0) >= (
            baseline.get("finding_f1", 0.0) + self.min_quality_improvement)
        hard_safety = (
            candidate.get("high_risk_recall", 1.0) + self.high_risk_recall_regression
            >= baseline.get("high_risk_recall", 1.0)
        )
        passes = report["passes"]
        passed = bool(passes) and all(v for v in passes.values()) and hard_safety
        reason = "PASS" if (passed and quality_sufficient) else "FAIL"
        if not quality_sufficient:
            reason = "FAIL: quality improvement below threshold"
        elif not hard_safety:
            reason = "FAIL: high-risk recall regressed beyond safety bound"
        elif not passed:
            reason = "FAIL: one or more protected metrics regressed"
        return {
            "decision": "PASS" if passed and quality_sufficient else "FAIL",
            "reason": reason,
            "high_risk_recall_safe": hard_safety,
            "quality_sufficient": quality_sufficient,
            "deltas": report["deltas"],
        }