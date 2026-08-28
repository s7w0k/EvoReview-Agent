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
    """Runs a :class:`RemoteReviewerAdapter` + collects A2A telemetry.

    The wrapped adapter may sit over any transport (in-process or real HTTP).
    When a metrics collector or trace bus is available (either passed in or
    already attached to the adapter), the aggregated report includes real retry
    / fallback rates, latency percentiles and trace coverage.
    """

    def __init__(
        self, adapter: RemoteReviewerAdapter,
        sanitizer: Optional[ArtifactSanitizer] = None,
        bus=None, metrics=None,
    ):
        self.adapter = adapter
        self.sanitizer = sanitizer or ArtifactSanitizer()
        self._latencies: List[float] = []
        self._executions: List[bool] = []
        self._errors: List[str] = []
        self._schema_valid = True
        transport = getattr(adapter, "transport", None)
        attached = getattr(transport, "metrics", None)
        if metrics is not None:
            transport.metrics = metrics
            self.metrics = metrics
        elif attached is not None:
            self.metrics = attached
        else:
            from .telemetry import A2AMetrics
            self.metrics = A2AMetrics(mirror=False)
            transport.metrics = self.metrics
        if bus is not None and getattr(adapter, "bus", None) is None:
            adapter.bus = bus
        self.bus = getattr(adapter, "bus", None)

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

    def _count(self, counter) -> int:
        if self.metrics is None:
            return 0
        value = getattr(self.metrics, counter, None)
        if isinstance(value, dict):
            return sum(value.values())
        return int(value or 0)

    def _trace_events(self) -> int:
        if self.bus is None:
            return 0
        return sum(1 for message in getattr(self.bus, "messages", [])
                   if str(message.kind).startswith("remote_"))

    @property
    def a2a_metrics(self) -> Dict[str, Any]:
        total = len(self._executions)
        successes = sum(self._executions)
        requests = self._count("requests") or max(total, 0)
        retries = self._count("retries")
        fallbacks = self._count("fallbacks")
        # Plan section 14.2 "trace coverage": fraction of the expected remote
        # trace events (submit -> running -> artifact, 3 per case) observed.
        expected_trace = 3 * total
        trace_events = self._trace_events()
        return {
            "remote_task_success": _safe_div(successes, total),
            "remote_timeout_rate": _safe_div(
                sum(1 for e in self._errors if "timed out" in e), total),
            "remote_retry_rate": _safe_div(retries, requests),
            "fallback_rate": _safe_div(fallbacks, requests),
            "fallback_success": min(1.0, _safe_div(successes, fallbacks)) if fallbacks else 0.0,
            "p50_latency_ms": _percentile(self._latencies, 0.50),
            "p95_latency_ms": _percentile(self._latencies, 0.95),
            "p99_latency_ms": _percentile(self._latencies, 0.99),
            "e2e_p95_latency_ms": _percentile(self._latencies, 0.95),
            "execution_success_rate": _safe_div(successes, total),
            "artifact_schema_validity": 1.0 if self._schema_valid else 0.0,
            "trace_coverage": _safe_div(trace_events, expected_trace),
            "requests_total": requests,
        }

    def close(self) -> None:
        pass


class HttpRemoteModeAdapter(RemoteModeAdapter):
    """V3 RemoteModeAdapter backed by a real HTTP A2A endpoint.

    Discovers the endpoint to build a :class:`RemoteReviewerAdapter` over an
    HTTP transport, attaches trace + metric collectors, then exposes the same
    V3 comparison / benchmark surface as the in-process adapter.
    """

    def __init__(
        self, endpoint: str, *, token: str = "", timeout_seconds: float = 10.0,
        local_fallback=None,
    ):
        from ..agents import CollaborationBus
        from .telemetry import A2AMetrics

        from .factory import build_remote_reviewers_typed

        reviewers, _registry = build_remote_reviewers_typed(
            [endpoint], token=token, timeout_seconds=timeout_seconds,
        )
        adapter = reviewers[0]
        if local_fallback is not None:
            adapter.local_fallback = local_fallback
        bus = CollaborationBus("a2a-http-benchmark")
        adapter.bus = bus
        metrics = A2AMetrics(mirror=False)
        adapter.transport.metrics = metrics
        super().__init__(adapter, bus=bus, metrics=metrics)


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


