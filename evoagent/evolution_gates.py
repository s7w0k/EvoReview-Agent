"""Closed-loop WP3: dataset stratification and quality gates.

Provides deterministic helpers for the "real data, generalization and
catastrophic-forgetting" gate family:

- :func:`stratified_metrics` computes per-language / per-repository / per-rule /
  per-severity / per-change-size / per-capability F1 from a replay's per-case
  results (append-only, no change to the existing overall metrics);
- :func:`forgetting_gate` rejects candidates that drop protected core recall or
  regress a language beyond the configured tolerance;
- :func:`generalization_gate` rejects candidates that only improve overall
  while degrading cross-repo / temporal holdout / key strata;
- :func:`production_source_gate` rejects synthetic data from satisfying the
  production activation requirement;
- :func:`detect_dataset_leakage` finds same-repo / same-diff / derived samples
  that cross validation/holdout partitions.

All gates return ``{"passed": bool, "reasons": [...]}`` so callers can record
them (shadow mode) or enforce them (production mode).
"""
import hashlib
from typing import Any, Dict, List, Optional

DEFAULT_THRESHOLDS = {
    "validation_f1_improvement": 0.02,
    "precision_regression_max": 0.005,
    "language_f1_regression_max": 0.02,
    "golden_critical_recall_min": 1.0,
    "cross_repo_f1_regression_max": 0.0,
    "temporal_holdout_f1_regression_max": 0.0,
    "repair_correctness_regression_max": 0.0,
}

GOLDEN_REGRESSION = "golden-regression"
CROSS_REPO_TRANSFER = "cross-repo-transfer"
TEMPORAL_HOLDOUT = "temporal-holdout"

SHADOW_DEFAULT_THRESHOLDS = {
    "min_samples": 100,
    "min_labeled": 30,
    "min_success_rate": 0.99,
    "high_risk_missed_max": 0,
    "fp_rate_budget_pp": 0.01,
    "p95_latency_growth_max": 0.20,
    "cost_growth_max": 0.15,
}


def _severity_bucket(expected: List[dict]) -> List[str]:
    if not expected:
        return ["clean"]
    buckets = set()
    for item in expected:
        severity = str(item.get("min_severity", "low")).lower()
        if severity in {"high", "critical"}:
            buckets.add("high")
        else:
            buckets.add(severity)
    return sorted(buckets)


def _change_size(diff: str) -> str:
    size = len((diff or "").encode("utf-8"))
    if size < 2000:
        return "small"
    if size < 20000:
        return "medium"
    return "large"


def _case_dimensions(case: Dict[str, Any]) -> Dict[str, Any]:
    expected = list(case.get("expected", []) or [])
    rule_ids = sorted({
        str(item.get("rule_id", "")).strip() or "unlabeled" for item in expected
    })
    return {
        "language": case.get("language") or "unknown",
        "repository": case.get("repository") or "unknown",
        "suite": case.get("suite_id") or case.get("split") or "unknown",
        "rule": rule_ids,
        "severity": _severity_bucket(expected),
        "change_size": _change_size(case.get("diff", "")),
        "capability": "pre-candidate" if case.get("created_before_candidate") else "post-candidate",
    }


def _group_metrics(group: Dict[str, int]) -> Dict[str, Any]:
    tp = group["tp"]
    fp = group["fp"]
    fn = group["fn"]
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "tp": tp, "fp": fp, "fn": fn, "n": group["n"],
        "precision": round(precision, 4), "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def stratified_metrics(cases: List[dict], case_results: List[dict]) -> Dict[str, Any]:
    """Aggregate per-case replay results into per-dimension F1 buckets."""
    groups: Dict[str, Dict[str, Dict[str, int]]] = {}
    for case, result in zip(cases, case_results or []):
        dimensions = _case_dimensions(case)
        tp = int(result.get("tp", 0))
        fp = int(result.get("fp", 0))
        fn = int(result.get("fn", 0))
        for dim_name, raw_values in dimensions.items():
            values = raw_values if isinstance(raw_values, list) else [raw_values]
            for value in values:
                key = str(value)
                bucket = groups.setdefault(dim_name, {}).setdefault(
                    key, {"tp": 0, "fp": 0, "fn": 0, "n": 0})
                bucket["tp"] += tp
                bucket["fp"] += fp
                bucket["fn"] += fn
                bucket["n"] += 1
    return {
        dim_name: {
            value: _group_metrics(bucket)
            for value, bucket in sorted(values.items())
        }
        for dim_name, values in groups.items()
    }


def _stratum(stratified: Dict[str, Any], dimension: str, key: str) -> Optional[Dict[str, Any]]:
    return (stratified or {}).get(dimension, {}).get(key)


def _thresholds(overrides: Optional[Dict[str, float]]) -> Dict[str, float]:
    merged = dict(DEFAULT_THRESHOLDS)
    if overrides:
        merged.update(overrides)
    return merged


