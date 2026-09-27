"""Allowlisted logs, isolated Prometheus registry and W3C request context."""

import json
import logging
import re
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Tracer, get_current_span
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

CORRELATION_ID: ContextVar[str | None] = ContextVar("correlation_id", default=None)
PROPAGATOR = TraceContextTextMapPropagator()


def correlation_id(value: str | None) -> str:
    if value is not None and re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", value
    ):
        return value
    return str(uuid4())


def parent_context(value: str | None) -> Context:
    # Only traceparent crosses this boundary; baggage/tracestate are not accepted.
    # Bound input before delegating W3C parsing and invalid-ID handling to OTel.
    carrier = {"traceparent": value} if value is not None and len(value) <= 512 else {}
    return PROPAGATOR.extract(carrier=carrier, context=Context())


def context_headers() -> dict[str, str]:
    """Use for explicit trusted-provider calls; never copy incoming headers wholesale."""
    headers: dict[str, str] = {}
    PROPAGATOR.inject(carrier=headers)
    current = CORRELATION_ID.get()
    if current is not None:
        headers["X-Correlation-ID"] = current
    return headers


def new_tracer() -> Tracer:
    # No global provider mutation, network exporter or background export worker.
    provider = TracerProvider(
        resource=Resource({"service.name": "retailops-ai-intelligence"}),
        shutdown_on_exit=False,
    )
    return provider.get_tracer("retailops_ai.http", "1.0")


def trace_ids() -> dict[str, str]:
    context = get_current_span().get_span_context()
    return {"trace_id": f"{context.trace_id:032x}", "span_id": f"{context.span_id:016x}"}


class JsonFormatter(logging.Formatter):
    """Never serialize arbitrary log messages, exception text, headers or request input."""

    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "event": "server_event",
        }
        if record.name == "retailops_ai.http":
            payload = getattr(record, "event_data", {})
            for key in (
                "event",
                "method",
                "route",
                "status",
                "duration_seconds",
                "correlation_id",
                "trace_id",
                "span_id",
            ):
                if key in payload:
                    data[key] = payload[key]
        return json.dumps(data, ensure_ascii=True, allow_nan=False)


def logging_config(level: str) -> dict[str, Any]:
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"json": {"()": JsonFormatter}},
        "handlers": {
            "json": {
                "class": "logging.StreamHandler",
                "formatter": "json",
                "stream": "ext://sys.stderr",
            }
        },
        "root": {"handlers": ["json"], "level": level},
        "loggers": {
            "uvicorn": {"handlers": ["json"], "level": level, "propagate": False},
            "uvicorn.error": {"handlers": ["json"], "level": level, "propagate": False},
            "uvicorn.access": {"handlers": [], "level": "CRITICAL", "propagate": False},
            "retailops_ai.http": {"handlers": ["json"], "level": level, "propagate": False},
        },
    }


class HttpMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        labels = ("method", "route", "status")
        self.requests = Counter(
            "retailops_ai_http_requests_total", "HTTP responses", labels, registry=self.registry
        )
        self.duration = Histogram(
            "retailops_ai_http_request_duration_seconds",
            "HTTP response duration",
            labels,
            buckets=(0.005, 0.025, 0.1, 0.5, 1.0, 5.0),
            registry=self.registry,
        )

    def observe(self, method: str, route: str, status: int, duration: float) -> None:
        values = (method, route, str(status))
        self.requests.labels(*values).inc()
        self.duration.labels(*values).observe(duration)

    def render(self) -> bytes:
        return generate_latest(self.registry)
