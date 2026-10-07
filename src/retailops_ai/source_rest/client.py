"""Typed source reads with a hard process deadline and explicit live-read semantics."""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Generic, Literal, TypeVar
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator

from . import wire

P = TypeVar("P", bound=wire.ReadModel)
Page = (
    wire.ProductListResponse
    | wire.SaleListResponse
    | wire.InventorySnapshotListResponse
    | wire.ForecastListResponse
    | wire.StockRiskListResponse
)
Q = TypeVar("Q", bound=wire.QueryModel)
PIN = json.loads(Path(__file__).with_name("upstream.json").read_text())


class SourceReadError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False, status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.status = status


class ClientConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)
    base_url: str
    credential: SecretStr = Field(min_length=32, max_length=256)
    allow_http_loopback: bool = False
    attempt_timeout_seconds: float = Field(default=5.0, ge=0.1, le=5.0)
    deadline_seconds: float = Field(default=15.0, ge=0.1, le=15.0)
    max_attempts: int = Field(default=3, ge=1, le=3)
    failure_threshold: int = Field(default=3, ge=1, le=10)
    cooldown_seconds: float = Field(default=30.0, ge=1, le=60)
    max_business_age_seconds: int = Field(default=86400, ge=1, le=604800)

    @model_validator(mode="after")
    def origin(self) -> ClientConfig:
        url = urlsplit(self.base_url)
        permitted = url.scheme == "https" or (
            self.allow_http_loopback
            and url.scheme == "http"
            and url.hostname in ("127.0.0.1", "::1", "localhost")
        )
        if (
            not permitted
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in ("", "/")
        ):
            raise ValueError("configured_origin_required")
        _ = url.port
        if not re.fullmatch(r"[A-Za-z0-9._~\-]{32,256}", self.credential.get_secret_value()):
            raise ValueError("invalid_credential_format")
        return self


@dataclass(frozen=True)
class TraceContext:
    correlation_id: str
    traceparent: str | None = None

    def headers(self) -> dict[str, str]:
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", self.correlation_id):
            raise SourceReadError("invalid_trace")
        headers = {"X-Correlation-ID": self.correlation_id}
        if self.traceparent is not None:
            if (
                not re.fullmatch(r"00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}", self.traceparent)
                or self.traceparent[3:35] == "0" * 32
                or self.traceparent[36:52] == "0" * 16
            ):
                raise SourceReadError("invalid_trace")
            headers["traceparent"] = self.traceparent
        return headers


@dataclass(frozen=True)
class ReadMetadata:
    fetched_at: datetime
    business_as_of: datetime | None
    freshness_status: Literal["unknown", "stale", "missing"]
    freshness_policy: Literal["source-read-freshness-1"]
    contract_source_commit: str
    contract_sha256: str
    correlation_id: str
    source_resource: str
    semantics: str
    max_business_age_seconds: int
    read_mode: Literal["bounded_live"] = "bounded_live"
    snapshot_supported: Literal[False] = False


@dataclass(frozen=True)
class SourceRead(Generic[P]):
    value: P
    metadata: ReadMetadata


def reject_constant(value: str) -> None:
    raise ValueError("non_finite_json")