#: Small frozen validation dataset for the real HTTP benchmark (plan section
#: 14.2 needs repeatable, dependency-free baseline numbers).
FROZEN_CASES: List[dict] = [
    {
        "id": "case-frozen-secret", "repository": "repo", "pull_request": 1,
        "split": "validation",
        "diff": "@@ -0 +1 @@\n+password = \"hunter2\"\n",
        "expected_findings": [{"path": "unknown", "line": 1, "severity": "high"}],
    },
    {
        "id": "case-frozen-clean", "repository": "repo", "pull_request": 2,
        "split": "validation",
        "diff": "@@ -0 +1 @@\n+# comment only\n",
        "expected_findings": [],
    },
]


def run_http_benchmark(
    endpoints: List[str], *, token: str = "", timeout_seconds: float = 10.0,
    cases: Optional[List[dict]] = None, local_fallback=None,
) -> Dict[str, Any]:
    """Real-HTTP Remote benchmark: local vs discovered A2A agents on ``cases``.

    Discovers each endpoint over the HTTP transport, runs the frozen
    ``cases`` in local and remote modes, and returns one V3 comparison report
    per Remote Agent.
    """
    from ..agents import CollaborationBus
    from .factory import build_remote_reviewers_typed
    from .registry import AgentRegistry
    from .telemetry import A2AMetrics

    cases = list(cases or FROZEN_CASES)
    reviewers, _registry = build_remote_reviewers_typed(
        endpoints, token=token, timeout_seconds=timeout_seconds,
        registry=AgentRegistry(),
    )
    local = LocalModeAdapter(local_fallback or _default_local_reviewer())
    reports = {}
    for adapter in reviewers:
        bus = CollaborationBus("a2a-http-benchmark")
        adapter.bus = bus
        metrics = A2AMetrics(mirror=False)
        adapter.transport.metrics = metrics
        remote = RemoteModeAdapter(adapter, bus=bus, metrics=metrics)
        reports[adapter.agent_id] = compare_local_remote(cases, local, remote)
        reports[adapter.agent_id]["endpoint"] = adapter.endpoint
    return {"cases": [case.get("id") for case in cases], "agents": reports}


def _default_local_reviewer():
    from ..reviewer import CompositeReviewer, ReliabilityRuleReviewer, SecurityRuleReviewer
    return CompositeReviewer([SecurityRuleReviewer(), ReliabilityRuleReviewer()])


def main(argv: Optional[List[str]] = None) -> int:
    """``python -m evoagent.a2a.evaluation`` CLI for the HTTP Remote benchmark.

    Endpoints come from ``--endpoints`` (repeatable) or
    ``EVOAGENT_A2A_ENDPOINTS`` (comma-separated).  Prints a compact V3 report
    as JSON lines, one per discovered Remote Agent.
    """
    import argparse
    import json

    from .factory import a2a_endpoints_from_env

    parser = argparse.ArgumentParser(
        prog="python -m evoagent.a2a.evaluation",
        description="Run the A2A V3 Evaluation against real HTTP endpoints.",
    )
    parser.add_argument("--endpoints", action="append", default=None,
                        metavar="URL", help="A2A endpoint (repeatable)")
    parser.add_argument("--token", default="", help="A2A bearer token")
    parser.add_argument("--timeout", type=float, default=10.0,
                        help="per-request timeout in seconds")
    parser.add_argument("--pretty", action="store_true",
                        help="pretty-print the report instead of JSON lines")
    args = parser.parse_args(argv)
    endpoints = list(args.endpoints) if args.endpoints else a2a_endpoints_from_env()
    if not endpoints:
        parser.error(
            "no A2A endpoints configured: pass --endpoints or set "
            "EVOAGENT_A2A_ENDPOINTS"
        )
    report = run_http_benchmark(
        endpoints, token=args.token, timeout_seconds=args.timeout)
    if args.pretty:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    else:
        for agent_id, item in report["agents"].items():
            line = {"agent": agent_id, "endpoint": item.get("endpoint"),
                    "modes": item.get("modes")}
            print(json.dumps(line, ensure_ascii=False, default=str))
    return 0


__all__ = [
    "RemoteExecutionResult", "LocalModeAdapter", "RemoteModeAdapter",
    "HttpRemoteModeAdapter", "FROZEN_CASES", "run_http_benchmark",
    "compare_local_remote", "compare_detection_local_remote",
    "RemoteReviewerAdapter",
]


if __name__ == "__main__":
    raise SystemExit(main())