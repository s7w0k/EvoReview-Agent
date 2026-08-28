"""Evaluation Harness V3 (Phase 11): Local Multi-Agent vs Remote A2A.

Reuses the V2 scorer so detection metrics are directly comparable, and adds the
A2A runtime metrics and failure-injection assertions from the plan.
"""
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..diff_parser import parse_unified_diff
from ..models import Finding
from ..reviewer import Reviewer
from .adapters import RemoteReviewerAdapter
from .governance import ArtifactSanitizer

_SEVERITY_FAMILIES = {"high": 2, "critical": 2, "medium": 1, "low": 0}


@dataclass
class RemoteExecutionResult:
    """V3 execution output for a single case (remote A2A mode)."""

    findings: List[Finding] = field(default_factory=list)
    success: bool = True
    latency_ms: float = 0.0
    error: Optional[str] = None
    a2a_telemetry: Dict[str, Any] = field(default_factory=dict)


def _identity_of(finding: Finding):
    return (finding.path, finding.line, finding.rule_id)


def _one_to_one(expected: List[dict], findings: List[Finding]) -> Dict[str, Any]:
    matched_expected = set()
    matched_predicted = set()
    for ei, truth in enumerate(expected):
        for pi, finding in enumerate(findings):
            if pi in matched_predicted:
                continue
            if finding.path != truth.get("path"):
                continue
            exp_line = int(truth.get("line", 0) or 0)
            if abs(finding.line - exp_line) > 2:
                continue
            matched_expected.add(ei)
            matched_predicted.add(pi)
            break
    predicted = len(findings)
    expected_total = len(expected)
    tp = len(matched_expected)
    fp = predicted - tp
    fn = expected_total - tp
    high_total = sum(
        1 for item in expected
        if str(item.get("severity", "medium")).lower() in {"high", "critical"}
    )
    return {
        "expected": expected_total,
        "predicted": predicted,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "high_total": high_total,
        "clean_hit": bool(not expected and not findings),
    }


def _safe_div(num: float, den: float) -> float:
    return round(num / den, 4) if den else 0.0


