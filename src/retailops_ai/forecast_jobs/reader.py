"""Bounded, scoped, repeatable-read projection of complete immutable publications."""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from sqlalchemy import Engine, text

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs.contracts import BatchRun, MechanicsPrediction
from retailops_ai.forecast_jobs.publication import Publication, grain, receipt
from retailops_ai.forecast_jobs.queue import checked, record
from retailops_ai.forecast_jobs.read_contracts import (
    ForecastFreshness,
    ForecastItem,
    ForecastPage,
    ForecastQuery,
    Pagination,
    ReadErrorCode,
    ReadPolicy,
)

ForecastGrain = tuple[str, str, str, int]
RunOrder = tuple[datetime, datetime, str]


class ForecastReadError(ValueError):
    def __init__(self, status: int, code: ReadErrorCode) -> None:
        super().__init__(code)
        self.status, self.code = status, code


class ForecastReader(Protocol):
    def read(self, query: ForecastQuery, principal: Principal) -> ForecastPage: ...


@dataclass(frozen=True)
class ReadScope:
    products: tuple[str, ...]
    locations: tuple[str, ...]
    channels: tuple[str, ...]


def resolve_scope(query: ForecastQuery, actor: Principal) -> ReadScope:
    if "forecast:read" not in actor.capabilities:
        raise ForecastReadError(403, "forecast-read-denied")
    products = (query.product_id,) if query.product_id else tuple(sorted(actor.product_ids))
    locations = (
        (query.selling_location_id,)
        if query.selling_location_id
        else tuple(sorted(actor.selling_location_ids))
    )
    channels = (query.channel,) if query.channel else tuple(sorted(actor.channels))
    if (
        not products
        or not locations
        or not channels
        or (
            not set(products) <= actor.product_ids
            or not set(locations) <= actor.selling_location_ids
            or not set(channels) <= actor.channels
        )
    ):
        raise ForecastReadError(422, "forecast-scope-invalid")
    if len(products) > 20 or len(locations) > 5:
        raise ForecastReadError(422, "forecast-scope-limit")
    return ReadScope(products, locations, channels)


def verify_publication(output: Publication, run: BatchRun, environment: str) -> None:
    m = output.manifest
    i = run.input_ref
    if (
        run.status != "succeeded"
        or run.purpose != "qualified_forecast"
        or run.environment != environment
        or run.output_ref is None
        or run.output_ref.artifact_id != m.artifact_id
        or run.output_ref.kind != "predictions"
        or run.output_ref.complete is not True
        or (m.run_id, m.release_id, m.resolved_model, m.image_digest)
        != (run.run_id, run.release_id, run.resolved_model, run.image_digest)
        or (m.profile_id, m.scope, m.as_of_time, m.horizon_days)
        != (i.profile_id, i.scope, i.as_of_time, max(i.request.horizons_days))
        or (m.source_dataset_id, m.curated_dataset_id, m.feature_set_id)
        != (i.source_dataset_id, i.curated_dataset_id, i.feature_set_id)
        or run.started_at is None
        or run.completed_at is None
        or not run.started_at <= m.generated_at <= run.completed_at
    ):
        raise ValueError("forecast_read_run_pin_mismatch")


