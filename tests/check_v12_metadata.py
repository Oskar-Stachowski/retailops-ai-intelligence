"""Disposable PostgreSQL v12 catalog/evaluation HTTP, immutable storage and restart."""

import json
import os
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from test_access import bearer, policy_file, problem
from test_v12_metadata import viewer
from v12_evaluation_fixture import evaluation_fixture

from retailops_ai.api.app import create_app
from retailops_ai.config import Settings
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs.input_store import PostgresInputStore
from retailops_ai.model_lifecycle import v12_catalog, v12_evaluation_store
from retailops_ai.model_lifecycle.evaluation_store import EvaluationError
from retailops_ai.model_lifecycle.read_contracts import CatalogQuery, CatalogScope
from retailops_ai.model_lifecycle.reader import CatalogError
from retailops_ai.model_lifecycle.v12_catalog import PostgresV12Catalog
from retailops_ai.model_lifecycle.v12_evaluation_store import PostgresV12Evaluations
from retailops_ai.model_lifecycle.v12_lifecycle_contracts import TEST_MODEL
from retailops_ai.model_lifecycle.v12_metadata_contracts import V12EvaluationQuery


def test_native_metadata_storage_scope_http_and_restart(tmp_path, monkeypatch):
    invocation = os.environ.get("AI05_V12_PRIVATE_INVOCATION")
    if not invocation:
        pytest.fail("Task-owned disposable runner required", pytrace=False)
    fd = os.open(invocation, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        assert (
            stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and not info.st_mode & 0o077
        )
        control = json.loads(stream.read(16384))
    engine = create_engine(control["database_url"], hide_parameters=True)
    state_file = Path(control["state_file"])
    try:
        with engine.connect() as conn:
            assert (
                conn.scalar(
                    text("SELECT value FROM ai.service_metadata WHERE name='v12_acceptance_owner'")
                )
                == control["owner"]
            )
        state = json.loads(state_file.read_bytes())
        profile = PostgresInputStore(engine, "test").get(state["queue_profile_id"]).inputs
        catalog_viewer = Principal(
            "local-viewer",
            frozenset({"viewer"}),
            frozenset({"forecast:read"}),
            frozenset(profile.scope.product_ids[:1]),
            frozenset(profile.scope.selling_location_ids),
            frozenset({"store"}),
        )
        catalog = PostgresV12Catalog(engine, "test", mechanics=True)
        reports = PostgresV12Evaluations(engine, "test", mechanics=True)
        if os.environ.get("AI05_V12_RESTART_INSPECT") == "1":
            assert (
                catalog.models(CatalogQuery(), catalog_viewer).view_sha256
                == state["catalog_view_sha256"]
            )
            assert (
                reports.evaluations(V12EvaluationQuery(), viewer()).view_sha256
                == state["evaluation_view_sha256"]
            )
            detail = reports.evaluation(state["evaluation_id"], CatalogScope(), viewer())
            assert (
                canonical_sha256([s.model_dump(mode="json") for s in detail.segments])
                == state["evaluation_segments_sha256"]
            )
            state["checks"].append(
                "v12_catalog_original_evaluations_and_scoped_views_survive_restart"
            )
            state["status"] = "passed"
            state_file.write_bytes(canonical_bytes(state) + b"\n")
            return

        model_page = catalog.models(CatalogQuery(), catalog_viewer)
        assert model_page.pagination.total == 1
        model = model_page.items[0]
        assert model.visible_version_count == 1 and model.approved_release is None
        assert model.deployment_status == "not_attested" and model.registry_aliases is None
        versions = catalog.versions(TEST_MODEL, CatalogQuery(), catalog_viewer)
        assert (
            len(versions.items) == 1 and versions.items[0].model_version != state["model_version"]
        )
        assert versions.items[0].status == "previously_published"
        assert catalog.model(TEST_MODEL, CatalogScope(), catalog_viewer).model_dump(
            exclude={"freshness", "generated_at"}
        ) == model.model_dump(exclude={"freshness", "generated_at"})
        assert (
            PostgresV12Catalog(engine, "test").models(CatalogQuery(), catalog_viewer).data_status
            == "no_data"
        )
        with pytest.raises(CatalogError, match="model-not-found"):
            catalog.model("not-this-model", CatalogScope(), catalog_viewer)
        with monkeypatch.context() as patch:
            patch.setattr(v12_catalog, "MAX_READ_BYTES", 1)
            with pytest.raises(CatalogError, match="model-read-budget"):
                catalog.models(CatalogQuery(), catalog_viewer)

        catalog_http = tmp_path / "catalog-http"
        catalog_http.mkdir()

        def catalog_grant(value):
            value["grants"][0]["scope"] = dict(
                product_ids=sorted(catalog_viewer.product_ids),
                selling_location_ids=sorted(catalog_viewer.selling_location_ids),
                channels=["store"],
            )

        policy, tokens = policy_file(catalog_http, catalog_grant)
        with TestClient(
            create_app(
                Settings(APP_ENV="test", ARTIFACT_ROOT="./artifacts", API_AUTH_FILE=policy),
                v12_model_catalog=catalog,
            ),
            base_url="http://127.0.0.1",
        ) as client:
            uri = "/api/v1/models/v12"
            headers = bearer(tokens["local-viewer"])
            for path in (uri, uri + "/" + TEST_MODEL, uri + "/" + TEST_MODEL + "/versions"):
                result = client.get(path, headers=headers)
                assert result.status_code == 200
                for private in ("source_uri", "run_dir", "release_dir", "artifact_uri"):
                    assert private not in result.text
                problem(client.get(path), 401)
                problem(client.get(path, headers=bearer(tokens["local-admin"])), 403)
            with engine.connect() as conn:
                original = conn.scalar(
                    text("SELECT document FROM ai.v12_forecast_outputs WHERE run_id=:id"),
                    dict(id=state["queue_run_id"]),
                )
            changed = json.loads(json.dumps(original))
            changed["rows"][-1]["prediction"]["candidate"]["mean"] = 12345
            changed["predictions_sha256"] = canonical_sha256(
                [
                    r["prediction"]
                    for r in sorted(changed["rows"], key=lambda r: r["prediction"]["key"])
                ]
            )
            changed["artifact_id"] = "v12-forecasts-sha256-" + canonical_sha256(
                {k: v for k, v in changed.items() if k != "artifact_id"}
            )

            def replace_output(document):
                with engine.begin() as conn:
                    conn.exec_driver_sql(
                        "ALTER TABLE ai.v12_forecast_outputs DISABLE TRIGGER v12_forecast_immutable"
                    )
                    conn.execute(
                        text(
                            "UPDATE ai.v12_forecast_outputs SET artifact_id=:id,document=CAST(:body AS jsonb) WHERE run_id=:run"
                        ),
                        dict(
                            id=document["artifact_id"],
                            body=json.dumps(document),
                            run=state["queue_run_id"],
                        ),
                    )
                    conn.exec_driver_sql(
                        "ALTER TABLE ai.v12_forecast_outputs ENABLE TRIGGER v12_forecast_immutable"
                    )

            try:
                replace_output(changed)
                problem(client.get(uri, headers=headers, params=dict(limit=1)), 503)
            finally:
                replace_output(original)

        operator = Principal(
            "unit-promoter",
            frozenset({"promoter"}),
            frozenset({"model:decide"}),
            frozenset(),
            frozenset(),
            frozenset(),
        )
        _, evidence = evaluation_fixture(tmp_path / "not-ready-export", monkeypatch)
        _, passed = evaluation_fixture(tmp_path / "passed-export", monkeypatch, passed=True)
        assert reports.register(evidence, operator) == evidence
        assert reports.register(evidence, operator) == evidence
        reports.register(passed, operator)
        with pytest.raises(ValueError, match="operator_required"):
            reports.register(evidence, viewer())
        assert reports.evaluations(V12EvaluationQuery(), viewer()).pagination.total == 2
        assert reports.evaluations(V12EvaluationQuery(), viewer(narrow=True)).pagination.total == 0
        assert (
            reports.evaluations(V12EvaluationQuery(product_id="p-101"), viewer()).pagination.total
            == 0
        )
        assert (
            reports.evaluations(
                V12EvaluationQuery(quality_status="not_ready"), viewer()
            ).pagination.total
            == 1
        )
        assert (
            PostgresV12Evaluations(engine, "test")
            .evaluations(V12EvaluationQuery(), viewer())
            .data_status
            == "no_data"
        )
        with pytest.raises(EvaluationError, match="evaluation-not-found"):
            reports.evaluation(evidence.evaluation_id, CatalogScope(), viewer(narrow=True))
        first = reports.evaluations(V12EvaluationQuery(limit=1), viewer())
        second = reports.evaluations(
            V12EvaluationQuery(limit=1, offset=1, view_sha256=first.view_sha256), viewer()
        )
        assert first.items[0].evaluation_id != second.items[0].evaluation_id
        detail = reports.evaluation(evidence.evaluation_id, CatalogScope(), viewer())
        assert [s.model_dump(mode="json") for s in detail.segments] == evidence.original_metrics[
            "segments"
        ]
        for sql in (
            "UPDATE ai.v12_evaluations SET evidence=evidence",
            "DELETE FROM ai.v12_evaluations",
        ):
            with pytest.raises(IntegrityError):
                with engine.begin() as conn:
                    conn.exec_driver_sql(sql)
        with monkeypatch.context() as patch:
            patch.setattr(v12_evaluation_store, "MAX_READ_BYTES", 1)
            with pytest.raises(EvaluationError, match="evaluation-read-budget"):
                reports.evaluations(V12EvaluationQuery(), viewer())
            # Unauthorized reports must not count toward even a one-byte read budget.
            assert (
                reports.evaluations(V12EvaluationQuery(), viewer(narrow=True)).pagination.total == 0
            )

        def mutate(value):
            value["grants"][0]["scope"]["product_ids"] = ["p-101", "p-202"]

        policy, tokens = policy_file(tmp_path, mutate)
        with TestClient(
            create_app(
                Settings(APP_ENV="test", ARTIFACT_ROOT="./artifacts", API_AUTH_FILE=policy),
                v12_model_catalog=catalog,
                v12_evaluation_reader=reports,
            ),
            base_url="http://127.0.0.1",
        ) as client:
            uri = "/api/v1/evaluations/v12/" + evidence.evaluation_id
            headers = bearer(tokens["local-viewer"])
            assert (
                client.get(uri, headers=headers).json()["segments"]
                == evidence.original_metrics["segments"]
            )
            problem(client.get(uri), 401)
            problem(client.get(uri, headers=bearer(tokens["local-admin"])), 403)
            problem(client.get(uri, headers=headers, params=dict(product_id="p-101")), 404)
            changed = evidence.model_dump(mode="json")
            changed["original_metrics"]["segments"][0]["candidate"]["mean"]["mse"] = 15.0
            changed["evidence_sha256"] = canonical_sha256(
                {k: v for k, v in changed.items() if k != "evidence_sha256"}
            )

            def replace(document):
                with engine.begin() as conn:
                    conn.exec_driver_sql(
                        "ALTER TABLE ai.v12_evaluations DISABLE TRIGGER v12_evaluation_immutable"
                    )
                    conn.execute(
                        text(
                            "UPDATE ai.v12_evaluations SET evidence_sha256=:sha,evidence=CAST(:body AS jsonb) WHERE evaluation_id=:id"
                        ),
                        dict(
                            sha=document["evidence_sha256"],
                            body=json.dumps(document),
                            id=evidence.evaluation_id,
                        ),
                    )
                    conn.exec_driver_sql(
                        "ALTER TABLE ai.v12_evaluations ENABLE TRIGGER v12_evaluation_immutable"
                    )

            try:
                replace(changed)
                problem(client.get(uri, headers=headers), 503)
                problem(
                    client.get("/api/v1/evaluations/v12", headers=headers, params=dict(limit=1)),
                    503,
                )
            finally:
                replace(evidence.model_dump(mode="json"))
        state["checks"].extend(
            [
                "v12_catalog_only_includes_verified_complete_scoped_publication_and_old_immutable_model_pin",
                "v12_catalog_authenticated_http_rejects_rehashed_corruption_outside_visible_scope",
                "v12_catalog_and_evaluations_never_attest_live_aliases_or_deployment",
                "v12_evaluation_scope_includes_excluded_product_and_is_filtered_before_counts_and_byte_budget",
                "v12_evaluation_immutable_idempotent_sql_and_authenticated_read_only_http",
                "v12_evaluation_preserves_original_failed_not_ready_nullable_mean_median_interval_and_lineage",
                "v12_evaluation_rehashed_metric_corruption_outside_first_page_is_rejected_against_original_receipt",
                "v12_catalog_and_evaluation_mechanics_hidden_from_default_production_namespace",
            ]
        )
        state.update(
            catalog_view_sha256=model_page.view_sha256,
            evaluation_view_sha256=first.view_sha256,
            evaluation_id=evidence.evaluation_id,
            evaluation_segments_sha256=canonical_sha256(
                [s.model_dump(mode="json") for s in detail.segments]
            ),
            recorded_fixture_evaluations=2,
            real_v12_evaluations_recorded=0,
            mlflow_test_metadata_backend="sqlite",
            ai_test_database_backend="postgresql",
            status="restart_pending",
        )
        state_file.write_bytes(canonical_bytes(state) + b"\n")
    finally:
        engine.dispose()