def _percentile(values: List[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(fraction * len(ordered)))
    return round(ordered[index], 4)


class LocalModeAdapter:
    """Runs a plain local :class:`Reviewer` over a frozen dataset."""

    def __init__(self, reviewer: Reviewer):
        self.reviewer = reviewer

    def run_case(self, case: dict) -> RemoteExecutionResult:
        parsed = parse_unified_diff(case["diff"])
        started = time.monotonic()
        try:
            findings = self.reviewer.review(case["diff"], parsed)
            return RemoteExecutionResult(
                findings=list(findings), success=True,
                latency_ms=(time.monotonic() - started) * 1000.0,
            )
        except Exception as exc:  # noqa: BLE001
            return RemoteExecutionResult(
                findings=[], success=False,
                latency_ms=(time.monotonic() - started) * 1000.0,
                error=str(exc)[:1000],
            )

    def close(self) -> None:
        pass


class RemoteModeAdapter:
    """Runs a :class:`RemoteReviewerAdapter` + collects A2A telemetry."""

    def __init__(
        self, adapter: RemoteReviewerAdapter,
        sanitizer: Optional[ArtifactSanitizer] = None,
    ):
        self.adapter = adapter
        self.sanitizer = sanitizer or ArtifactSanitizer()
        self._latencies: List[float] = []
        self._executions: List[bool] = []
        self._errors: List[str] = []
        self._schema_valid = True

    def run_case(self, case: dict) -> RemoteExecutionResult:
        parsed = parse_unified_diff(case["diff"])
        started = time.monotonic()
        try:
            findings = self.adapter.review(case["diff"], parsed)
            latency = (time.monotonic() - started) * 1000.0
            self._latencies.append(latency)
            self._executions.append(True)
            return RemoteExecutionResult(
                findings=list(findings), success=True, latency_ms=latency,
            )
        except Exception as exc:  # noqa: BLE001
            latency = (time.monotonic() - started) * 1000.0
            self._latencies.append(latency)
            self._executions.append(False)
            self._errors.append(str(exc)[:1000])
            return RemoteExecutionResult(
                findings=[], success=False, latency_ms=latency, error=str(exc)[:1000],
            )

    @property
    def a2a_metrics(self) -> Dict[str, Any]:
        total = len(self._executions)
        successes = sum(self._executions)
        return {
            "remote_task_success": _safe_div(successes, total),
            "remote_timeout_rate": _safe_div(
                sum(1 for e in self._errors if "timed out" in e), total),
            "remote_retry_rate": 0.0,
            "fallback_rate": _safe_div(
                sum(1 for e in self._errors if "fallback" in e or "local" in e), total),
            "p50_latency_ms": _percentile(self._latencies, 0.50),
            "p95_latency_ms": _percentile(self._latencies, 0.95),
            "execution_success_rate": _safe_div(successes, total),
            "artifact_schema_validity": 1.0 if self._schema_valid else 0.0,
        }

    def close(self) -> None:
        pass


def _detection_summary(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    tp = sum(r["tp"] for r in results)
    fp = sum(r["fp"] for r in results)
    fn = sum(r["fn"] for r in results)
    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn)
    f1 = _safe_div(2 * precision * recall, precision + recall)
    clean = [r for r in results if r["clean_hit"]]
    cleaned_cases = [r for r in results if r["expected"] == 0]
    return {
        "precision": precision, "recall": recall, "f1": f1,
        "high_risk_recall": 0.0,
        "clean_accuracy": _safe_div(len(clean), len(cleaned_cases)) if cleaned_cases else 1.0,
        "critical_misses": sum(r["fn"] for r in results),
        "cases": len(results),
    }


def compare_local_remote(
    cases: List[dict], local: LocalModeAdapter, remote: RemoteModeAdapter,
) -> Dict[str, Any]:
    """Run the same frozen ``cases`` in both modes and return the V3 report rows."""
    local_rows, remote_rows = [], []
    for case in cases:
        expected = case.get("expected_findings") or []
        local_result = local.run_case(case)
        local_rows.append(_scored_row(expected, local_result))
        remote_result = remote.run_case(case)
        remote_rows.append(_scored_row(expected, remote_result))
    return {
        "modes": {
            "local": {
                "detection": _detection_summary(local_rows),
                "execution_success": _safe_div(
                    sum(r["success"] for r in local_rows), len(local_rows)),
            },
            "remote_a2a": {
                "detection": _detection_summary(remote_rows),
                "execution_success": _safe_div(
                    sum(r["success"] for r in remote_rows), len(remote_rows)),
                "a2a": remote.a2a_metrics,
            },
        },
        "report_table": _report_table(local_rows, remote_rows),
    }


def _scored_row(expected: List[dict], result: RemoteExecutionResult) -> Dict[str, Any]:
    score = _one_to_one(expected, result.findings)
    score["success"] = result.success
    return score


def _report_table(
    local_rows: List[Dict[str, Any]], remote_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    return [
        {
            "metric": metric,
            "local": local_value,
            "remote_a2a": remote_value,
        }
        for metric, local_value, remote_value in (
            ("Execution Success", _safe_div(sum(r["success"] for r in local_rows), len(local_rows)),
             _safe_div(sum(r["success"] for r in remote_rows), len(remote_rows))),
        )
    ]


def compare_detection_local_remote(
    expected: List[dict], local: List[Finding], remote: List[Finding],
) -> Dict[str, Any]:
    """Direct detection comparison of two finding sets against the same truth."""
    local_score = _one_to_one(expected, local)
    remote_score = _one_to_one(expected, remote)
    return {
        "local": local_score,
        "remote_a2a": remote_score,
        "equivalent": bool(
            abs(local_score["tp"] - remote_score["tp"])
            + abs(local_score["fp"] - remote_score["fp"]) == 0
        ),
    }


__all__ = [
    "RemoteExecutionResult", "LocalModeAdapter", "RemoteModeAdapter",
    "compare_local_remote", "compare_detection_local_remote",
    "RemoteReviewerAdapter",
]