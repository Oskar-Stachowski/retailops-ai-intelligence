"""Verify complete stored outputs, then return only authorized physical risk rows."""

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from sqlalchemy import Engine, text

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs.read_contracts import Pagination
from retailops_ai.stockout_jobs.batch import MAX_OUTPUT_BYTES, StockoutOutput, verify_output
from retailops_ai.stockout_jobs.contracts import (
    StockoutJobRun,
    StockoutRequest,
    StockoutRun,
    public_run,
)
from retailops_ai.stockout_jobs.input_store import StockoutError, checked, load_inputs
from retailops_ai.stockout_jobs.ports import (
    StockoutAdministration as StockoutAdministration,
)
from retailops_ai.stockout_jobs.ports import (
    StockoutReader as StockoutReader,
)
from retailops_ai.stockout_jobs.priority import priorities
from retailops_ai.stockout_jobs.queue import PostgresStockoutQueue
from retailops_ai.stockout_jobs.read_contracts import (
    StockoutAttempts,
    StockoutPriority,
    StockoutQuery,
    StockoutRisk,
    StockoutRiskPage,
)
from retailops_ai.stockout_lifecycle.contract import MODEL, TEST_MODEL
from retailops_ai.stockout_runtime.contracts import RiskItem

MAX_READ_BYTES = 4 * 1024**2
MAX_CANDIDATES = 32


class PostgresStockoutAdministration:
    def __init__(self, queue: PostgresStockoutQueue) -> None:
        self.queue = queue

    def submit(self, request: StockoutRequest, principal: Principal, key: str) -> StockoutJobRun:
        return public_run(self.queue.submit(request, principal, key))

    def get(self, run_id: str, principal: Principal) -> StockoutJobRun:
        return public_run(self.queue.get(run_id, principal))

    def attempts(self, run_id: str, principal: Principal) -> StockoutAttempts:
        rows = self.queue.attempts(run_id, principal)
        with self.queue.engine.begin() as connection:
            now = checked(connection)
        return StockoutAttempts(
            run_id=run_id, items=tuple(public_run(r) for r in rows), generated_at=now
        )


def read_scope(query: StockoutQuery, principal: Principal) -> tuple[set[str], set[str]]:
    access = principal.stockout
    if "stockout:read" not in principal.capabilities or access is None:
        raise StockoutError(403, "stockout-scope-denied")
    products = {query.product_id} if query.product_id else set(access.product_ids)
    stocks = (
        {query.stock_location_id} if query.stock_location_id else set(access.stock_location_ids)
    )
    if (
        not products
        or not stocks
        or not products <= access.product_ids
        or not stocks <= access.stock_location_ids
        or len(products) > 20
        or len(stocks) > 5
    ):
        raise StockoutError(403, "stockout-scope-denied")
    return products, stocks


def fresh(
    item: RiskItem,
    now: datetime,
    *,
    pending: bool = False,
    priority: StockoutPriority | None = None,
) -> StockoutRisk:
    if now < item.generated_at:
        raise ValueError("stockout_read_future_output")
    age = (now - item.as_of).total_seconds()
    if pending:
        status, reason = "stale", "newer_run_unpublished"
    elif age > 86400:
        status, reason = "stale", "origin_age_exceeded"
    elif (
        item.lineage.source_watermark is None
        or item.lineage.source_completeness_status != "complete"
    ):
        status, reason = "unknown", "source_watermark_unavailable"
    else:
        status, reason = "current", "within_policy"
    return StockoutRisk.model_validate_json(
        canonical_bytes(
            {
                **item.model_dump(mode="json"),
                "freshness_status": status,
                "freshness_reason": reason,
                "read_at": now.isoformat(),
                "origin_age_seconds": age,
                "output_age_seconds": (now - item.generated_at).total_seconds(),
                "priority": priority.model_dump(mode="json") if priority is not None else None,
            }
        )
    )


