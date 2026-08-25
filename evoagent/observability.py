"""Tracing helpers that degrade cleanly when OpenTelemetry is not installed."""
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional


trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")
logger = logging.getLogger("evoagent")


class Observability:
    def __init__(self, service_name: str = "evoagent", endpoint: str = ""):
        self.tracer = None
        self._span_processor = None
        self._closed = False
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
            if endpoint:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
                processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
                provider.add_span_processor(processor)
                self._span_processor = processor
            trace.set_tracer_provider(provider)
            self.tracer = trace.get_tracer(service_name)
        except ImportError:
            self.tracer = None

    def close(self) -> None:
        # Only release resources this service created and holds.  We install the
        # provider globally, so we never shut down a third-party/shared provider;
        # we only stop the OTLP exporter processor we own.  No-op without
        # OpenTelemetry, and idempotent across repeated calls.
        if self._closed:
            return
        self._closed = True
        if self._span_processor is not None:
            self._span_processor.shutdown()
            self._span_processor = None

    @contextmanager
    def span(self, name: str, trace_id: str = "", **attributes):
        token = trace_id_var.set(trace_id or trace_id_var.get())
        if self.tracer:
            with self.tracer.start_as_current_span(name) as span:
                for key, value in attributes.items():
                    if value is not None:
                        span.set_attribute(key, value)
                try:
                    yield span
                except Exception as exc:
                    span.record_exception(exc)
                    raise
                finally:
                    trace_id_var.reset(token)
        else:
            try:
                yield None
            finally:
                trace_id_var.reset(token)


class AlertManager:
    def __init__(self, store, failure_rate: float = .2, min_samples: int = 10):
        self.store = store
        self.failure_rate = failure_rate
        self.min_samples = min_samples

    def evaluate(self, tenant_id: str) -> None:
        stats = self.store.dashboard_stats(tenant_id)
        if stats["tasks_total"] >= self.min_samples:
            rate = stats["tasks_failed"] / stats["tasks_total"]
            if rate > self.failure_rate:
                self.store.create_alert(
                    tenant_id, "review-failure-rate", "critical",
                    "Review failure rate %.1f%% exceeds the %.1f%% threshold."
                    % (rate * 100, self.failure_rate * 100),
                )

    def evaluate_queue(
        self, queue, tenant_id: str,
        max_pending: int = 100, max_dead_letters: int = 20,
    ) -> None:
        """Work Package 10: queue backlog and dead-letter depth alerts."""
        pending = getattr(queue, "pending_count", lambda: 0)()
        if pending > max_pending:
            self.store.create_alert(
                tenant_id, "queue:backlog", "warning",
                "Queue backlog %d exceeds the %d threshold." % (pending, max_pending),
            )
        dead = getattr(queue, "dead_letter_count", lambda: 0)()
        if dead > max_dead_letters:
            self.store.create_alert(
                tenant_id, "queue:dead-letters", "critical",
                "Dead-letter queue depth %d exceeds the %d threshold."
                % (dead, max_dead_letters),
            )
