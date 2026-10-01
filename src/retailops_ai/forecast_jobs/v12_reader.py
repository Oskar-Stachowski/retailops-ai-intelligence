"""Scoped, bounded, repeatable-read v12 forecasts with stable page identities."""

import json
from datetime import datetime
from typing import Literal, Protocol

from sqlalchemy import Engine, text

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs.freshness import freshness
from retailops_ai.forecast_jobs.queue import checked
from retailops_ai.forecast_jobs.read_contracts import ForecastQuery, Pagination, ReadPolicy
from retailops_ai.forecast_jobs.reader import (
    ForecastGrain,
    ForecastReadError,
    RunOrder,
    resolve_scope,
)
from retailops_ai.forecast_jobs.source_freshness import SourceFreshness
from retailops_ai.forecast_jobs.v12_batch import V12BatchReceipt, V12BatchRun
from retailops_ai.forecast_jobs.v12_publication import (
    V12Publication,
    V12PublishedRow,
    grain,
    verify_publication,
)
from retailops_ai.forecast_jobs.v12_queue import record
from retailops_ai.forecast_jobs.v12_read_contracts import V12ForecastItem, V12ForecastPage
from retailops_ai.model_lifecycle.v12_lifecycle_contracts import MODEL, TEST_MODEL

MAX_READ_BYTES = 16 * 1024**2


class V12ForecastReader(Protocol):
    def read(self, query: ForecastQuery, principal: Principal) -> V12ForecastPage: ...


def projection(
    query: ForecastQuery,
    actor: Principal,
    outputs: tuple[tuple[V12Publication, V12BatchRun, V12BatchReceipt], ...],
    *,
    now: datetime,
    unpublished: dict[ForecastGrain, RunOrder],
) -> V12ForecastPage:
    scope = resolve_scope(query, actor)
    if len(outputs) > ReadPolicy().max_candidate_outputs:
        raise ForecastReadError(429, "forecast-read-budget")
    selected: dict[ForecastGrain, tuple[V12PublishedRow, V12Publication, V12BatchRun]] = {}
    for output, run, receipt in outputs:
        output = V12Publication.model_validate_json(output.model_dump_json())
        run = V12BatchRun.model_validate_json(run.model_dump_json())
        verify_publication(output, run, receipt)
        if output.generated_at > now or output.as_of_time > now:
            raise ValueError("v12_read_future_publication")
        if (query.inference_run_id and run.run_id != query.inference_run_id) or (
            query.as_of and output.as_of_time != query.as_of
        ):
            continue
        for row in output.rows:
            if (
                row.product_id not in scope.products
                or row.selling_location_id not in scope.locations
                or row.channel not in scope.channels
            ):
                continue
            old = selected.get(grain(row))
            if old is None or (output.as_of_time, run.requested_at, run.run_id) > (
                old[1].as_of_time,
                old[2].requested_at,
                old[2].run_id,
            ):
                selected[grain(row)] = row, output, run
    if query.inference_run_id and not selected:
        raise ForecastReadError(404, "forecast-output-not-found")
    items = []
    for row, output, run in selected.values():
        if (
            query.target_from is not None
            and query.target_to is not None
            and not query.target_from <= row.target_date <= query.target_to
        ):
            continue
        newer = unpublished.get(grain(row))
        binding = output.resolved_model
        raw = row.model_dump(mode="json")
        raw.update(
            prediction_id="prediction-sha256-"
            + canonical_sha256(
                dict(
                    projection="forecast-v12-read-v1",
                    artifact_id=output.artifact_id,
                    key=row.model_dump(
                        mode="json",
                        include=set(V12PublishedRow.model_fields)
                        - {"prediction", "execution_profile_id"},
                    ),
                )
            ),
            prediction_dataset_id=output.artifact_id,
            model_name=binding.model_name,
            model_version=binding.model_version,
            approval_sha256=binding.approval_sha256,
            runtime_pin_sha256=canonical_sha256(
                binding.approval.qualification.pin.model_dump(mode="json")
            ),
            image_digest=output.image_digest,
            release_id=output.release_id,
            receipt_id=output.receipt_id,
            source_dataset_id=output.source_dataset_id,
            curated_dataset_id=output.curated_dataset_id,
            feature_set_id=output.feature_set_id,
            inference_run_id=output.run_id,
            profile_id=output.profile_id,
            generated_at=output.generated_at.isoformat(),
            approval_valid_until=binding.approval.qualification.valid_until.isoformat(),
            freshness=freshness(
                row,
                output.source_freshness,
                now=now,
                newer_unpublished=newer is not None
                and newer > (row.forecast_origin, run.requested_at, run.run_id),
            ).model_dump(mode="json"),
        )
        items.append(V12ForecastItem.model_validate_json(json.dumps(raw)))
    items.sort(
        key=lambda r: (
            r.product_id,
            r.selling_location_id,
            r.channel,
            r.target_date,
            r.forecast_origin,
            r.prediction_id,
        )
    )
    view = canonical_sha256(
        dict(
            projection="forecast-v12-read-v1",
            principal_id=actor.principal_id,
            scope=dict(products=scope.products, locations=scope.locations, channels=scope.channels),
            query=query.model_dump(mode="json", exclude={"limit", "offset", "view_sha256"}),
            predictions=[r.prediction_id for r in items],
        )
    )
    if query.offset and query.view_sha256 is None:
        raise ForecastReadError(409, "forecast-view-required")
    if query.view_sha256 is not None and query.view_sha256 != view:
        raise ForecastReadError(409, "forecast-view-changed")
    end = query.offset + query.limit
    return V12ForecastPage(
        items=tuple(items[query.offset : end]),
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
        else "latest_per_series_horizon",
        view_sha256=view,
    )