class PostgresStockoutReader:
    def __init__(
        self, engine: Engine, environment: Literal["local", "test"], *, mechanics: bool = False
    ) -> None:
        if environment not in {"local", "test"} or mechanics and environment != "test":
            raise ValueError("stockout_reader_environment")
        self.engine, self.environment = engine, environment
        self.model = TEST_MODEL if mechanics else MODEL

    def _view(
        self, query: StockoutQuery, principal: Principal, *, risk_id: str | None = None
    ) -> StockoutRiskPage:
        query = StockoutQuery.model_validate_json(query.model_dump_json())
        products, stocks = read_scope(query, principal)
        with self.engine.begin() as connection:
            connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            now = checked(connection)
            params = dict(
                env=self.environment,
                model=self.model,
                products=json.dumps(sorted(products)),
                stocks=json.dumps(sorted(stocks)),
                asof=query.as_of,
                run=query.inference_run_id,
                risk=risk_id,
            )
            rows = (
                connection.execute(
                    text("""
SELECT b.record,o.output FROM ai.stockout_batch_outputs o JOIN ai.stockout_batch_runs b USING(run_id)
WHERE b.environment=:env AND b.status='succeeded' AND b.record->'release'->'binding'->>'model_name'=:model
AND (CAST(:run AS text) IS NULL OR b.run_id=CAST(:run AS text))
AND (CAST(:asof AS timestamptz) IS NULL OR (b.record->'input_ref'->'request'->>'as_of')::timestamptz=CAST(:asof AS timestamptz))
AND EXISTS(SELECT 1 FROM jsonb_array_elements(o.output->'items') i
 WHERE CAST(:products AS jsonb) ? (i->>'product_id') AND CAST(:stocks AS jsonb) ? (i->>'stock_location_id')
 AND (CAST(:risk AS text) IS NULL OR i->>'risk_id'=CAST(:risk AS text)))
ORDER BY (o.output->>'generated_at')::timestamptz DESC,b.run_id LIMIT 33
"""),
                    params,
                )
                .mappings()
                .all()
            )
            if len(rows) > MAX_CANDIDATES:
                raise StockoutError(503, "stockout-read-budget")
            pending: Sequence[object] = (
                connection.execute(
                    text(
                        """
SELECT record FROM ai.stockout_batch_runs WHERE environment=:env
AND record->'release'->'binding'->>'model_name'=:model AND status IN ('queued','running')
AND record->'input_ref'->'request'->'scope'->'product_ids' ?| ARRAY(SELECT jsonb_array_elements_text(CAST(:products AS jsonb)))
AND record->'input_ref'->'request'->'scope'->'stock_location_ids' ?| ARRAY(SELECT jsonb_array_elements_text(CAST(:stocks AS jsonb)))
ORDER BY (record->>'requested_at')::timestamptz LIMIT 101
"""
                    ),
                    dict(
                        env=self.environment,
                        model=self.model,
                        products=params["products"],
                        stocks=params["stocks"],
                    ),
                )
                .scalars()
                .all()
            )
            if len(pending) > 100:
                raise StockoutError(503, "stockout-read-budget")
            pending_runs = [StockoutRun.model_validate_json(json.dumps(r)) for r in pending]
            selected: dict[tuple[str, str], RiskItem] = {}
            exact: list[RiskItem] = []
            priority_rows: dict[str, StockoutPriority] = {}
            for row in rows:
                run = StockoutRun.model_validate_json(json.dumps(row["record"]))
                raw = json.dumps(row["output"]).encode()
                if len(raw) > MAX_OUTPUT_BYTES:
                    raise StockoutError(503, "stockout-read-budget")
                output = StockoutOutput.model_validate_json(raw)
                profile = load_inputs(
                    connection, self.environment, run.input_ref.request.profile_id
                )
                # Reconstruct the immutable running attempt for the verifier.
                running = run.model_dump(mode="json")
                running.update(status="running", completed_at=None, output_id=None, error=None)
                verify_output(
                    StockoutRun.model_validate_json(canonical_bytes(running)),
                    profile,
                    run.release,
                    output,
                )
                if output.output_id != run.output_id:
                    raise ValueError("stockout_read_output_pointer")
                priority_rows.update(priorities(output, profile, principal))
                for item in output.items:
                    if (
                        item.product_id not in products
                        or item.stock_location_id not in stocks
                        or risk_id
                        and item.risk_id != risk_id
                    ):
                        continue
                    if query.as_of is not None or query.inference_run_id is not None or risk_id:
                        exact.append(item)
                    else:
                        key = (item.product_id, item.stock_location_id)
                        old = selected.get(key)
                        if old is None or (item.as_of, item.generated_at, item.risk_id) > (
                            old.as_of,
                            old.generated_at,
                            old.risk_id,
                        ):
                            selected[key] = item
            items = sorted(
                exact if query.as_of or query.inference_run_id or risk_id else selected.values(),
                key=lambda r: (r.product_id, r.stock_location_id, r.as_of, r.risk_id),
            )
            # A state view filters the chosen latest state, rather than finding
            # an older matching status hidden behind a newer complete output.
            if query.view == "attention_queue":
                items = [i for i in items if priority_rows[i.risk_id].selected_at_origin is True]
            elif query.view == "current_stockouts":
                items = [i for i in items if i.status == "already_stockout"]
            if query.view == "attention_queue":
                items.sort(
                    key=lambda r: (-float(r.probability or 0), r.product_id, r.stock_location_id)
                )
            stale = {
                i.risk_id: any(
                    i.product_id in r.input_ref.request.scope.product_ids
                    and i.stock_location_id in r.input_ref.request.scope.stock_location_ids
                    and (
                        r.input_ref.request.as_of > i.as_of
                        or r.input_ref.request.as_of == i.as_of
                        and r.requested_at > i.generated_at
                    )
                    for r in pending_runs
                )
                for i in items
            }
            view = canonical_sha256(
                dict(
                    query=query.model_dump(mode="json", exclude={"offset", "limit", "view_sha256"}),
                    items=[i.model_dump(mode="json") for i in items],
                    newer_unpublished=stale,
                    priorities={
                        i.risk_id: priority_rows[i.risk_id].model_dump(mode="json") for i in items
                    },
                )
            )
            if query.view_sha256 is not None and query.view_sha256 != view:
                raise StockoutError(409, "stockout-view-changed")
            end = query.offset + query.limit
            projected = tuple(
                fresh(i, now, pending=stale[i.risk_id], priority=priority_rows[i.risk_id])
                for i in items[query.offset : end]
            )
            page = StockoutRiskPage(
                items=projected,
                pagination=Pagination(
                    limit=query.limit,
                    offset=query.offset,
                    total=len(items),
                    next_offset=end if end < len(items) else None,
                ),
                generated_at=now,
                data_status="available" if items else "no_data",
                selection="inference_run"
                if query.inference_run_id
                else "origin"
                if query.as_of
                else "latest_per_product_stock",
                view_sha256=view,
            )
            if len(canonical_bytes(page.model_dump(mode="json"))) > MAX_READ_BYTES:
                raise StockoutError(503, "stockout-read-budget")
            return page

    def list(self, query: StockoutQuery, principal: Principal) -> StockoutRiskPage:
        return self._view(query, principal)

    def get(self, risk_id: str, principal: Principal) -> StockoutRisk:
        page = self._view(StockoutQuery(), principal, risk_id=risk_id)
        if len(page.items) != 1:
            raise StockoutError(404, "stockout-risk-not-found")
        return page.items[0]
