from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid5

import pytest
from pydantic import SecretStr, ValidationError

from retailops_ai.source_rest import wire
from retailops_ai.source_rest.client import (
    PIN,
    ClientConfig,
    SourceClient,
    SourceReadError,
    TraceContext,
)

PRODUCT = UUID("85710dbe-1aea-50ac-a155-fb216e12ab97")
TOKEN = "explicit-ai10-source-test-" + "a" * 40
NOW = datetime(2026, 10, 4, tzinfo=UTC)


def sale(index: int) -> dict[str, Any]:
    return {
        "id": str(uuid5(PRODUCT, str(index))),
        "product_id": str(PRODUCT),
        "quantity": 3,
        "sold_at": "2026-10-01T00:00:00Z",
        "unit_price": 10.0,
        "total_amount": 30.0,
        "currency": "PLN",
        "channel": "store",
        "created_at": "2026-10-01T00:01:00Z",
    }


def query(**changes: Any) -> wire.SalesQuery:
    return wire.SalesQuery(
        product_id=PRODUCT,
        channel="store",
        sold_from=datetime(2026, 9, 1, tzinfo=UTC),
        sold_to=NOW,
        **changes,
    )


@contextmanager
def server(replies: Any) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    received: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            request = {
                "path": urlsplit(self.path).path,
                "query": parse_qs(urlsplit(self.path).query),
                "headers": {k.lower(): v for k, v in self.headers.items()},
            }
            received.append(request)
            reply = (
                replies(request, len(received))
                if callable(replies)
                else replies[min(len(received) - 1, len(replies) - 1)]
            )
            if reply.get("delay"):
                time.sleep(reply["delay"])
            self.send_response(reply.get("status", 200))
            headers = {
                "Content-Type": "application/json",
                "X-RetailOps-Read-Mode": "bounded-live",
                "X-RetailOps-Source-Contract-Sha256": PIN["sha256"],
                "X-Correlation-ID": self.headers.get("X-Correlation-ID", ""),
                **reply.get("headers", {}),
            }
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            body = reply.get("raw")
            if body is None:
                body = json.dumps(reply.get("body", {})).encode()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args: Any) -> None:
            pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    http.daemon_threads = True
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(http.server_port), received
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=2)


def client(url: str, **config: Any) -> SourceClient:
    return SourceClient(
        ClientConfig(base_url=url, credential=SecretStr(TOKEN), allow_http_loopback=True, **config),
        utcnow=lambda: NOW,
        sleep=lambda _: None,
    )


def page(
    rows: list[dict[str, Any]], limit: int = 50, offset: int = 0, total: int | None = None
) -> dict[str, Any]:
    return {
        "items": rows,
        "pagination": {
            "limit": limit,
            "offset": offset,
            "total": len(rows) if total is None else total,
        },
    }


@pytest.mark.parametrize("count", [0, 1, 100])
def test_real_http_typed_pages_and_business_freshness(count: int) -> None:
    rows = [sale(i) for i in range(count)]
    if rows:
        rows[0]["future_optional_field"] = {"version": "minor-compatible"}
    with server([{"body": page(rows, limit=100)}]) as (url, received):
        result = client(url).sales(query(limit=100))
    assert len(result.value.items) == count
    assert result.metadata.fetched_at == NOW
    assert result.metadata.freshness_status == ("missing" if not rows else "stale")
    assert result.metadata.snapshot_supported is False
    assert result.metadata.source_resource == "sales"
    assert result.metadata.semantics == "product_channel_sales_without_full_ml_grain"
    assert result.metadata.max_business_age_seconds == 86400
    assert received[0]["query"]["sold_from"] == ["2026-09-01T00:00:00Z"]
    assert received[0]["headers"]["authorization"] == "Bearer " + TOKEN
    if rows:
        assert result.value.items[0].model_extra == {
            "future_optional_field": {"version": "minor-compatible"}
        }
        assert result.metadata.business_as_of == datetime(2026, 10, 1, tzinfo=UTC)


def test_125_rows_use_bounded_last_page_without_claiming_snapshot() -> None:
    def replies(request: dict[str, Any], count: int) -> dict[str, Any]:
        offset = int(request["query"]["offset"][0])
        limit = int(request["query"]["limit"][0])
        return {
            "body": page(
                [sale(i) for i in range(offset, min(offset + limit, 125))], limit, offset, 125
            )
        }

    with server(replies) as (url, received):
        results = client(url).sales_pages(query(limit=100))
    assert [len(p.value.items) for p in results] == [100, 25]
    assert len(received) == 2
    assert all(p.metadata.read_mode == "bounded_live" for p in results)
    assert len({r["headers"]["x-correlation-id"] for r in received}) == 1