def projection(
    query: ForecastQuery,
    actor: Principal,
    outputs: tuple[tuple[Publication, BatchRun], ...],
    *,
    now: datetime,
    unpublished: dict[ForecastGrain, RunOrder],
) -> ForecastPage:
    """Verify every row before scope/filters/page; clock freshness does not change identity."""
    scope = resolve_scope(query, actor)
    policy = ReadPolicy()
    if len(outputs) > policy.max_candidate_outputs:
        raise ForecastReadError(429, "forecast-read-budget")
    selected: dict[
        tuple[str, str, str, int], tuple[MechanicsPrediction, Publication, BatchRun]
    ] = {}
    for output, run in outputs:
        # Revalidate even if a caller supplied model_copy or another unchecked object.
        output = Publication.model_validate_json(output.model_dump_json())
        run = BatchRun.model_validate_json(run.model_dump_json())
        verify_publication(output, run, run.environment)
        m = output.manifest
        if m.as_of_time > now or m.generated_at > now:
            raise ValueError("forecast_read_future_output")
        if query.inference_run_id and run.run_id != query.inference_run_id:
            continue
        if query.as_of and m.as_of_time != query.as_of:
            continue
        for partition in output.partitions:
            for row in partition.predictions:
                if (
                    row.product_id not in scope.products
                    or row.selling_location_id not in scope.locations
                    or row.channel not in scope.channels
                ):
                    continue
                key = grain(row)
                previous = selected.get(key)
                if previous is None or (m.as_of_time, run.requested_at, run.run_id) > (
                    previous[1].manifest.as_of_time,
                    previous[2].requested_at,
                    previous[2].run_id,
                ):
                    selected[key] = row, output, run
    if query.inference_run_id and not selected:
        raise ForecastReadError(404, "forecast-output-not-found")
    items = []
    # Choose latest per series/horizon BEFORE date filtering; never fall back to an older origin.
    for row, output, run in selected.values():
        if (
            query.target_from is not None
            and query.target_to is not None
            and not (query.target_from <= row.target_date <= query.target_to)
        ):
            continue
        m = output.manifest
        age = (now - row.forecast_origin).total_seconds()
        newer = unpublished.get(grain(row))
        reason: Literal[
            "source_watermark_unavailable", "origin_age_exceeded", "newer_run_unpublished"
        ]
        reason = (
            "newer_run_unpublished"
            if newer is not None and newer > (row.forecast_origin, run.requested_at, run.run_id)
            else "origin_age_exceeded"
            if age > policy.max_origin_age_seconds
            else "source_watermark_unavailable"
        )
        raw = row.model_dump(mode="json")
        raw.update(
            prediction_id="prediction-sha256-"
            + canonical_sha256(
                {
                    "projection": "forecast-read-v1",
                    "artifact_id": m.artifact_id,
                    "key": row.model_dump(mode="json", exclude={"predicted_units"}),
                }
            ),
            prediction_dataset_id=m.artifact_id,
            model_version=m.resolved_model.model_version,
            qualification_sha256=m.resolved_model.qualification_sha256,
            model_artifact_sha256=m.resolved_model.qualification.model.sha256,
            model_config_sha256=m.resolved_model.qualification.config_sha256,
            evaluation_id=m.resolved_model.qualification.evaluation_id,
            image_digest=m.image_digest,
            release_id=m.release_id,
            source_dataset_id=m.source_dataset_id,
            curated_dataset_id=m.curated_dataset_id,
            feature_set_id=m.feature_set_id,
            inference_run_id=m.run_id,
            profile_id=m.profile_id,
            execution_profile_id=m.execution_profile_id,
            generated_at=m.generated_at.isoformat(),
            freshness=ForecastFreshness(
                status="unknown" if reason == "source_watermark_unavailable" else "stale",
                reason=reason,
                evaluated_at=now,
                origin_age_seconds=age,
            ).model_dump(mode="json"),
        )
        items.append(ForecastItem.model_validate_json(json.dumps(raw)))
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
        {
            "projection": policy.policy_id,
            "principal_id": actor.principal_id,
            "scope": {
                "products": scope.products,
                "locations": scope.locations,
                "channels": scope.channels,
            },
            "query": query.model_dump(mode="json", exclude={"limit", "offset", "view_sha256"}),
            "predictions": [r.prediction_id for r in items],
        }
    )
    if query.offset and query.view_sha256 is None:
        raise ForecastReadError(409, "forecast-view-required")
    if query.view_sha256 is not None and query.view_sha256 != view:
        raise ForecastReadError(409, "forecast-view-changed")
    end = query.offset + query.limit
    return ForecastPage(
        items=tuple(items[query.offset : end]),
        pagination=Pagination(
            limit=query.limit,
            offset=query.offset,
            total=len(items),
            next_offset=end if end < len(items) else None,
        ),
        generated_at=now,
        data_status="available" if items else "no_data",
        selection=(
            "inference_run"
            if query.inference_run_id
            else "origin"
            if query.as_of
            else "latest_per_series_horizon"
        ),
        view_sha256=view,
    )


