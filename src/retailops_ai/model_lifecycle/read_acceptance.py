"""Installed HTTP/SQL catalog acceptance; synthetic fixtures never attest model quality."""

import argparse
import json
import sys
from typing import Any

from sqlalchemy import Engine, create_engine, text

from retailops_ai.config import load_settings
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecast_jobs.contracts import BatchRequest, QueuePolicy
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
from retailops_ai.forecast_jobs.read_acceptance import actor, server
from retailops_ai.model_lifecycle.acceptance import require
from retailops_ai.model_lifecycle.contracts import MODEL
from retailops_ai.model_lifecycle.read_contracts import CatalogQuery
from retailops_ai.model_lifecycle.reader import PostgresModelCatalog
from retailops_ai.security.local import strict_json


def catalog_snapshot(engine: Engine) -> dict[str, Any]:
    reader = PostgresModelCatalog(engine, "test")
    who = actor(("fixture-product-00",))
    models = reader.models(CatalogQuery(), who)
    versions = reader.versions(MODEL, CatalogQuery(), who)
    all_versions = reader.versions(
        MODEL, CatalogQuery(), actor(tuple(f"fixture-product-{i:02}" for i in range(20)))
    )
    require(
        [v.model_version for v in versions.items] == ["2", "10"],
        "catalog_state_missing_after_restart",
    )
    require(all_versions.pagination.total == 3, "catalog_all_state_missing_after_restart")
    state = snapshot(engine)
    # Lifecycle rows are included as well as the publication/input tables.
    with engine.connect() as conn:
        registry = {
            table: conn.scalars(
                text(f"SELECT to_jsonb(t) FROM ai.{table} t ORDER BY to_jsonb(t)::text")  # noqa: S608 - closed allowlist
            ).all()
            for table in (
                "model_versions",
                "model_releases",
                "model_heads",
                "model_decisions",
                "model_steps",
            )
        }
    state.update(
        catalog_view_sha256=models.view_sha256,
        versions_view_sha256=versions.view_sha256,
        all_versions_view_sha256=all_versions.view_sha256,
        catalog_metadata_sha256=canonical_sha256(
            [v.model_dump(mode="json", exclude={"freshness"}) for v in versions.items]
        ),
        registry_state_sha256=canonical_sha256(registry),
    )
    return state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    if settings.app_env != "test" or settings.database_url is None or settings.image_digest is None:
        raise ValueError("catalog_acceptance_requires_disposable_test_database")
    engine = create_engine(
        settings.database_url.get_secret_value(), connect_args={"connect_timeout": 3}
    )
    try:
        if args.inspect:
            print(json.dumps(catalog_snapshot(engine)))
            return 0
        require(
            all(v == 0 for v in snapshot(engine)["counts"].values()),
            "catalog_acceptance_requires_empty_database",
        )
        with engine.connect() as conn:
            require(
                conn.scalar(text("SELECT count(*) FROM ai.model_versions")) == 0,
                "catalog_acceptance_requires_empty_registry",
            )
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("acceptance_input_byte_limit")
        strict_json(raw)
        inputs = expanded_fixture(PreparedInputs.model_validate_json(raw))
        require(
            inputs.scope.selling_location_ids == ("s-1",) and inputs.scope.channel == "store",
            "unexpected_catalog_fixture_scope",
        )
        PostgresInputStore(engine, "test").register(inputs)
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

        def publish(key: str, product: str) -> None:
            run = queue.submit(
                BatchRequest(
                    profile_id=inputs.profile_id,
                    as_of=inputs.as_of_time,
                    channel="store",
                    product_ids=(product,),
                    horizons_days=(7,),
                ),
                pipeline,
                key,
            )
            claim = queue.claim()
            require(
                claim is not None and claim.run.run_id == run.run_id,
                "catalog_fixture_claim_missing",
            )
            if claim is None:
                raise ValueError("catalog_fixture_claim_missing")
            queue.complete_forecast(claim, result_for(claim))

        seed_stub(engine, stub_release(inputs, settings.image_digest, "2"))
        publish("catalog-v2-viewer", inputs.scope.product_ids[0])
        checks = []
        with server(inputs.scope.product_ids) as http:
            uri = "/api/v1/models"
            detail = uri + "/" + MODEL
            versions = detail + "/versions"
            require(
                http(uri, None)[0] == 401 and http(uri, "http-pipeline")[0] == 403,
                "catalog_read_capability_not_enforced",
            )
            before_head = http(uri)[1]["view_sha256"]
            require(
                http(detail)[1]["approved_release"]["model_version"] == "2",
                "catalog_visible_head_missing",
            )
            seed_stub(engine, stub_release(inputs, settings.image_digest, "10"))
            require(
                http(detail)[1]["approved_release"] is None
                and http(versions)[1]["pagination"]["total"] == 1,
                "catalog_unpublished_version_or_head_leaked",
            )
            publish("catalog-v10-viewer", inputs.scope.product_ids[0])
            require(
                http(uri + "?view_sha256=" + before_head)[0] == 409,
                "catalog_changed_head_view_allowed",
            )
            status, first = http(versions + "?limit=1")
            require(
                status == 200
                and first["pagination"]["total"] == 2
                and first["items"][0]["model_version"] == "2",
                "catalog_numeric_order_or_scoped_count_wrong",
            )
            sha = first["view_sha256"]
            status, second = http(versions + "?limit=1&offset=1&view_sha256=" + sha)
            require(
                status == 200
                and second["items"][0]["model_version"] == "10"
                and second["pagination"]["next_offset"] is None,
                "catalog_stable_pagination_wrong",
            )
            require(http(versions + "?offset=1")[0] == 409, "catalog_unpinned_page_allowed")
            require(
                http(versions + "?product_id=outside")[0] == 422, "catalog_scope_filter_bypassed"
            )
            require(
                http(detail, "http-outside")[0] == 404
                and http(versions, "http-outside")[0] == 404
                and http(uri, "http-outside")[1]["pagination"]["total"] == 0,
                "catalog_invisible_model_leaked",
            )
            require(http(uri + "/unknown")[0] == 404, "catalog_unknown_model_disclosed")
            for bad in ("role=admin", "channel=store&channel=online", "limit=201", "offset=1001"):
                require(http(uri + "?" + bad)[0] == 422, "catalog_invalid_query_allowed")
            checks.extend(
                [
                    "installed_http_401_403_422_and_invisible_model_404",
                    "numeric_versions_2_before_10_and_scope_filtered_counts",
                    "stable_pages_require_principal_scope_and_unchanged_view",
                ]
            )
            seed_stub(engine, stub_release(inputs, settings.image_digest, "11"))
            publish("catalog-v11-outside", inputs.scope.product_ids[-1])
            summary = http(detail)[1]
            visible = http(versions)[1]
            all_visible = http(versions, "http-all")[1]
            require(
                summary["visible_version_count"] == 2
                and summary["approved_release"] is None
                and visible["pagination"]["total"] == 2
                and all_visible["pagination"]["total"] == 3,
                "catalog_outside_version_or_head_leaked",
            )
            require(
                http(detail, "http-all")[1]["approved_release"]["model_version"] == "11",
                "catalog_all_visible_head_missing",
            )
            require(
                summary["freshness"]["status"] == "unknown"
                and summary["registry_aliases"] is None
                and summary["deployed_model_version"] is None
                and summary["deployment_status"] == "not_attested"
                and summary["drift_status"] == "not_run",
                "catalog_runtime_status_fabricated",
            )
            serialized = json.dumps(visible)
            require(
                all(
                    s not in serialized
                    for s in ("source_uri", "mlflow-artifacts:", "gates", "fixture-product-19")
                ),
                "catalog_private_metadata_leaked",
            )
            scoped_sha = visible["view_sha256"]
            # Duplicate history cannot truncate distinct versions or alter a different scope's view.
            for i in range(33):
                publish("catalog-outside-history-" + str(i), inputs.scope.product_ids[-1])
            require(
                http(versions)[1]["view_sha256"] == scoped_sha
                and http(versions, "http-all")[1]["pagination"]["total"] == 3,
                "catalog_history_count_or_scope_view_wrong",
            )
            checks.extend(
                [
                    "unpublished_and_outside_scope_heads_hidden_without_registry_alias_or_deployment_claim",
                    "sql_scope_filter_and_distinct_versions_precede_limit_despite_33_duplicate_publications",
                    "only_sanitized_receipts_ids_and_unknown_registry_freshness_are_exposed",
                ]
            )
            before = catalog_snapshot(engine)
            with engine.connect() as conn:
                original = conn.scalar(
                    text(
                        "SELECT binding FROM ai.model_versions WHERE model_name=:name AND model_version='2'"
                    ),
                    {"name": MODEL},
                )
            require(isinstance(original, dict), "catalog_binding_fixture_missing")

            def replace(value: dict[str, Any]) -> None:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE ai.model_versions DISABLE TRIGGER USER"))
                    conn.execute(
                        text(
                            "UPDATE ai.model_versions SET binding=CAST(:binding AS jsonb) WHERE model_name=:name AND model_version='2'"
                        ),
                        {"binding": json.dumps(value), "name": MODEL},
                    )
                    conn.execute(text("ALTER TABLE ai.model_versions ENABLE TRIGGER USER"))

            try:
                changed = dict(original)
                changed["source_uri"] += "-tampered"
                replace(changed)
                status, invalid = http(versions)
                require(
                    status == 503
                    and invalid.get("code") == "model-metadata-invalid"
                    and "source_uri" not in json.dumps(invalid),
                    "catalog_mismatched_enrollment_served",
                )
                oversized = dict(original)
                oversized["source_uri"] = "x" * 65537
                replace(oversized)
                require(http(uri)[0] == 503, "catalog_oversized_metadata_served")
            finally:
                replace(original)
            require(
                catalog_snapshot(engine) == before and http(uri)[0] == 200,
                "catalog_corruption_restore_failed",
            )
            checks.append(
                "mismatched_and_oversized_enrollment_metadata_refuses_read_with_safe_503_and_exact_restore"
            )
        report = catalog_snapshot(engine)
        report.update(
            status="passed",
            checks=checks,
            purpose="sql_fixture_only",
            forecast_quality_approved=False,
            published_forecast_outputs=0,
            synthetic_output_manifests=36,
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