def test_recent_business_time_stays_unknown_without_watermark() -> None:
    row = dict(sale(0), sold_at="2026-10-03T23:00:00Z")
    with server([{"body": page([row])}]) as (url, _):
        result = client(url).sales(query())
    assert result.metadata.freshness_status == "unknown"


def test_retry_only_safe_gets_and_preserve_trace_headers() -> None:
    trace = TraceContext("cross-repo-http-42", "00-" + "1" * 32 + "-" + "2" * 16 + "-01")
    with server(
        [
            {"status": 503, "raw": b"private diagnostic"},
            {"status": 429, "headers": {"Retry-After": "0"}},
            {"body": page([])},
        ]
    ) as (url, received):
        result = client(url).sales(query(), trace=trace)
    assert len(received) == 3
    assert all(
        r["headers"]["x-correlation-id"] == trace.correlation_id
        and r["headers"]["traceparent"] == trace.traceparent
        for r in received
    )
    assert result.metadata.correlation_id == trace.correlation_id


@pytest.mark.parametrize(
    "status,code", [(401, "unauthorized"), (403, "invalid_scope"), (422, "invalid_query")]
)
def test_authorization_errors_are_not_retried_or_echoed(status: int, code: str) -> None:
    with server([{"status": status, "raw": TOKEN.encode()}]) as (url, received):
        with pytest.raises(SourceReadError) as error:
            client(url).sales(query())
    assert len(received) == 1 and error.value.code == code
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize(
    "reply,code",
    [
        (
            {"headers": {"X-RetailOps-Source-Contract-Sha256": "0" * 64}, "body": page([])},
            "contract_mismatch",
        ),
        ({"headers": {"Content-Type": "text/plain"}, "body": page([])}, "invalid_response"),
        ({"raw": b'{"items": [], "pagination": {"limit": NaN}}'}, "invalid_response"),
        (
            {"body": page([dict(sale(0), product_id="00000000-0000-4000-8000-000000000001")])},
            "invalid_scope",
        ),
        ({"body": page([dict(sale(0), channel="online")])}, "invalid_scope"),
        ({"body": page([dict(sale(0), quantity=0)])}, "invalid_response"),
        ({"body": page([sale(0), sale(0)])}, "invalid_response"),
        ({"body": page([], total=2)}, "invalid_response"),
        ({"raw": b"x" * 1048577}, "invalid_response"),
        ({"raw": b'{"extra":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}"}, "invalid_response"),
    ],
)
def test_invalid_or_foreign_response_cannot_become_source_data(
    reply: dict[str, Any], code: str
) -> None:
    with server([reply]) as (url, received):
        with pytest.raises(SourceReadError) as error:
            client(url).sales(query())
    assert error.value.code == code and len(received) == 1


def test_redirect_does_not_forward_service_credential() -> None:
    with server([{"body": page([])}]) as (target, target_received):
        with server([{"status": 302, "headers": {"Location": target + "/steal"}}]) as (
            url,
            received,
        ):
            with pytest.raises(SourceReadError) as error:
                client(url).sales(query())
    assert error.value.code == "invalid_response"
    assert len(received) == 1 and not target_received


def test_hard_deadline_terminates_slow_worker() -> None:
    with server([{"delay": 2.0, "body": page([])}]) as (url, received):
        start = time.monotonic()
        with pytest.raises(SourceReadError) as error:
            client(url, attempt_timeout_seconds=0.2, deadline_seconds=0.35).sales(query())
        elapsed = time.monotonic() - start
    assert elapsed < 1.5 and error.value.code == "unavailable"
    assert len(received) <= 3


def test_circuit_breaker_blocks_then_recovers_after_cooldown() -> None:
    clock = [100.0]
    with server([{"status": 503}, {"status": 503}, {"body": page([])}]) as (url, received):
        reader = SourceClient(
            ClientConfig(
                base_url=url,
                credential=SecretStr(TOKEN),
                allow_http_loopback=True,
                max_attempts=1,
                failure_threshold=2,
            ),
            monotonic=lambda: clock[0],
        )
        for _ in range(2):
            with pytest.raises(SourceReadError, match="unavailable"):
                reader.sales(query())
        with pytest.raises(SourceReadError, match="circuit_open"):
            reader.sales(query())
        assert len(received) == 2
        clock[0] += 31
        assert reader.sales(query()).value.items == []
    assert len(received) == 3


def test_query_and_missing_grain_are_rejected_before_transport() -> None:
    with pytest.raises(ValidationError):
        wire.SalesQuery.model_validate_json(
            json.dumps(dict(query().model_dump(mode="json"), store_id="invented"))
        )
    with server([{"body": page([])}]) as (url, received):
        reader = client(url)
        with pytest.raises(SourceReadError, match="snapshot_unsupported"):
            reader.snapshot()
        with pytest.raises(SourceReadError, match="unsupported_grain"):
            reader.require_full_sales_grain()
    assert not received


@pytest.mark.parametrize("change", ["total", "duplicate"])
def test_live_page_drift_or_duplicates_fail_explicitly(change: str) -> None:
    second = page(
        [sale(0 if change == "duplicate" else 1)],
        limit=1,
        offset=1,
        total=3 if change == "total" else 2,
    )
    with server([{"body": page([sale(0)], limit=1, total=2)}, {"body": second}]) as (url, _):
        with pytest.raises(SourceReadError, match="source_changed"):
            client(url).sales_pages(query(limit=1))


def test_row_budget_does_not_read_beyond_authorized_operation_bound() -> None:
    with server([{"body": page([sale(0)], limit=1, total=2)}]) as (url, received):
        with pytest.raises(SourceReadError, match="budget_exceeded"):
            client(url).sales_pages(query(limit=100), max_rows=1)
    assert len(received) == 1 and received[0]["query"]["limit"] == ["1"]


@pytest.mark.parametrize(
    "base",
    [
        "file:///etc/passwd",
        "http://example.com",
        "https://token:password@example.com",
        "https://example.com/extra",
        "https://example.com?query=1",
    ],
)
def test_origin_cannot_come_from_a_tool_url_or_redirect(base: str) -> None:
    with pytest.raises(ValidationError):
        ClientConfig(base_url=base, credential=SecretStr(TOKEN))


def test_private_cli_saves_payload_without_echoing_credentials(
    tmp_path, monkeypatch, capsys
) -> None:
    from retailops_ai.source_rest.cli import main

    with server([{"body": page([])}]) as (url, received):
        config_path = tmp_path / "config.json"
        query_path = tmp_path / "query.json"
        output = tmp_path / "page.json"
        config_path.write_text(
            json.dumps({"base_url": url, "credential": TOKEN, "allow_http_loopback": True})
        )
        query_path.write_text(query().model_dump_json())
        config_path.chmod(0o600)
        query_path.chmod(0o600)
        monkeypatch.setattr(
            "sys.argv",
            [
                "source-rest",
                "--config",
                str(config_path),
                "--query",
                str(query_path),
                "--resource",
                "sales",
                "--output",
                str(output),
            ],
        )
        assert main() == 0
        assert output.stat().st_mode & 0o077 == 0
        assert json.loads(output.read_text())["metadata"]["snapshot_supported"] is False
        assert TOKEN not in output.read_text()
        assert main() == 2  # Never overwrite an operator's existing result.
        config_path.chmod(0o644)
        assert main() == 2
    assert len(received) == 2
    assert TOKEN not in capsys.readouterr().out


def test_worker_gets_credentials_only_through_stdin(monkeypatch) -> None:
    import base64
    import subprocess

    calls = []

    def run(args, **kwargs):
        request = json.loads(kwargs["input"])
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args,
            0,
            json.dumps(
                {
                    "status": 200,
                    "headers": {
                        "Content-Type": "application/json",
                        "X-RetailOps-Read-Mode": "bounded-live",
                        "X-RetailOps-Source-Contract-Sha256": PIN["sha256"],
                        "X-Correlation-ID": request["headers"]["X-Correlation-ID"],
                    },
                    "body": base64.b64encode(json.dumps(page([])).encode()).decode(),
                }
            ).encode(),
            b"",
        )

    monkeypatch.setenv("PRIVATE_PROVIDER_SECRET", "must-not-reach-worker")
    monkeypatch.setattr("retailops_ai.source_rest.client.subprocess.run", run)
    reader = SourceClient(
        ClientConfig(base_url="https://retailops.example", credential=SecretStr(TOKEN))
    )
    assert reader.sales(query()).value.items == []
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert TOKEN not in " ".join(args)
    assert "PRIVATE_PROVIDER_SECRET" not in kwargs["env"]
    assert json.loads(kwargs["input"])["headers"]["Authorization"] == "Bearer " + TOKEN