class PostgresForecastReader:
    def __init__(self, engine: Engine, environment: Literal["local", "test"]) -> None:
        self.engine, self.environment = engine, environment

    def read(self, query: ForecastQuery, principal: Principal) -> ForecastPage:
        query = ForecastQuery.model_validate_json(query.model_dump_json())
        scope = resolve_scope(query, principal)
        params = {
            "env": self.environment,
            "products": list(scope.products),
            "locations": list(scope.locations),
            "channels": list(scope.channels),
            "as_of": query.as_of,
            "run_id": query.inference_run_id,
            "cap": ReadPolicy().max_candidate_outputs + 1,
        }
        # Authorization is in SQL before the budget/count, not an after-pagination filter.
        with self.engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
            with conn.begin():
                conn.execute(text("SET TRANSACTION READ ONLY"))
                now = checked(conn)
                conn.execute(text("SET LOCAL statement_timeout='3s'"))
                rows = conn.execute(
                    text("""
                    SELECT m.artifact_id, m.manifest, r.record
                    FROM ai.forecast_output_manifests m
                    JOIN ai.forecast_batch_runs r ON r.run_id=m.run_id
                    WHERE m.environment=:env
                      AND m.manifest->'scope'->>'channel'=ANY(:channels)
                      AND (m.manifest->'scope'->'product_ids') ?| CAST(:products AS text[])
                      AND (m.manifest->'scope'->'selling_location_ids') ?| CAST(:locations AS text[])
                      AND (CAST(:as_of AS timestamptz) IS NULL OR
                           (m.manifest->>'as_of_time')::timestamptz=:as_of)
                      AND (CAST(:run_id AS text) IS NULL OR m.run_id=:run_id)
                    ORDER BY (m.manifest->>'as_of_time')::timestamptz DESC,
                             (r.record->>'requested_at')::timestamptz DESC, m.run_id DESC
                    LIMIT :cap
                """),
                    params,
                ).all()
                if len(rows) > ReadPolicy().max_candidate_outputs:
                    raise ForecastReadError(429, "forecast-read-budget")
                parts = conn.execute(
                    text("""
                    SELECT artifact_id, ordinal, partition, sha256 FROM ai.forecast_output_partitions
                    WHERE artifact_id=ANY(CAST(:ids AS text[])) ORDER BY artifact_id, ordinal
                """),
                    {"ids": [row.artifact_id for row in rows]},
                ).all()
                outputs_list = []
                for row in rows:
                    stored_parts = [p for p in parts if p.artifact_id == row.artifact_id]
                    output = Publication.model_validate_json(
                        json.dumps(
                            {
                                "manifest": row.manifest,
                                "partitions": [p.partition for p in stored_parts],
                            }
                        )
                    )
                    if output.manifest.artifact_id != row.artifact_id or any(
                        p.ordinal != part.ordinal or p.sha256 != receipt(part).sha256
                        for p, part in zip(stored_parts, output.partitions, strict=True)
                    ):
                        raise ValueError("forecast_read_partition_pin_mismatch")
                    outputs_list.append((output, record(row.record)))
                outputs = tuple(outputs_list)
                for output, run in outputs:
                    verify_publication(output, run, self.environment)
                unpublished = {
                    (row.product, row.location, row.channel, row.horizon): (
                        row.origin,
                        row.requested_at,
                        row.run_id,
                    )
                    for row in conn.execute(
                        text("""
                        SELECT DISTINCT ON (p.product,l.location,r.record->'input_ref'->'scope'->>'channel',h.horizon)
                               p.product, l.location, r.record->'input_ref'->'scope'->>'channel' channel,
                               h.horizon,
                               (r.record->'input_ref'->>'as_of_time')::timestamptz origin,
                               (r.record->>'requested_at')::timestamptz requested_at, r.run_id
                        FROM ai.forecast_batch_runs r
                        CROSS JOIN LATERAL jsonb_array_elements_text(
                            r.record->'input_ref'->'scope'->'product_ids') p(product)
                        CROSS JOIN LATERAL jsonb_array_elements_text(
                            r.record->'input_ref'->'scope'->'selling_location_ids') l(location)
                        CROSS JOIN LATERAL generate_series(1,(SELECT max(value::int)
                            FROM jsonb_array_elements_text(r.record->'input_ref'->'request'->'horizons_days'))) h(horizon)
                        WHERE r.environment=:env AND r.record->>'purpose'='qualified_forecast'
                          AND r.record->>'status'!='succeeded'
                          AND p.product=ANY(:products) AND l.location=ANY(:locations)
                          AND r.record->'input_ref'->'scope'->>'channel'=ANY(:channels)
                        ORDER BY p.product,l.location,channel,h.horizon,origin DESC,requested_at DESC,r.run_id DESC
                    """),
                        params,
                    )
                }
                return projection(query, principal, outputs, now=now, unpublished=unpublished)