class PostgresV12ForecastReader:
    def __init__(
        self, engine: Engine, environment: Literal["local", "test"], *, mechanics: bool = False
    ) -> None:
        if environment not in {"local", "test"} or (mechanics and environment != "test"):
            raise ValueError("v12_read_environment")
        self.engine, self.environment = engine, environment
        self.model = TEST_MODEL if mechanics else MODEL

    def read(self, query: ForecastQuery, principal: Principal) -> V12ForecastPage:
        query = ForecastQuery.model_validate_json(query.model_dump_json())
        scope = resolve_scope(query, principal)
        params = dict(
            env=self.environment,
            model=self.model,
            products=list(scope.products),
            locations=list(scope.locations),
            channels=list(scope.channels),
            as_of=query.as_of,
            run_id=query.inference_run_id,
            cap=ReadPolicy().max_candidate_outputs + 1,
        )
        with self.engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
            with conn.begin():
                conn.execute(text("SET TRANSACTION READ ONLY"))
                now = checked(conn)
                conn.execute(text("SET LOCAL statement_timeout='3s'"))
                headers = conn.execute(
                    text("""
SELECT o.artifact_id, octet_length(o.document::text)+octet_length(c.receipt::text) bytes
FROM ai.v12_forecast_outputs o JOIN ai.v12_batch_runs r USING(run_id)
JOIN ai.v12_batch_receipts c USING(run_id)
WHERE o.environment=:env AND o.model_name=:model
 AND o.document->'scope'->>'channel'=ANY(:channels)
 AND (o.document->'scope'->'product_ids') ?| CAST(:products AS text[])
 AND (o.document->'scope'->'selling_location_ids') ?| CAST(:locations AS text[])
 AND (CAST(:as_of AS timestamptz) IS NULL OR (o.document->>'as_of_time')::timestamptz=:as_of)
 AND (CAST(:run_id AS text) IS NULL OR o.run_id=:run_id)
ORDER BY (o.document->>'as_of_time')::timestamptz DESC,(r.record->>'requested_at')::timestamptz DESC,o.run_id DESC LIMIT :cap
"""),
                    params,
                ).all()
                if (
                    len(headers) > ReadPolicy().max_candidate_outputs
                    or sum(h.bytes for h in headers) > MAX_READ_BYTES
                ):
                    raise ForecastReadError(429, "forecast-read-budget")
                outputs = []
                for row in conn.execute(
                    text("""
SELECT o.artifact_id,o.document,r.record,c.receipt,
 p.profile->>'schema_version' input_version,p.profile->'source_freshness' input_freshness
FROM ai.v12_forecast_outputs o JOIN ai.v12_batch_runs r USING(run_id)
JOIN ai.v12_batch_receipts c USING(run_id)
JOIN ai.forecast_prepared_inputs p ON p.environment=o.environment AND p.profile_id=r.profile_id
WHERE o.artifact_id=ANY(CAST(:ids AS text[]))
"""),
                    dict(ids=[h.artifact_id for h in headers]),
                ):
                    output = V12Publication.model_validate_json(json.dumps(row.document))
                    run = record(row.record)
                    receipt = V12BatchReceipt.model_validate_json(json.dumps(row.receipt))
                    source = SourceFreshness.model_validate_json(json.dumps(row.input_freshness))
                    if (
                        output.artifact_id != row.artifact_id
                        or output.environment != self.environment
                        or output.resolved_model.model_name != self.model
                        or row.input_version != "1.1"
                        or source.scoped(output.scope) != output.source_freshness
                    ):
                        raise ValueError("v12_read_stored_pin")
                    outputs.append((output, run, receipt))
                if len(outputs) != len(headers):
                    raise ValueError("v12_read_missing_dependency")
                unpublished = {
                    (r.product, r.location, r.channel, r.horizon): (
                        r.origin,
                        r.requested_at,
                        r.run_id,
                    )
                    for r in conn.execute(
                        text("""
SELECT DISTINCT ON (p.product,l.location,r.record->'input_ref'->'scope'->>'channel',h.horizon)
 p.product,l.location,r.record->'input_ref'->'scope'->>'channel' channel,h.horizon,
 (r.record->'input_ref'->>'as_of_time')::timestamptz origin,
 (r.record->>'requested_at')::timestamptz requested_at,r.run_id
FROM ai.v12_batch_runs r
CROSS JOIN LATERAL jsonb_array_elements_text(r.record->'input_ref'->'scope'->'product_ids') p(product)
CROSS JOIN LATERAL jsonb_array_elements_text(r.record->'input_ref'->'scope'->'selling_location_ids') l(location)
CROSS JOIN LATERAL generate_series(1,(SELECT max(value::int) FROM jsonb_array_elements_text(r.record->'input_ref'->'request'->'horizons_days'))) h(horizon)
WHERE r.environment=:env AND r.record->'resolved_model'->>'model_name'=:model
 AND NOT EXISTS(SELECT 1 FROM ai.v12_forecast_outputs o WHERE o.run_id=r.run_id)
 AND p.product=ANY(:products) AND l.location=ANY(:locations)
 AND r.record->'input_ref'->'scope'->>'channel'=ANY(:channels)
ORDER BY p.product,l.location,channel,h.horizon,origin DESC,requested_at DESC,r.run_id DESC
"""),
                        params,
                    )
                }
                return projection(
                    query, principal, tuple(outputs), now=now, unpublished=unpublished
                )
