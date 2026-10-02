"""Real SQL/HTTP daily-clock probes using explicitly synthetic, unqualified inputs."""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from psycopg.errors import CheckViolation
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import IntegrityError

from retailops_ai.data_contracts.common import end_of_day
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs.contracts import BatchRequest
from retailops_ai.forecast_jobs.freshness_fixture import fixture
from retailops_ai.forecast_jobs.input_store import PostgresInputStore
from retailops_ai.forecast_jobs.inputs import PreparedInputs
from retailops_ai.forecast_jobs.publication_acceptance import (
    result_for,
    seed_stub,
    snapshot,
    stub_release,
)
from retailops_ai.forecast_jobs.queue import PostgresBatchQueue
from retailops_ai.model_lifecycle.acceptance import require


def probes(
    engine: Engine,
    queue: PostgresBatchQueue,
    inputs: PreparedInputs,
    pipeline: Principal,
    http: Callable[..., tuple[int, Any]],
    image: str,
) -> list[dict[str, Any]]:
    origin = end_of_day(datetime.now(UTC).date() - timedelta(days=1))
    store = PostgresInputStore(engine, "test")
    current = fixture(inputs, origin, complete_through=origin.date())
    seed_stub(engine, stub_release(current, image, "3"))
    cases = [
        ("complete", current, "current", "within_policy"),
        (
            "daily_close_previous_day",
            fixture(inputs, origin, complete_through=origin.date(), missing_origin=True),
            "current",
            "within_policy",
        ),
        (
            "lagging",
            fixture(inputs, origin, complete_through=origin.date() - timedelta(days=1)),
            "stale",
            "source_watermark_lag_exceeded",
        ),
        (
            "not_ready",
            fixture(inputs, origin, complete_through=None),
            "unknown",
            "source_watermark_not_ready",
        ),
        (
            "missing_at_cutoff",
            fixture(inputs, origin, complete_through=origin.date(), missing_history_days=2),
            "stale",
            "source_observation_lag_exceeded",
        ),
        (
            "unsupported",
            fixture(
                inputs,
                origin,
                complete_through=origin.date(),
                policy_version="daily-demand-unqualified-v99",
            ),
            "unknown",
            "source_watermark_policy_unsupported",
        ),
        (
            "unavailable",
            fixture(inputs, origin, complete_through=origin.date(), omit_watermark=True),
            "unknown",
            "source_watermark_unavailable",
        ),
    ]
    report = []
    first_id = ""
    for name, profile, status, reason in cases:
        registration = store.register(profile)
        require(store.register(profile) == registration, "watermark_registration_not_idempotent")
        request = BatchRequest(
            profile_id=profile.profile_id,
            as_of=origin,
            channel="store",
            product_ids=inputs.scope.product_ids[:1],
        )
        run = queue.submit(request, pipeline, "watermark-" + name)
        claimed = queue.claim()
        require(claimed is not None and claimed.run.run_id == run.run_id, "watermark_claim_missing")
        if claimed is None:
            raise ValueError("watermark_claim_missing")
        queue.complete_forecast(claimed, result_for(claimed))
        uri = "/api/v1/forecasts?inference_run_id=" + run.run_id
        code, body = http(uri)
        require(code == 200 and body["pagination"]["total"] == 14, "watermark_http_missing")
        require(
            all(
                (r["freshness"]["status"], r["freshness"]["reason"]) == (status, reason)
                for r in body["items"]
            ),
            "watermark_http_wrong_status",
        )
        encoded = json.dumps(body)
        require(
            "curated_descriptor" not in encoded
            and "fixture-product-19" not in encoded
            and "source_uri" not in encoded,
            "watermark_http_private_evidence_leak",
        )
        require(
            body["freshness_policy"]["policy_id"] == "forecast-read-v2", "watermark_policy_missing"
        )
        report.append(dict(case=name, run_id=run.run_id, freshness=body["items"][0]["freshness"]))
        if name == "complete":
            first_id = run.run_id
    future = fixture(
        inputs,
        origin,
        complete_through=origin.date(),
        declaration_as_of=datetime.now(UTC) + timedelta(days=1),
    )
    before = snapshot(engine)
    try:
        store.register(future)
    except ValueError as exc:
        require(
            str(exc) == "prepared_inputs_watermark_from_future", "wrong_future_watermark_refusal"
        )
    else:
        raise ValueError("future_watermark_registered")
    require(snapshot(engine) == before, "future_watermark_registration_changed_state")

    # SQL guard must bind metadata to the original registered profile, even after rehashing.
    request = BatchRequest(
        profile_id=current.profile_id,
        as_of=origin,
        channel="store",
        product_ids=inputs.scope.product_ids[-1:],
    )
    queue.submit(request, pipeline, "watermark-sql-refusal")
    claimed = queue.claim()
    if claimed is None:
        raise ValueError("watermark_guard_claim_missing")
    before = snapshot(engine)
    injected = False

    def change(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
    ) -> tuple[str, Any]:
        nonlocal injected
        if "INSERT INTO ai.forecast_output_manifests" in statement:
            raw = json.loads(parameters["manifest"])
            raw["source_freshness"]["observations"][0]["latest_complete_observation_date"] = (
                origin.date() - timedelta(days=1)
            ).isoformat()
            raw["artifact_id"] = "predictions-sha256-" + canonical_sha256(
                {k: v for k, v in raw.items() if k != "artifact_id"}
            )
            parameters = dict(parameters, manifest=json.dumps(raw), id=raw["artifact_id"])
            injected = True
        return statement, parameters

    event.listen(engine, "before_cursor_execute", change, retval=True)
    try:
        try:
            queue.complete_forecast(claimed, result_for(claimed))
        except IntegrityError as exc:
            require(
                isinstance(exc.orig, CheckViolation)
                and "forecast_output_freshness_input_binding" in str(exc.orig),
                "wrong_sql_watermark_refusal",
            )
        else:
            raise ValueError("sql_rehashed_watermark_mismatch_accepted")
    finally:
        event.remove(engine, "before_cursor_execute", change)
    require(injected and snapshot(engine) == before, "watermark_rejection_not_atomic")
    queue.fail(claimed, reason="controlled_watermark_pin_refusal", retryable=False)

    # Own disposable table-owner injection, exact restore before the restart test.
    with engine.begin() as conn:
        saved = conn.execute(
            text("SELECT artifact_id,manifest FROM ai.forecast_output_manifests WHERE run_id=:id"),
            {"id": first_id},
        ).one()
        changed = json.loads(json.dumps(saved.manifest))
        changed["source_freshness"]["observations"][0]["latest_complete_observation_date"] = (
            origin.date() - timedelta(days=1)
        ).isoformat()
        conn.execute(text("ALTER TABLE ai.forecast_output_manifests DISABLE TRIGGER USER"))
        conn.execute(
            text(
                "UPDATE ai.forecast_output_manifests SET manifest=CAST(:m AS jsonb) WHERE artifact_id=:id"
            ),
            {"m": json.dumps(changed), "id": saved.artifact_id},
        )
        conn.execute(text("ALTER TABLE ai.forecast_output_manifests ENABLE TRIGGER USER"))
    try:
        code, body = http("/api/v1/forecasts?inference_run_id=" + first_id)
        require(
            code == 503 and body.get("code") == "forecast-output-invalid",
            "corrupt_watermark_served",
        )
    finally:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE ai.forecast_output_manifests DISABLE TRIGGER USER"))
            conn.execute(
                text(
                    "UPDATE ai.forecast_output_manifests SET manifest=CAST(:m AS jsonb) WHERE artifact_id=:id"
                ),
                {"m": json.dumps(saved.manifest), "id": saved.artifact_id},
            )
            conn.execute(text("ALTER TABLE ai.forecast_output_manifests ENABLE TRIGGER USER"))
    code, body = http("/api/v1/forecasts?inference_run_id=" + first_id)
    require(
        code == 200 and all(r["freshness"]["status"] == "current" for r in body["items"]),
        "watermark_restore_failed",
    )
    return report
