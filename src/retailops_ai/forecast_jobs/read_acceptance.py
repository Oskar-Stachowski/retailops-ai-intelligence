"""Real HTTP/SQL read acceptance; all forecasts are synthetic SQL-only quality stubs."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError

from retailops_ai.config import load_settings
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs.contracts import BatchRequest, QueuePolicy
from retailops_ai.forecast_jobs.freshness_acceptance import probes
from retailops_ai.forecast_jobs.input_store import PostgresInputStore
from retailops_ai.forecast_jobs.inputs import MAX_INPUT_BYTES, PreparedInputs
from retailops_ai.forecast_jobs.publication_acceptance import (
    expanded_fixture,
    result_for,
    seed_stub,
    snapshot,
    stub_release,
)
from retailops_ai.forecast_jobs.queue import PostgresBatchQueue
from retailops_ai.forecast_jobs.read_contracts import ForecastQuery
from retailops_ai.forecast_jobs.reader import PostgresForecastReader
from retailops_ai.model_lifecycle.acceptance import require
from retailops_ai.security.local import strict_json
from retailops_ai.security.provision import provision


def actor(products: tuple[str, ...], *, pipeline: bool = False) -> Principal:
    return Principal(
        "http-pipeline" if pipeline else "http-viewer",
        frozenset({"pipeline" if pipeline else "viewer"}),
        frozenset({"forecast:run" if pipeline else "forecast:read"}),
        frozenset(products),
        frozenset({"s-1"}),
        frozenset({"store"}),
    )


def read_snapshot(engine: Engine) -> dict[str, Any]:
    view = PostgresForecastReader(engine, "test").read(
        ForecastQuery(limit=200), actor(("fixture-product-00",))
    )
    require(view.pagination.total == 14, "read_state_missing_after_restart")
    result = snapshot(engine)
    result.update(
        read_view_sha256=view.view_sha256,
        read_predictions_sha256=canonical_sha256(
            [r.model_dump(mode="json", exclude={"freshness"}) for r in view.items]
        ),
    )
    return result


@contextmanager
def server(
    products: tuple[str, ...],
    *,
    locations: tuple[str, ...] = ("s-1",),
    channels: tuple[str, ...] = ("store",),
) -> Iterator[Callable[..., tuple[int, Any]]]:
    """No host port, no logged token, own child with private temporary grants."""
    with tempfile.TemporaryDirectory(prefix="forecast-read-access-") as temporary:
        private = Path(temporary)
        grants = private / "grants.json"
        grants.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "policy_id": "forecast-read-acceptance",
                    "grants": [
                        {
                            "principal_id": name,
                            "roles": [role],
                            "capabilities": [cap],
                            "scope": {
                                "product_ids": list(ids),
                                "selling_location_ids": list(locations),
                                "channels": list(channels),
                            },
                        }
                        for name, role, cap, ids in (
                            ("http-viewer", "viewer", "forecast:read", products[:1]),
                            ("http-all", "viewer", "forecast:read", products),
                            ("http-outside", "viewer", "forecast:read", ("outside",)),
                            ("http-pipeline", "pipeline", "forecast:run", products),
                        )
                    ],
                }
            )
        )
        provision(grants, private / "access", 1)
        tokens = {
            v["principal_id"]: v["bearer_token"]
            for v in json.loads((private / "access/api-client-credentials.json").read_text())[
                "credentials"
            ]
        }
        process = subprocess.Popen(
            [sys.executable, "-m", "retailops_ai", "serve"],
            env=dict(
                os.environ,
                HTTP_PORT="18082",
                HTTP_HOST="127.0.0.1",
                API_AUTH_FILE=str(private / "access/api-access-policy.json"),
                RAG_BEDROCK_ENABLED="false",
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )  # noqa: S603 - fixed own server

        def http(path: str, who: str | None = "http-viewer") -> tuple[int, Any]:
            conn = HTTPConnection("127.0.0.1", 18082, timeout=15)
            try:
                conn.request(
                    "GET",
                    path,
                    headers={} if who is None else {"Authorization": "Bearer " + tokens[who]},
                )
                response = conn.getresponse()
                return response.status, json.loads(response.read())
            finally:
                conn.close()

        try:
            for _ in range(100):
                try:
                    status, ready = http("/ready")
                    if status == 200:
                        require(
                            ready["role"] == "ai_api" and ready["status"] == "ready",
                            "forecast_reader_not_ready",
                        )
                        break
                except OSError:
                    pass
                time.sleep(0.1)
            else:
                raise ValueError("forecast_read_http_server_not_ready")
            yield http
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    if settings.app_env != "test" or settings.database_url is None or settings.image_digest is None:
        raise ValueError("read_acceptance_requires_disposable_test_database")
    engine = create_engine(
        settings.database_url.get_secret_value(), connect_args={"connect_timeout": 3}
    )
    try:
        if args.inspect:
            print(json.dumps(read_snapshot(engine)))
            return 0
        require(
            all(v == 0 for v in snapshot(engine)["counts"].values()),
            "read_acceptance_requires_empty_database",
        )
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("acceptance_input_byte_limit")
        strict_json(raw)
        inputs = expanded_fixture(PreparedInputs.model_validate_json(raw))
        require(
            inputs.scope.selling_location_ids == ("s-1",) and inputs.scope.channel == "store",
            "unexpected_read_fixture_scope",
        )
        PostgresInputStore(engine, "test").register(inputs)
        seed_stub(engine, stub_release(inputs, settings.image_digest))
        queue = PostgresBatchQueue(
            engine,
            "test",
            QueuePolicy(
                lease_seconds=30,
                heartbeat_seconds=1,
                attempt_timeout_seconds=60,
                run_timeout_seconds=300,
                retry_backoff_seconds=0,
            ),
            mechanics=False,
        )
        pipeline = actor(inputs.scope.product_ids, pipeline=True)

        def publish(key: str, *, product: str | None = None, units: float = 2.0) -> str:
            request = BatchRequest(
                profile_id=inputs.profile_id,
                as_of=inputs.as_of_time,
                channel="store",
                product_ids=() if product is None else (product,),
                horizons_days=(14,) if product is None else (7,),
            )
            run = queue.submit(request, pipeline, key)
            claim = queue.claim()
            require(
                claim is not None and claim.run.run_id == run.run_id, "read_fixture_claim_missing"
            )
            if claim is None:
                raise ValueError("read_fixture_claim_missing")
            result = result_for(claim)
            values = tuple(units for _ in result.quantities)
            result = result.model_copy(
                update={"quantities": values, "quantities_sha256": canonical_sha256(values)}
            )
            queue.complete_forecast(claim, result)
            return run.run_id

        first_id = publish("read-first")
        try:
            direct = PostgresForecastReader(engine, "test").read(
                ForecastQuery(), actor(inputs.scope.product_ids[:1])
            )
            require(direct.pagination.total == 14, "direct_read_scope_count_wrong")
        except DBAPIError as exc:
            state = getattr(exc.orig, "sqlstate", None)
            if isinstance(state, str) and len(state) == 5 and state.isalnum():
                print(json.dumps({"read_sqlstate": state}), flush=True)
            raise ValueError("direct_read_database_failed") from None
        checks = []
        with server(inputs.scope.product_ids) as http:
            uri = "/api/v1/forecasts"
            require(
                http(uri, None)[0] == 401 and http(uri, "http-pipeline")[0] == 403,
                "read_capability_not_enforced",
            )
            status, first = http(uri + "?limit=5")
            require(status == 200 and first["pagination"]["total"] == 14, "read_scope_count_wrong")
            sha = first["view_sha256"]
            status, second = http(uri + "?limit=5&offset=5&view_sha256=" + sha)
            require(
                status == 200
                and len(second["items"]) == 5
                and {v["prediction_id"] for v in first["items"]}.isdisjoint(
                    v["prediction_id"] for v in second["items"]
                ),
                "read_pages_not_stable",
            )
            require(http(uri + "?offset=5")[0] == 409, "read_unpinned_page_allowed")
            require(http(uri + "?product_id=outside")[0] == 422, "read_scope_filter_bypassed")
            require(
                http(uri, "http-outside")[1]["data_status"] == "no_data"
                and http(uri + "?inference_run_id=" + first_id, "http-outside")[0] == 404,
                "read_invisible_output_leaked",
            )
            require(
                all(
                    v["freshness"]["status"] == "stale"
                    and v["prediction_interval"] is None
                    and v["interval_unavailable_reason"] == "not_published"
                    for v in first["items"]
                ),
                "read_freshness_or_interval_fabricated",
            )
            target = first["items"][2]["target_date"]
            require(
                http(uri + "?target_from=" + target + "&target_to=" + target)[1]["pagination"][
                    "total"
                ]
                == 1,
                "read_target_filter_wrong",
            )
            require(
                http(uri + "?target_from=" + target)[0] == 422
                and http(uri + "?target_from=" + target + "T00:00:00Z&target_to=" + target)[0]
                == 422
                and http(uri + "?user_id=http-all")[0] == 422
                and http(uri + "?channel=store&channel=online")[0] == 422,
                "read_invalid_query_allowed",
            )
            checks.extend(
                [
                    "real_http_401_403_422_scoped_counts_and_invisible_pin_404",
                    "stable_scope_filtered_pagination_and_bounded_closed_date_queries",
                    "ready_classic_forecast_read_without_mlflow_or_bedrock",
                    "old_origin_is_stale_without_fabricated_intervals",
                ]
            )
            seed_stub(engine, stub_release(inputs, settings.image_digest, "2"))
            second_id = publish(
                "read-subset-replay", product=inputs.scope.product_ids[0], units=3.0
            )
            status, latest = http(uri)
            require(
                status == 200
                and latest["pagination"]["total"] == 14
                and all(
                    r["predicted_units"] == (3.0 if r["horizon_days"] <= 7 else 2.0)
                    for r in latest["items"]
                ),
                "read_latest_overlap_wrong",
            )
            require(
                http(uri + "?offset=5&view_sha256=" + sha)[0] == 409,
                "read_mixed_publication_pages_allowed",
            )
            require(
                http(uri + "?inference_run_id=" + first_id)[1]["items"][0]["model_version"] == "1"
                and latest["items"][0]["inference_run_id"] == second_id,
                "read_pins_followed_alias",
            )
            request = BatchRequest(
                profile_id=inputs.profile_id,
                as_of=inputs.as_of_time,
                channel="store",
                product_ids=inputs.scope.product_ids[:1],
            )
            queue.submit(request, pipeline, "read-failed-newer")
            failed = queue.claim()
            require(failed is not None, "read_failed_run_missing")
            if failed is None:
                raise ValueError("read_failed_run_missing")
            queue.fail(failed, reason="controlled_read_failure", retryable=False)
            after_failure = http(uri)[1]
            require(
                after_failure["view_sha256"] == latest["view_sha256"]
                and all(
                    r["freshness"]["reason"] == "newer_run_unpublished"
                    for r in after_failure["items"]
                ),
                "read_failed_run_hid_previous_output",
            )
            checks.extend(
                [
                    "latest_per_series_horizon_keeps_uncovered_days_and_numeric_release_pins",
                    "changed_latest_view_conflicts_while_original_run_stays_readable",
                    "failed_newer_attempt_keeps_previous_complete_predictions_explicitly_stale",
                ]
            )
            # History outside the viewer scope must not consume its candidate budget or count.
            for i in range(33):
                publish("read-outside-history-" + str(i), product=inputs.scope.product_ids[-1])
            require(
                http(uri)[0] == 200 and http(uri)[1]["pagination"]["total"] == 14,
                "read_outside_history_consumed_budget",
            )
            require(
                http(uri, "http-all")[0] == 429
                and http(uri + "?inference_run_id=" + first_id, "http-all")[0] == 200,
                "read_candidate_budget_or_pin_wrong",
            )
            checks.append(
                "sql_scope_filter_precedes_candidate_budget_and_pin_recovers_bounded_history"
            )
            before = snapshot(engine)
            # Table-owner corruption injection in this disposable fixture; restore exactly in finally.
            with engine.begin() as conn:
                part = conn.execute(
                    text(
                        "SELECT artifact_id,ordinal,partition FROM ai.forecast_output_partitions ORDER BY artifact_id,ordinal DESC"
                    )
                ).all()
                chosen = next(
                    p
                    for p in part
                    if any(
                        r["product_id"] == "fixture-product-19" for r in p.partition["predictions"]
                    )
                    and len(p.partition["predictions"]) > 7
                )
                changed = json.loads(json.dumps(chosen.partition))
                changed["predictions"][-1]["predicted_units"] += 1
                conn.execute(text("ALTER TABLE ai.forecast_output_partitions DISABLE TRIGGER USER"))
                conn.execute(
                    text(
                        "UPDATE ai.forecast_output_partitions SET partition=CAST(:p AS jsonb) WHERE artifact_id=:id AND ordinal=:n"
                    ),
                    {"p": json.dumps(changed), "id": chosen.artifact_id, "n": chosen.ordinal},
                )
                conn.execute(text("ALTER TABLE ai.forecast_output_partitions ENABLE TRIGGER USER"))
            try:
                status, invalid = http(uri)
                require(
                    status == 503
                    and invalid.get("code") == "forecast-output-invalid"
                    and "fixture-product-19" not in json.dumps(invalid),
                    "read_corrupt_hidden_partition_served",
                )
            finally:
                with engine.begin() as conn:
                    conn.execute(
                        text("ALTER TABLE ai.forecast_output_partitions DISABLE TRIGGER USER")
                    )
                    conn.execute(
                        text(
                            "UPDATE ai.forecast_output_partitions SET partition=CAST(:p AS jsonb) WHERE artifact_id=:id AND ordinal=:n"
                        ),
                        {
                            "p": json.dumps(chosen.partition),
                            "id": chosen.artifact_id,
                            "n": chosen.ordinal,
                        },
                    )
                    conn.execute(
                        text("ALTER TABLE ai.forecast_output_partitions ENABLE TRIGGER USER")
                    )
            require(
                snapshot(engine) == before and http(uri)[0] == 200, "read_corruption_restore_failed"
            )
            checks.append(
                "corrupt_out_of_page_and_out_of_scope_partition_refuses_entire_read_without_leaking_rows"
            )
            watermark_probes = probes(engine, queue, inputs, pipeline, http, settings.image_digest)
            checks.extend(
                [
                    "pinned_source_watermark_http_current_stale_unknown_and_cutoff_availability",
                    "future_declaration_rejected_and_rehashed_sql_watermark_mismatch_rolls_back",
                    "corrupt_watermark_refuses_scoped_read_and_exact_restore_recovers_current",
                ]
            )
        report = read_snapshot(engine)
        report.update(
            status="passed",
            checks=checks,
            purpose="sql_fixture_only",
            forecast_quality_approved=False,
            published_forecast_outputs=0,
            synthetic_output_manifests=42,
            watermark_probes=watermark_probes,
            real_http=True,
            bedrock_calls=0,
            mlflow_calls=0,
        )
        print(json.dumps(report))
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
