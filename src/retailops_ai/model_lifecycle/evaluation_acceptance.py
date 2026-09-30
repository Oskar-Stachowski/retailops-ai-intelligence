"""Installed HTTP/SQL whole-scope evaluation acceptance with optional historical evidence."""

import argparse
import json
import sys
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import IntegrityError

from retailops_ai.config import load_settings
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecast_jobs.inputs import PreparedInputs
from retailops_ai.forecast_jobs.publication_acceptance import expanded_fixture, snapshot
from retailops_ai.forecast_jobs.read_acceptance import actor, server
from retailops_ai.model_lifecycle.acceptance import require
from retailops_ai.model_lifecycle.evaluation_contracts import EvaluationEvidence, EvaluationQuery
from retailops_ai.model_lifecycle.evaluation_fixture import fixture
from retailops_ai.model_lifecycle.evaluation_store import PostgresEvaluations
from retailops_ai.security.local import strict_json


def state(engine: Engine) -> dict[str, Any]:
    reader = PostgresEvaluations(engine, "test")
    page = reader.evaluations(EvaluationQuery(), actor(("fixture-product-00",)))
    require(page.pagination.total == 2, "evaluation_view_missing_after_restart")
    return dict(
        snapshot(engine),
        evaluation_view_sha256=page.view_sha256,
        evaluation_metadata_sha256=canonical_sha256(
            [v.model_dump(mode="json", exclude={"freshness"}) for v in page.items]
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    if settings.app_env != "test" or settings.database_url is None or settings.image_digest is None:
        raise ValueError("evaluation_acceptance_requires_disposable_test_database")
    engine = create_engine(
        settings.database_url.get_secret_value(), connect_args={"connect_timeout": 3}
    )
    try:
        if args.inspect:
            print(json.dumps(state(engine)))
            return 0
        require(
            all(v == 0 for v in snapshot(engine)["counts"].values()),
            "evaluation_acceptance_requires_empty_database",
        )
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("evaluation_acceptance_input_byte_limit")
        strict_json(raw)
        payload = json.loads(raw)
        require(
            set(payload) == {"fixture", "historical_evidence"},
            "evaluation_acceptance_input_fields_invalid",
        )
        inputs = expanded_fixture(
            PreparedInputs.model_validate_json(json.dumps(payload["fixture"]))
        )
        historical = (
            EvaluationEvidence.model_validate_json(json.dumps(payload["historical_evidence"]))
            if payload["historical_evidence"]
            else None
        )
        products = inputs.scope.product_ids
        now = datetime.now(UTC)
        reader = PostgresEvaluations(engine, "test")
        one = fixture(products[:1], now, salt="one", status="failed")
        broad = fixture((products[0], products[-1]), now, salt="broad")
        outside = fixture(products[-1:], now, salt="outside", status="passed")
        for value in (one, broad, outside):
            reader.register(value)
        require(reader.register(one) == one, "evaluation_repeat_import_not_idempotent")
        checks = []
        uri = "/api/v1/evaluations"
        with server(products) as http:
            require(
                http(uri, None)[0] == 401 and http(uri, "http-pipeline")[0] == 403,
                "evaluation_read_capability_not_enforced",
            )
            status, visible = http(uri)
            require(
                status == 200
                and visible["pagination"]["total"] == 1
                and visible["items"][0]["evaluation_id"] == one.descriptor.evaluation_id,
                "evaluation_whole_scope_count_wrong",
            )
            require(
                http(uri, "http-all")[1]["pagination"]["total"] == 3
                and http(uri, "http-outside")[1]["data_status"] == "no_data",
                "evaluation_complete_scope_or_empty_wrong",
            )
            require(
                http(uri + "/" + broad.descriptor.evaluation_id)[0] == 404
                and http(uri + "/" + outside.descriptor.evaluation_id)[0] == 404
                and http(uri + "/forecast-quality-sha256-" + "f" * 64)[0] == 404,
                "evaluation_global_metrics_leaked_to_partial_scope",
            )
            detail = http(uri + "/" + one.descriptor.evaluation_id)[1]
            require(
                len(detail["metrics"]) == 14
                and all(
                    m["point"]["mae"] == 1.0 and m["point"]["wape"] == 2 / 3
                    for m in detail["metrics"]
                ),
                "evaluation_numeric_metrics_wrong",
            )
            require(
                detail["quality_status"] == "failed"
                and detail["serving_eligible"] is False
                and detail["registered_model_version"] is None
                and detail["freshness"]["status"] == "unknown",
                "evaluation_quality_or_runtime_claim_fabricated",
            )
            require(
                "fixture-product-19" not in json.dumps(visible)
                and "source_uri" not in json.dumps(detail)
                and "receipt" not in json.dumps(detail),
                "evaluation_private_metadata_leaked",
            )
            require(
                http(uri + "?quality_status=passed")[1]["pagination"]["total"] == 0
                and http(uri + "?quality_status=failed")[1]["pagination"]["total"] == 1,
                "evaluation_status_filter_before_count_wrong",
            )
            require(
                http(uri + "?product_id=" + products[0], "http-all")[1]["pagination"]["total"] == 1,
                "evaluation_filter_relabelled_global_scope",
            )
            for bad in (
                "role=admin",
                "quality_status=done",
                "offset=257",
                "limit=201",
                "channel=store&channel=online",
                "product_id=outside",
            ):
                require(http(uri + "?" + bad)[0] == 422, "evaluation_invalid_query_allowed")
            sha = visible["view_sha256"]
            for i in range(33):
                reader.register(fixture(products[-1:], now, salt="outside-" + str(i)))
            require(http(uri)[1]["view_sha256"] == sha, "evaluation_outside_history_changed_view")
            second = fixture(products[:1], now, salt="second", status="not_ready")
            reader.register(second)
            require(http(uri + "?view_sha256=" + sha)[0] == 409, "evaluation_changed_view_allowed")
            first = http(uri + "?limit=1")[1]
            later = http(uri + "?limit=1&offset=1&view_sha256=" + first["view_sha256"])[1]
            require(
                first["pagination"]["total"] == 2
                and first["items"][0]["evaluation_id"] != later["items"][0]["evaluation_id"],
                "evaluation_stable_pages_wrong",
            )
            require(http(uri + "?offset=1")[0] == 409, "evaluation_unpinned_page_allowed")
            try:
                reader.register(one.model_copy(update={"evidence_sha256": "f" * 64}))
            except ValueError:
                pass
            else:
                raise ValueError("evaluation_invalid_evidence_registered")
            for sql in (
                "UPDATE ai.forecast_evaluations SET evidence=evidence",
                "DELETE FROM ai.forecast_evaluations",
            ):
                try:
                    with engine.begin() as conn:
                        conn.execute(text(sql))
                except IntegrityError:
                    pass
                else:
                    raise ValueError("evaluation_immutability_not_enforced")
            before = state(engine)
            with engine.connect() as conn:
                original = conn.scalar(
                    text("SELECT evidence FROM ai.forecast_evaluations WHERE evaluation_id=:id"),
                    {"id": one.descriptor.evaluation_id},
                )

            def replace(value: dict[str, Any]) -> None:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE ai.forecast_evaluations DISABLE TRIGGER USER"))
                    conn.execute(
                        text(
                            "UPDATE ai.forecast_evaluations SET evidence=CAST(:body AS jsonb) WHERE evaluation_id=:id"
                        ),
                        {"body": json.dumps(value), "id": one.descriptor.evaluation_id},
                    )
                    conn.execute(text("ALTER TABLE ai.forecast_evaluations ENABLE TRIGGER USER"))

            try:
                changed = json.loads(json.dumps(original))
                changed["descriptor"]["metrics"][0]["point"]["mae"] += 1
                replace(changed)
                status, invalid = http(uri)
                require(
                    status == 503
                    and invalid.get("code") == "evaluation-evidence-invalid"
                    and "metrics" not in json.dumps(invalid),
                    "evaluation_corrupt_metrics_served",
                )
            finally:
                replace(original)
            require(
                state(engine) == before and http(uri)[0] == 200,
                "evaluation_corruption_restore_failed",
            )
            checks.extend(
                [
                    "installed_http_auth_capability_closed_queries_and_status_filters",
                    "whole_report_scope_precedes_counts_and_global_metrics_partial_scope_404",
                    "numeric_mae_wape_reuse_existing_evaluator_with_failed_quality_and_no_serving_claim",
                    "33_outside_reports_do_not_change_view_and_scoped_pages_require_hash",
                    "idempotent_registration_invalid_identity_rejection_and_immutable_sql_rows",
                    "corrupt_point_metric_denominator_or_digest_refuses_entire_read_and_exact_restore",
                ]
            )
        historical_report = None
        if historical is not None:
            require(
                historical.descriptor.purpose == "historical_development_evidence",
                "historical_evaluation_purpose_required",
            )
            reader.register(historical)
            s = historical.descriptor.scope
            with server(
                s.product_ids, locations=s.selling_location_ids, channels=s.channels
            ) as http:
                status, result = http(uri + "/" + historical.descriptor.evaluation_id, "http-all")
                require(
                    status == 200
                    and result["quality_status"] == historical.descriptor.quality_status
                    and result["serving_eligible"] is False
                    and result["metrics"]
                    == [m.model_dump(mode="json") for m in historical.descriptor.metrics],
                    "historical_evaluation_http_mismatch",
                )
            historical_report = dict(
                evaluation_id=historical.descriptor.evaluation_id,
                evidence_sha256=historical.evidence_sha256,
                original_export_run_id=historical.descriptor.original_export_run_id,
                quality_status=historical.descriptor.quality_status,
                evaluation_membership_rows=historical.descriptor.evaluation_membership_rows,
                metrics=[m.model_dump(mode="json") for m in historical.descriptor.metrics],
            )
            checks.append(
                "verified_historical_export_scoped_http_preserves_replayed_metrics_and_original_quality_without_promotion"
            )
        report = state(engine)
        report.update(
            status="passed",
            checks=checks,
            real_http=True,
            synthetic_evaluations=37,
            historical_evaluation=historical_report,
            published_forecast_outputs=0,
            forecast_quality_approved=False,
            mlflow_calls=0,
            bedrock_calls=0,
        )
        print(json.dumps(report))
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
