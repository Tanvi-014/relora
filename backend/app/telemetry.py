"""
OpenTelemetry integration for Hermes.

Enabled when OTEL_EXPORTER_OTLP_ENDPOINT is set in the environment.
Provides distributed tracing (via OTLP/gRPC) and custom metrics for:
  - webhook ingestion throughput
  - delivery latency (P50/P95/P99)
  - DLQ depth
  - circuit breaker state changes

Usage in FastAPI / worker:
    from app.telemetry import setup_telemetry, get_tracer, get_meter
    setup_telemetry()           # call once at startup
    tracer = get_tracer()
    meter  = get_meter()
"""
import logging
import os
from typing import Optional

logger = logging.getLogger("hermes.telemetry")

_tracer = None
_meter = None
_otel_available = False


def setup_telemetry(service_name: str = "hermes") -> None:
    global _tracer, _meter, _otel_available

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        logger.info("OTEL_EXPORTER_OTLP_ENDPOINT not set — OpenTelemetry disabled.")
        return

    try:
        from opentelemetry import trace, metrics
        from opentelemetry.sdk.resources import Resource, SERVICE_NAME
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

        resource = Resource(attributes={SERVICE_NAME: service_name})

        # Traces
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
        )
        trace.set_tracer_provider(tracer_provider)
        _tracer = trace.get_tracer(service_name)

        # Metrics
        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=endpoint, insecure=True),
            export_interval_millis=30_000,
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
        metrics.set_meter_provider(meter_provider)
        _meter = metrics.get_meter(service_name)

        _otel_available = True
        logger.info("OpenTelemetry initialised → %s (service=%s)", endpoint, service_name)

        _register_instruments()

    except ImportError:
        logger.warning(
            "opentelemetry packages not installed — tracing disabled. "
            "Install: opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc"
        )
    except Exception as exc:
        logger.warning("OpenTelemetry setup failed: %s", exc)


# ---------------------------------------------------------------------------
# Instruments (counters / histograms)
# ---------------------------------------------------------------------------

_webhooks_ingested: Optional[object] = None
_webhooks_delivered: Optional[object] = None
_webhooks_failed: Optional[object] = None
_delivery_duration: Optional[object] = None
_dlq_depth: Optional[object] = None
_circuit_trips: Optional[object] = None


def _register_instruments() -> None:
    global _webhooks_ingested, _webhooks_delivered, _webhooks_failed
    global _delivery_duration, _dlq_depth, _circuit_trips

    if not _meter:
        return

    _webhooks_ingested = _meter.create_counter(
        "hermes.webhooks.ingested",
        description="Total webhooks accepted at the ingest endpoint",
        unit="1",
    )
    _webhooks_delivered = _meter.create_counter(
        "hermes.webhooks.delivered",
        description="Total webhooks successfully delivered to destinations",
        unit="1",
    )
    _webhooks_failed = _meter.create_counter(
        "hermes.webhooks.failed",
        description="Total webhooks that exhausted all retries (DLQ)",
        unit="1",
    )
    _delivery_duration = _meter.create_histogram(
        "hermes.delivery.duration_ms",
        description="HTTP delivery round-trip latency in milliseconds",
        unit="ms",
    )
    _dlq_depth = _meter.create_up_down_counter(
        "hermes.dlq.depth",
        description="Current number of webhooks in the dead-letter queue",
        unit="1",
    )
    _circuit_trips = _meter.create_counter(
        "hermes.circuit_breaker.trips",
        description="Number of times a destination circuit breaker opened",
        unit="1",
    )


# ---------------------------------------------------------------------------
# Public helpers — safe to call even when OTEL is disabled
# ---------------------------------------------------------------------------

def get_tracer():
    return _tracer


def get_meter():
    return _meter


def record_ingested(tenant_id: str) -> None:
    if _webhooks_ingested:
        _webhooks_ingested.add(1, {"tenant_id": tenant_id})


def record_delivered(tenant_id: str, destination_url: str, duration_ms: int) -> None:
    if _webhooks_delivered:
        _webhooks_delivered.add(1, {"tenant_id": tenant_id})
    if _delivery_duration:
        _delivery_duration.record(duration_ms, {"tenant_id": tenant_id, "outcome": "success"})


def record_delivery_failed(tenant_id: str, destination_url: str, duration_ms: int) -> None:
    if _delivery_duration:
        _delivery_duration.record(duration_ms, {"tenant_id": tenant_id, "outcome": "failure"})


def record_dlq(tenant_id: str) -> None:
    if _webhooks_failed:
        _webhooks_failed.add(1, {"tenant_id": tenant_id})
    if _dlq_depth:
        _dlq_depth.add(1, {"tenant_id": tenant_id})


def record_circuit_trip(destination_id: str) -> None:
    if _circuit_trips:
        _circuit_trips.add(1, {"destination_id": destination_id})


def start_span(name: str, attributes: Optional[dict] = None):
    """Context-manager wrapper; returns a no-op if tracing is disabled."""
    if _tracer:
        return _tracer.start_as_current_span(name, attributes=attributes or {})

    class _Noop:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass

    return _Noop()
