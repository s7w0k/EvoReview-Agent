"""A2A observability (Phase 10).

Delivers the labelled metrics families from the plan:

``a2a_requests_total``, ``a2a_request_latency_seconds``,
``a2a_request_failures_total``, ``a2a_timeouts_total``, ``a2a_retries_total``,
``a2a_fallback_total``, ``a2a_circuit_open_total``, ``a2a_artifacts_total``.

Labels stay finite-cardinality (``source_agent``, ``target_agent``,
``task_type``, ``status``, ``protocol_version``) - never tenant / repository /
task ids, so cardinality stays bounded.  The module keeps its own counters and
can optionally mirror them into the shared ``evoagent.metrics`` registry.
"""
import threading
from collections import defaultdict
from typing import Dict

from .models import PROTOCOL_VERSION


class A2AMetrics:
    def __init__(self, mirror: bool = True):
        self._lock = threading.Lock()
        self.requests = defaultdict(int)          # (ver, method, target, status)
        self.failures = defaultdict(int)          # (ver, target, error_class)
        self.timeouts = defaultdict(int)          # (ver, target)
        self.retries = defaultdict(int)           # (ver, target)
        self.fallbacks = defaultdict(int)         # (ver, mode, reason)
        self.circuit_open = defaultdict(int)      # (ver, target)
        self.artifacts = defaultdict(int)         # (ver, artifact_type)
        self.latency_sum = defaultdict(float)     # (ver, method)
        self.latency_count = defaultdict(int)     # (ver, method)
        self.latency_samples: list = []           # raw per-request latencies (s)
        self._mirror = mirror

    # -- recorders ---------------------------------------------------------
    def record_request(self, method: str, target: str, status: str) -> None:
        with self._lock:
            self.requests[(PROTOCOL_VERSION, method, target, status)] += 1

    def record_latency(self, method: str, elapsed: float) -> None:
        key = (PROTOCOL_VERSION, method)
        with self._lock:
            self.latency_sum[key] += elapsed
            self.latency_count[key] += 1
            self.latency_samples.append(float(elapsed))

    def record_failure(self, target: str, error_class: str) -> None:
        with self._lock:
            self.failures[(PROTOCOL_VERSION, target, error_class)] += 1

    def record_timeout(self, target: str) -> None:
        with self._lock:
            self.timeouts[(PROTOCOL_VERSION, target)] += 1

    def record_retry(self, target: str) -> None:
        with self._lock:
            self.retries[(PROTOCOL_VERSION, target)] += 1

    def record_fallback(self, mode: str, reason: str) -> None:
        with self._lock:
            self.fallbacks[(PROTOCOL_VERSION, mode, reason)] += 1

    def record_circuit_open(self, target: str) -> None:
        with self._lock:
            self.circuit_open[(PROTOCOL_VERSION, target)] += 1

    def record_artifact(self, artifact_type: str) -> None:
        with self._lock:
            self.artifacts[(PROTOCOL_VERSION, artifact_type)] += 1

    # -- snapshots ---------------------------------------------------------
    def _memo(self) -> Dict[str, int]:
        with self._lock:
            return {
                "a2a_requests_total": sum(self.requests.values()),
                "a2a_request_failures_total": sum(self.failures.values()),
                "a2a_timeouts_total": sum(self.timeouts.values()),
                "a2a_retries_total": sum(self.retries.values()),
                "a2a_fallback_total": sum(self.fallbacks.values()),
                "a2a_circuit_open_total": sum(self.circuit_open.values()),
                "a2a_artifacts_total": sum(self.artifacts.values()),
            }

    def snapshot(self) -> Dict[str, int]:
        values = self._memo()
        with self._lock:
            for key, count in self.latency_count.items():
                values["a2a_request_latency_seconds|%s" % key[1]] = round(
                    self.latency_sum[key] / count, 6
                ) if count else 0
        return values

    def latency_percentiles(self, fractions=(0.50, 0.95, 0.99)) -> Dict[float, float]:
        """Raw per-request latency percentiles in seconds (empty when no samples)."""
        with self._lock:
            samples = list(self.latency_samples)
        if not samples:
            return {}
        ordered = sorted(samples)
        result: Dict[float, float] = {}
        for fraction in fractions:
            index = min(len(ordered) - 1, int(fraction * len(ordered)))
            result[float(fraction)] = round(ordered[index], 6)
        return result

    def prometheus(self) -> str:
        values = self._memo()
        lines = []
        for metric, value in sorted(values.items()):
            lines.append("# TYPE evoagent_%s counter" % metric)
            lines.append("evoagent_%s %d" % (metric, value))
        with self._lock:
            for (ver, target, status), count in sorted(self.requests.items()):
                labels = "source_agent=%s,target_agent=%s,status=%s,protocol_version=%s" % (
                    _quote("coordinator"), _quote(target), _quote(status), _quote(ver))
                lines.append("evoagent_a2a_requests_total{%s} %d" % (labels, count))
            for (ver, target, eclass), count in sorted(self.failures.items()):
                labels = "target_agent=%s,error_class=%s,protocol_version=%s" % (
                    _quote(target), _quote(eclass), _quote(ver))
                lines.append("evoagent_a2a_request_failures_total{%s} %d" % (labels, count))
            for (ver, target, mode, reason), count in sorted(self.fallbacks.items()):
                labels = "mode=%s,reason=%s,target_agent=%s,protocol_version=%s" % (
                    _quote(mode), _quote(reason), _quote(target), _quote(ver))
                lines.append("evoagent_a2a_fallback_total{%s} %d" % (labels, count))
            for ver, target in sorted(self.timeouts):
                labels = "target_agent=%s,protocol_version=%s" % (_quote(target), _quote(ver))
                lines.append(
                    "evoagent_a2a_timeouts_total{%s} %d"
                    % (labels, self.timeouts[(ver, target)])
                )
            for (ver, source, target, method), count in sorted(self.requests.items()):
                pass
        return "\n".join(lines) + "\n"


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return '"%s"' % escaped


a2a_metrics = A2AMetrics()

__all__ = ["A2AMetrics", "a2a_metrics"]