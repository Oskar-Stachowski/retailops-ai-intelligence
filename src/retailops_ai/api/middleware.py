"""Pure ASGI middleware preserves async request context and bounds telemetry labels."""

import logging
import re
from time import perf_counter

from opentelemetry.trace import SpanKind, StatusCode, Tracer
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from retailops_ai.adapters.telemetry import (
    CORRELATION_ID,
    HttpMetrics,
    context_headers,
    correlation_id,
    parent_context,
    trace_ids,
)
from retailops_ai.api.errors import problem_response

LOGGER = logging.getLogger("retailops_ai.http")
METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})


def single_header(headers: Headers, name: str) -> str | None:
    values = headers.getlist(name)
    return values[0] if len(values) == 1 else None


class HttpObservation:
    def __init__(
        self, app: ASGIApp, *, metrics: HttpMetrics, tracer: Tracer, compose: bool = False
    ) -> None:
        self.app, self.metrics, self.tracer = app, metrics, tracer
        self.host_pattern = (
            r"(?:127\.0\.0\.1|localhost|\[::1\]"
            + ("|api" if compose else "")
            + r")(?::[0-9]{1,5})?"
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        current = correlation_id(single_header(headers, "x-correlation-id"))
        token = CORRELATION_ID.set(current)
        started = perf_counter()
        status = 500
        response_started = False
        method = scope["method"] if scope["method"] in METHODS else "OTHER"
        with self.tracer.start_as_current_span(
            "HTTP request",
            context=parent_context(single_header(headers, "traceparent")),
            kind=SpanKind.SERVER,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:

            async def observed_send(message: Message) -> None:
                nonlocal status, response_started
                if message["type"] == "http.response.start":
                    status = message["status"]
                    response_started = True
                    outgoing = MutableHeaders(scope=message)
                    outgoing["X-Correlation-ID"] = current
                    traceparent = context_headers().get("traceparent")
                    if traceparent is not None:
                        outgoing["traceparent"] = traceparent
                    outgoing["Cache-Control"] = "no-store"
                    outgoing["X-Content-Type-Options"] = "nosniff"
                await send(message)

            try:
                host = single_header(headers, "host")
                if host is None or re.fullmatch(self.host_pattern, host.lower()) is None:
                    await problem_response(400)(scope, receive, observed_send)
                else:
                    await self.app(scope, receive, observed_send)
            except Exception:
                span.set_status(StatusCode.ERROR)
                if response_started:
                    raise
                await problem_response(500)(scope, receive, observed_send)
            finally:
                duration = perf_counter() - started
                # Framework route templates are bounded; raw paths/queries never become labels.
                route = getattr(scope.get("route"), "path", "unmatched")
                span.update_name(f"{method} {route}")
                span.set_attribute("http.request.method", method)
                span.set_attribute("http.route", route)
                span.set_attribute("http.response.status_code", status)
                if status >= 500:
                    span.set_status(StatusCode.ERROR)
                self.metrics.observe(method, route, status, duration)
                LOGGER.log(
                    logging.ERROR if status >= 500 else logging.INFO,
                    "http_request",
                    extra={
                        "event_data": {
                            "event": "http_request",
                            "method": method,
                            "route": route,
                            "status": status,
                            "duration_seconds": round(duration, 6),
                            "correlation_id": current,
                            **trace_ids(),
                        }
                    },
                )
                CORRELATION_ID.reset(token)