class SourceClient:
    def __init__(
        self,
        config: ClientConfig,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        utcnow: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.monotonic = monotonic
        self.utcnow = utcnow
        self.sleep = sleep
        self.failures = 0
        self.opened_until = 0.0
        self.lock = threading.Lock()

    def _request(
        self,
        path: str,
        params: dict[str, Any],
        trace: TraceContext,
        absolute_deadline: float | None = None,
    ) -> tuple[bytes, str]:
        if not self.lock.acquire(blocking=False):
            raise SourceReadError("client_busy", retryable=True)
        try:
            if self.monotonic() < self.opened_until:
                raise SourceReadError("circuit_open", retryable=True)
            headers = {
                "Accept": "application/json",
                "Authorization": "Bearer " + self.config.credential.get_secret_value(),
                **trace.headers(),
            }
            url = self.config.base_url.rstrip("/") + "/integration/v2/" + path
            if params:
                url += "?" + urlencode(params)
            deadline = self.monotonic() + self.config.deadline_seconds
            if absolute_deadline is not None:
                deadline = min(deadline, absolute_deadline)
            for attempt in range(self.config.max_attempts):
                remaining = deadline - self.monotonic()
                if remaining <= 0:
                    break
                timeout = min(self.config.attempt_timeout_seconds, remaining)
                request = json.dumps(
                    {"url": url, "headers": headers, "timeout": timeout}, allow_nan=False
                ).encode()
                try:
                    process = subprocess.run(
                        [sys.executable, "-m", "retailops_ai.source_rest.http_worker"],
                        input=request,
                        capture_output=True,
                        timeout=timeout,
                        check=False,
                        env={
                            "PYTHONNOUSERSITE": "1",
                            "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
                        },
                    )  # noqa: S603 - fixed private worker, secret is passed only through stdin
                    response = (
                        json.loads(process.stdout, parse_constant=reject_constant)
                        if process.returncode == 0
                        else {"error": "unavailable"}
                    )
                except (subprocess.TimeoutExpired, OSError, ValueError):
                    response = {"error": "unavailable"}
                if not isinstance(response, dict):
                    raise SourceReadError("invalid_response")
                status = response.get("status")
                if response.get("error") == "invalid_response":
                    raise SourceReadError("invalid_response")
                if status == 200:
                    self.failures = 0
                    reply_headers = response.get("headers", {})
                    if (
                        not isinstance(reply_headers, dict)
                        or reply_headers.get("X-RetailOps-Source-Contract-Sha256") != PIN["sha256"]
                    ):
                        raise SourceReadError("contract_mismatch")
                    if (
                        reply_headers.get("X-RetailOps-Read-Mode") != "bounded-live"
                        or reply_headers.get("X-Correlation-ID") != trace.correlation_id
                        or str(reply_headers.get("Content-Type", ""))
                        .split(";", 1)[0]
                        .strip()
                        .lower()
                        != "application/json"
                    ):
                        raise SourceReadError("invalid_response")
                    try:
                        body = base64.b64decode(response["body"], validate=True)
                        json.loads(body, parse_constant=reject_constant)
                    except (KeyError, ValueError, TypeError, RecursionError) as exc:
                        raise SourceReadError("invalid_response") from exc
                    if len(body) > 1048576:
                        raise SourceReadError("invalid_response")
                    return body, trace.correlation_id
                if status not in (None, 429, 500, 502, 503, 504):
                    self.failures = 0
                    code = {
                        401: "unauthorized",
                        403: "invalid_scope",
                        422: "invalid_query",
                        409: "snapshot_unsupported",
                    }.get(status, "invalid_response")
                    raise SourceReadError(code, status=status)
                if attempt + 1 < self.config.max_attempts:
                    retry_after = response.get("retry_after", "")
                    delay = (
                        min(float(retry_after), 1.0)
                        if isinstance(retry_after, str)
                        and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", retry_after)
                        else 0.1 * (attempt + 1)
                    )
                    if delay >= deadline - self.monotonic():
                        break
                    self.sleep(delay)
            self.failures += 1
            if self.failures >= self.config.failure_threshold:
                self.opened_until = self.monotonic() + self.config.cooldown_seconds
            raise SourceReadError("unavailable", retryable=True)
        finally:
            self.lock.release()

    def _get(
        self,
        path: str,
        query: wire.QueryModel | None,
        model: type[P],
        trace: TraceContext | None,
        absolute_deadline: float | None = None,
    ) -> SourceRead[P]:
        trace = trace or TraceContext("source-read-" + str(uuid4()))
        params = query.model_dump(mode="json", exclude_none=True) if query is not None else {}
        body, correlation = self._request(path, params, trace, absolute_deadline)
        try:
            value = model.model_validate_json(body)
        except ValidationError as exc:
            raise SourceReadError("invalid_response") from exc
        now = self.utcnow()
        as_of: datetime | None = None
        freshness: Literal["unknown", "stale", "missing"] = "unknown"
        if isinstance(
            value,
            (
                wire.ProductListResponse,
                wire.SaleListResponse,
                wire.InventorySnapshotListResponse,
                wire.ForecastListResponse,
                wire.StockRiskListResponse,
            ),
        ):
            if query is None:
                raise SourceReadError("invalid_query")
            requested = query.model_dump(mode="python")
            pagination = value.pagination
            expected_count = min(requested["limit"], max(0, pagination.total - requested["offset"]))
            if (
                pagination.limit != requested["limit"]
                or pagination.offset != requested["offset"]
                or len(value.items) != expected_count
            ):
                raise SourceReadError("invalid_response")
            identities = [
                getattr(item, "id", getattr(item, "product_id", None)) for item in value.items
            ]
            if len(set(identities)) != len(identities):
                raise SourceReadError("invalid_response")
            for item in value.items:
                if (
                    getattr(item, "product_id", getattr(item, "id", None))
                    != requested["product_id"]
                ):
                    raise SourceReadError("invalid_scope")
                if isinstance(item, wire.SaleResponse) and item.channel != requested["channel"]:
                    raise SourceReadError("invalid_scope")
                if (
                    isinstance(item, wire.InventorySnapshotResponse)
                    and item.warehouse_code != requested["warehouse_code"]
                ):
                    raise SourceReadError("invalid_scope")
            timestamps = [
                getattr(
                    item,
                    {
                        "products": "updated_at",
                        "sales": "sold_at",
                        "inventory-snapshots": "recorded_at",
                        "forecasts": "generated_at",
                        "inventory-risks": "inventory_updated_at",
                    }[path],
                )
                for item in value.items
            ]
            valid = [timestamp for timestamp in timestamps if timestamp is not None]
            as_of = max(valid) if valid else None
            if not value.items:
                freshness = "missing"
            elif (
                as_of is not None
                and (now - as_of).total_seconds() > self.config.max_business_age_seconds
            ):
                freshness = "stale"
        return SourceRead(
            value,
            ReadMetadata(
                fetched_at=now,
                business_as_of=as_of,
                freshness_status=freshness,
                freshness_policy="source-read-freshness-1",
                contract_source_commit=PIN["commit"],
                contract_sha256=PIN["sha256"],
                correlation_id=correlation,
                source_resource=path,
                semantics={
                    "products": "product_metadata",
                    "sales": "product_channel_sales_without_full_ml_grain",
                    "inventory-snapshots": "warehouse_inventory_without_location_mapping",
                    "forecasts": "legacy_product_period_forecast",
                    "inventory-risks": "legacy_product_heuristic_risk",
                    "capabilities": "source_capabilities",
                }[path],
                max_business_age_seconds=self.config.max_business_age_seconds,
            ),
        )

    def products(
        self, query: wire.ProductsQuery, *, trace: TraceContext | None = None
    ) -> SourceRead[wire.ProductListResponse]:
        return self._get("products", query, wire.ProductListResponse, trace)

    def sales(
        self, query: wire.SalesQuery, *, trace: TraceContext | None = None
    ) -> SourceRead[wire.SaleListResponse]:
        return self._get("sales", query, wire.SaleListResponse, trace)

    def inventory(
        self, query: wire.InventoryQuery, *, trace: TraceContext | None = None
    ) -> SourceRead[wire.InventorySnapshotListResponse]:
        return self._get("inventory-snapshots", query, wire.InventorySnapshotListResponse, trace)

    def forecasts(
        self, query: wire.ForecastsQuery, *, trace: TraceContext | None = None
    ) -> SourceRead[wire.ForecastListResponse]:
        return self._get("forecasts", query, wire.ForecastListResponse, trace)

    def risks(
        self, query: wire.RisksQuery, *, trace: TraceContext | None = None
    ) -> SourceRead[wire.StockRiskListResponse]:
        return self._get("inventory-risks", query, wire.StockRiskListResponse, trace)

    def capabilities(
        self, *, trace: TraceContext | None = None
    ) -> SourceRead[wire.SourceCapabilities]:
        return self._get("capabilities", None, wire.SourceCapabilities, trace)

    def snapshot(self) -> None:
        raise SourceReadError("snapshot_unsupported")

    def require_full_sales_grain(self) -> None:
        raise SourceReadError("unsupported_grain")

    def sales_pages(
        self,
        query: wire.SalesQuery,
        *,
        max_pages: int = 3,
        max_rows: int = 300,
        trace: TraceContext | None = None,
    ) -> tuple[SourceRead[wire.SaleListResponse], ...]:
        if not 1 <= max_pages <= 5 or not 1 <= max_rows <= 500:
            raise SourceReadError("invalid_budget")
        pages: list[SourceRead[wire.SaleListResponse]] = []
        ids: set[object] = set()
        total: int | None = None
        offset = query.offset
        deadline = self.monotonic() + self.config.deadline_seconds
        trace = trace or TraceContext("source-read-" + str(uuid4()))
        for _ in range(max_pages):
            if len(ids) >= max_rows:
                raise SourceReadError("budget_exceeded")
            current = wire.SalesQuery.model_validate_json(
                query.model_copy(
                    update={"offset": offset, "limit": min(query.limit, max_rows - len(ids))}
                ).model_dump_json()
            )
            page = self._get("sales", current, wire.SaleListResponse, trace, deadline)
            if total is not None and page.value.pagination.total != total:
                raise SourceReadError("source_changed")
            total = page.value.pagination.total
            for row in page.value.items:
                if row.id in ids:
                    raise SourceReadError("source_changed")
                ids.add(row.id)
            if len(ids) > max_rows:
                raise SourceReadError("budget_exceeded")
            pages.append(page)
            offset += len(page.value.items)
            if offset >= total or not page.value.items:
                return tuple(pages)
        raise SourceReadError("budget_exceeded")
