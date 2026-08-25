import threading
import time
from collections import defaultdict
from contextlib import contextmanager


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self.counters = defaultdict(float)
        self.duration_sum = defaultdict(float)
        self.duration_count = defaultdict(int)
        # Work Package 10: labelled observability families (append-only).
        self.agent_calls = defaultdict(int)
        self.agent_failures = defaultdict(int)
        self.finding_distribution = defaultdict(int)
        self.rule_fp = defaultdict(int)
        self.rule_total = defaultdict(int)
        # Work Package 7: chat observability.  Labels are finite-cardinality
        # only (provider/model/status/category/reason) - never tenant,
        # repository or session id, to avoid unbounded cardinality.
        self.chat_messages = defaultdict(int)           # status
        self.chat_requests = defaultdict(int)           # (provider, model, status)
        self.chat_requests_seconds = defaultdict(float) # (provider, model)
        self.chat_tokens_input = defaultdict(int)       # (provider, model)
        self.chat_tokens_output = defaultdict(int)      # (provider, model)
        self.chat_insights = defaultdict(int)           # (category, status)
        self.chat_feedback = defaultdict(int)           # category
        self.chat_failures = defaultdict(int)           # reason
        self.chat_invalid_citations = 0
        self.chat_stale_sessions = 0

    def inc(self, name: str, value: float = 1.0) -> None:
        with self._lock:
            self.counters[name] += value

    def record_chat_message(self, status: str) -> None:
        with self._lock:
            self.chat_messages[status] += 1

    def record_chat_request(
        self, provider: str, model: str, status: str, elapsed: float,
        input_tokens: int = 0, output_tokens: int = 0,
    ) -> None:
        with self._lock:
            key = (provider or "", model or "")
            self.chat_requests[(provider or "", model or "", status)] += 1
            self.chat_requests_seconds[key] += elapsed
            self.chat_tokens_input[key] += int(input_tokens or 0)
            self.chat_tokens_output[key] += int(output_tokens or 0)

    def record_chat_insight(self, category: str, status: str) -> None:
        with self._lock:
            self.chat_insights[(category or "", status or "")] += 1

    def record_chat_feedback(self, category: str) -> None:
        with self._lock:
            self.chat_feedback[category or ""] += 1

    def record_chat_failure(self, reason: str) -> None:
        with self._lock:
            self.chat_failures[reason or ""] += 1

    def record_chat_invalid_citations(self, count: int) -> None:
        if count <= 0:
            return
        with self._lock:
            self.chat_invalid_citations += int(count)

    def record_chat_stale_session(self) -> None:
        with self._lock:
            self.chat_stale_sessions += 1

    @contextmanager
    def timer(self, name: str):
        started = time.monotonic()
        try:
            yield
        finally:
            elapsed = time.monotonic() - started
            with self._lock:
                self.duration_sum[name] += elapsed
                self.duration_count[name] += 1

    def record_agent(self, agent_name: str, elapsed: float, failed: bool = False) -> None:
        """Work Package 10: per-agent call count, latency and failure rate."""
        with self._lock:
            self.agent_calls[agent_name] += 1
            self.duration_sum["agent_seconds:%s" % agent_name] += elapsed
            self.duration_count["agent_seconds:%s" % agent_name] += 1
            if failed:
                self.agent_failures[agent_name] += 1

    def record_finding(
        self, tenant_id: str, repository: str, rule_id: str, model: str,
    ) -> None:
        """Work Package 10: finding distribution by tenant x repository x rule x model."""
        with self._lock:
            self.finding_distribution[
                (tenant_id or "default", repository or "", rule_id or "", model or "")
            ] += 1

    def record_rule_feedback(self, rule_id: str, is_fp: bool) -> None:
        """Work Package 10: per-rule false-positive counters from feedback."""
        with self._lock:
            self.rule_total[rule_id] += 1
            if is_fp:
                self.rule_fp[rule_id] += 1

    def prometheus(self) -> str:
        with self._lock:
            lines = []
            for name, value in sorted(self.counters.items()):
                lines.extend(["# TYPE evoagent_%s counter" % name, "evoagent_%s %s" % (name, value)])
            for name, value in sorted(self.duration_sum.items()):
                lines.extend([
                    "# TYPE evoagent_%s_seconds summary" % name,
                    "evoagent_%s_seconds_sum %s" % (name, value),
                    "evoagent_%s_seconds_count %s" % (name, self.duration_count[name]),
                ])
            # Work Package 10: labelled families.
            for agent_name, calls in sorted(self.agent_calls.items()):
                label = "agent=%s" % _quote(agent_name)
                lines.append('evoagent_agent_calls_total{%s} %d' % (label, calls))
                lines.append(
                    'evoagent_agent_failures_total{%s} %d' % (label, self.agent_failures[agent_name])
                )
                latency = self.duration_sum.get("agent_seconds:%s" % agent_name, 0.0)
                count = self.duration_count.get("agent_seconds:%s" % agent_name, 0)
                lines.append('evoagent_agent_seconds_sum{%s} %s' % (label, latency))
                lines.append('evoagent_agent_seconds_count{%s} %d' % (label, count))
            for key, value in sorted(self.finding_distribution.items()):
                tenant, repository, rule_id, model = key
                label_parts = [
                    "tenant=%s" % _quote(tenant),
                    "repository=%s" % _quote(repository),
                    "rule_id=%s" % _quote(rule_id),
                    "model=%s" % _quote(model),
                ]
                lines.append(
                    "evoagent_finding_distribution_total{%s} %d"
                    % (",".join(label_parts), value)
                )
            for rule_id in sorted(set(self.rule_total) | set(self.rule_fp)):
                label = "rule_id=%s" % _quote(rule_id)
                lines.append(
                    'evoagent_rule_feedback_total{%s} %d' % (label, self.rule_total[rule_id])
                )
                lines.append(
                    'evoagent_rule_false_positives_total{%s} %d' % (label, self.rule_fp[rule_id])
                )
            # Work Package 7: chat observability families.
            for status in sorted(self.chat_messages):
                lines.append(
                    'evoagent_chat_messages_total{status=%s} %d'
                    % (_quote(status), self.chat_messages[status])
                )
            for (provider, model, status) in sorted(self.chat_requests):
                labels = "provider=%s,model=%s,status=%s" % (
                    _quote(provider), _quote(model), _quote(status))
                lines.append(
                    'evoagent_chat_requests_total{%s} %d' % (labels, self.chat_requests[(provider, model, status)])
                )
            for (provider, model) in sorted(self.chat_requests_seconds):
                labels = "provider=%s,model=%s" % (_quote(provider), _quote(model))
                lines.append(
                    'evoagent_chat_request_seconds_sum{%s} %s'
                    % (labels, self.chat_requests_seconds[(provider, model)])
                )
                lines.append(
                    'evoagent_chat_tokens_input_total{%s} %d'
                    % (labels, self.chat_tokens_input[(provider, model)])
                )
                lines.append(
                    'evoagent_chat_tokens_output_total{%s} %d'
                    % (labels, self.chat_tokens_output[(provider, model)])
                )
            for (category, status) in sorted(self.chat_insights):
                labels = "category=%s,status=%s" % (_quote(category), _quote(status))
                lines.append(
                    'evoagent_chat_insights_total{%s} %d' % (labels, self.chat_insights[(category, status)])
                )
            for category in sorted(self.chat_feedback):
                lines.append(
                    'evoagent_chat_feedback_total{category=%s} %d'
                    % (_quote(category), self.chat_feedback[category])
                )
            for reason in sorted(self.chat_failures):
                lines.append(
                    'evoagent_chat_failures_total{reason=%s} %d'
                    % (_quote(reason), self.chat_failures[reason])
                )
            lines.append("evoagent_chat_invalid_citations_total %d" % self.chat_invalid_citations)
            lines.append("evoagent_chat_stale_sessions_total %d" % self.chat_stale_sessions)
        return "\n".join(lines) + "\n"


def _quote(value: str) -> str:
    # Prometheus label values are double-quoted; escape the interior.
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return '"%s"' % escaped


metrics = Metrics()