def forgetting_gate(
    baseline: Dict[str, Any], candidate: Dict[str, Any],
    thresholds: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Reject a candidate that drops protected core recall or regresses a language.

    ``baseline``/``candidate`` are full replay metrics including the append-only
    ``stratified`` key and the ``fix_correctness`` signal.
    """
    thresholds = _thresholds(thresholds)
    base_strata = baseline.get("stratified", {})
    cand_strata = candidate.get("stratified", {})
    reasons: List[str] = []
    checks: Dict[str, Any] = {}

    golden = _stratum(cand_strata, "suite", GOLDEN_REGRESSION)
    checks["golden_critical_recall"] = golden["recall"] if golden else None
    if golden is not None and golden["recall"] < thresholds["golden_critical_recall_min"]:
        reasons.append("golden critical recall dropped below 100%%")

    for rule_id, base in (base_strata.get("rule", {}) or {}).items():
        cand = cand_strata.get("rule", {}).get(rule_id)
        checks.setdefault("core_rule_recall", {})[rule_id] = cand["recall"] if cand else None
        if cand is not None and base.get("recall", 0.0) > 0 and cand["recall"] < base["recall"]:
            reasons.append("core rule %s recall regressed" % rule_id)

    for language, base in (base_strata.get("language", {}) or {}).items():
        cand = cand_strata.get("language", {}).get(language)
        checks.setdefault("language_f1", {})[language] = cand["f1"] if cand else None
        if cand is not None and cand["f1"] + thresholds["language_f1_regression_max"] < base["f1"]:
            reasons.append("language %s F1 regressed" % language)

    base_fix = baseline.get("fix_correctness")
    cand_fix = candidate.get("fix_correctness")
    checks["repair_correctness"] = {"baseline": base_fix, "candidate": cand_fix}
    if (
        base_fix is not None and cand_fix is not None
        and cand_fix + thresholds["repair_correctness_regression_max"] < base_fix
    ):
        reasons.append("repair correctness regressed")

    return {"passed": not reasons, "reasons": reasons, "checks": checks}


def generalization_gate(
    baseline: Dict[str, Any], candidate: Dict[str, Any],
    thresholds: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Reject a candidate that improves overall while degrading held-out strata."""
    thresholds = _thresholds(thresholds)
    base_strata = baseline.get("stratified", {})
    cand_strata = candidate.get("stratified", {})
    reasons: List[str] = []
    checks: Dict[str, Any] = {}

    cross_repo = _stratum(cand_strata, "suite", CROSS_REPO_TRANSFER)
    cross_repo_base = _stratum(base_strata, "suite", CROSS_REPO_TRANSFER)
    checks["cross_repo_f1"] = {"baseline": cross_repo_base["f1"] if cross_repo_base else None,
                               "candidate": cross_repo["f1"] if cross_repo else None}
    if (
        cross_repo is not None and cross_repo_base is not None
        and cross_repo["f1"] + thresholds["cross_repo_f1_regression_max"] < cross_repo_base["f1"]
    ):
        reasons.append("cross-repo F1 regressed")

    temporal = _stratum(cand_strata, "suite", TEMPORAL_HOLDOUT)
    temporal_base = _stratum(base_strata, "suite", TEMPORAL_HOLDOUT)
    checks["temporal_holdout_f1"] = {"baseline": temporal_base["f1"] if temporal_base else None,
                                     "candidate": temporal["f1"] if temporal else None}
    if (
        temporal is not None and temporal_base is not None
        and temporal["f1"] + thresholds["temporal_holdout_f1_regression_max"] < temporal_base["f1"]
    ):
        reasons.append("temporal holdout F1 regressed")

    # Any key stratum present in the baseline must not regress more than the
    # language tolerance (prevents overall gains masking a critical-domain drop).
    for dimension, values in (base_strata or {}).items():
        for key, base in values.items():
            cand = (cand_strata.get(dimension, {}) or {}).get(key)
            if cand is not None and cand["f1"] + thresholds["language_f1_regression_max"] < base["f1"]:
                reasons.append("%s/%s degraded while overall improved" % (dimension, key))

    return {"passed": not reasons, "reasons": reasons, "checks": checks}


def production_source_gate(
    cases: List[dict], *, min_real_samples: int = 1,
) -> Dict[str, Any]:
    """Synthetic/manual data cannot satisfy the production activation gate."""
    real = 0
    for case in cases:
        if case.get("source") == "github-real" and case.get("source_uri"):
            real += 1
    return {
        "passed": real >= max(1, min_real_samples),
        "reasons": [] if real >= max(1, min_real_samples)
        else ["no production-sourced evaluation samples (synthetic data only)"],
        "checks": {"real_samples": real, "min_real_samples": max(1, min_real_samples)},
    }


def shadow_gate(
    observations: List[dict],
    *,
    thresholds: Optional[Dict[str, float]] = None,
    baseline: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Evaluate the shadow -> canary promotion gate over collected observations.

    ``observations`` are decoded ``release_observations`` rows (candidate side).
    ``baseline`` optionally carries the stable side's ``fp_rate``, ``accept_rate``,
    ``p95_latency_ms`` and ``avg_cost`` for comparative gates.
    """
    merged = dict(SHADOW_DEFAULT_THRESHOLDS)
    if thresholds:
        merged.update(thresholds)
    thresholds = merged
    baseline = baseline or {}
    total = len(observations)
    labeled = [o for o in observations if o.get("feedback_category") or o.get("human_label")]
    failures = sum(1 for o in observations if o.get("candidate_failed"))
    success_rate = 1 - failures / total if total else 0.0
    fp = sum(1 for o in labeled if o.get("feedback_category") == "false_positive")
    accepted = sum(1 for o in labeled if o.get("accepted"))
    fp_rate = fp / len(labeled) if labeled else 0.0
    accept_rate = accepted / len(labeled) if labeled else 0.0
    high_risk_missed = sum(
        int((o.get("metrics") or {}).get("high_risk_missed", 0)) for o in observations
    )
    latencies = sorted(
        o.get("latency_ms") for o in observations if o.get("latency_ms") is not None
    )
    p95 = latencies[int(len(latencies) * 0.95)] if latencies else None
    costs = [o.get("cost_estimate") for o in observations if o.get("cost_estimate") is not None]
    avg_cost = sum(costs) / len(costs) if costs else None

    reasons: List[str] = []
    checks: Dict[str, Any] = {
        "samples": total, "labeled": len(labeled), "success_rate": round(success_rate, 4),
        "high_risk_missed": high_risk_missed, "fp_rate": round(fp_rate, 4),
        "accept_rate": round(accept_rate, 4), "p95_latency_ms": p95,
        "avg_cost": avg_cost,
    }
    if total < thresholds["min_samples"]:
        reasons.append("insufficient shadow samples")
    if len(labeled) < thresholds["min_labeled"]:
        reasons.append("insufficient labeled shadow samples")
    if success_rate < thresholds["min_success_rate"]:
        reasons.append("candidate execution success rate too low")
    if high_risk_missed > thresholds["high_risk_missed_max"]:
        reasons.append("high-risk miss detected in shadow")
    if baseline.get("fp_rate") is not None and fp_rate > baseline["fp_rate"] + thresholds["fp_rate_budget_pp"]:
        reasons.append("candidate false-positive rate exceeds stable budget")
    if baseline.get("accept_rate") is not None and accept_rate < baseline["accept_rate"]:
        reasons.append("candidate human accept rate below stable")
    if baseline.get("p95_latency_ms") and p95 is not None:
        growth = (p95 - baseline["p95_latency_ms"]) / baseline["p95_latency_ms"]
        if growth > thresholds["p95_latency_growth_max"]:
            reasons.append("candidate p95 latency growth too high")
    if baseline.get("avg_cost") and avg_cost is not None:
        growth = (avg_cost - baseline["avg_cost"]) / baseline["avg_cost"]
        if growth > thresholds["cost_growth_max"]:
            reasons.append("candidate cost growth too high")

    return {"passed": not reasons, "reasons": reasons, "checks": checks}


def detect_dataset_leakage(cases: List[dict]) -> List[Dict[str, Any]]:
    """Return leakage issues where a repo/diff/derived sample crosses partitions."""
    by_split = {}
    for case in cases:
        by_split.setdefault(case.get("split", ""), []).append(case)

    issues: List[Dict[str, Any]] = []
    splits = sorted(by_split)

    for split in splits:
        repos = {case.get("repository") for case in by_split[split] if case.get("repository")}
        for other in splits:
            if other <= split:
                continue
            overlap = repos & {case.get("repository") for case in by_split[other] if case.get("repository")}
            for repo in sorted(overlap):
                issues.append({"type": "same_repository", "repository": repo,
                               "splits": [split, other]})

    # Same diff across splits (exact content leak).
    seen: Dict[str, str] = {}
    for case in cases:
        digest = hashlib.sha256((case.get("diff") or "").encode("utf-8")).hexdigest()
        split = case.get("split", "")
        if digest in seen and seen[digest] != split:
            issues.append({"type": "same_diff", "splits": [seen[digest], split],
                           "name": case.get("name")})
        else:
            seen[digest] = split

    # Derived samples: same repository + expected finding location across splits.
    loc_by_repo: Dict[str, Dict[tuple, str]] = {}
    for case in cases:
        repo = case.get("repository") or ""
        split = case.get("split", "")
        for expected in case.get("expected", []):
            loc = (str(expected.get("path", "")), int(expected.get("line", 0)))
            if repo not in loc_by_repo:
                loc_by_repo[repo] = {}
            if loc in loc_by_repo[repo] and loc_by_repo[repo][loc] != split:
                issues.append({"type": "derived_sample", "repository": repo,
                               "splits": [loc_by_repo[repo][loc], split], "location": loc})
            else:
                loc_by_repo[repo][loc] = split

    # De-duplicate identical issues while preserving order.
    unique = []
    seen_keys = set()
    for issue in issues:
        key = (issue["type"], json_safe(issue))
        if key not in seen_keys:
            seen_keys.add(key)
            unique.append(issue)
    return unique


def json_safe(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
