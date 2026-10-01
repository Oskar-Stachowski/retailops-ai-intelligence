"""Task-owned real PostgreSQL/MLflow publication and ASGI HTTP/restart acceptance."""

import json
import os
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from test_access import bearer, policy_file, problem
from test_v12_queue import actor

from retailops_ai.api.app import create_app
from retailops_ai.config import Settings
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs import v12_reader
from retailops_ai.forecast_jobs.input_store import PostgresInputStore
from retailops_ai.forecast_jobs.read_contracts import ForecastQuery
from retailops_ai.forecast_jobs.reader import ForecastReadError
from retailops_ai.forecast_jobs.v12_output_store import PostgresV12Publisher
from retailops_ai.forecast_jobs.v12_queue import PostgresV12Queue
from retailops_ai.forecast_jobs.v12_reader import PostgresV12ForecastReader
from retailops_ai.model_lifecycle.v12_registry import MLflowV12Registry


def test_atomic_publication_scoped_http_and_restart(tmp_path, monkeypatch):
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
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT value FROM ai.service_metadata WHERE name='v12_acceptance_owner'")
                )
                == control["owner"]
            )
        state = json.loads(state_file.read_bytes())
        profile = PostgresInputStore(engine, "test").get(state["queue_profile_id"]).inputs
        principal = actor(profile)
        queue = PostgresV12Queue(engine, "test", mechanics=True)
        reader = PostgresV12ForecastReader(engine, "test", mechanics=True)
        viewer = Principal(
            "local-viewer",
            frozenset({"viewer"}),
            frozenset({"forecast:read"}),
            frozenset(profile.scope.product_ids[:1]),
            frozenset(profile.scope.selling_location_ids),
            frozenset({"store"}),
        )
        if os.environ.get("AI05_V12_RESTART_INSPECT") == "1":
            result = reader.read(
                ForecastQuery(inference_run_id=state["queue_run_id"], limit=200), viewer
            )
            assert result.pagination.total == 28
            assert result.view_sha256 == state["publication_view_sha256"]
            assert {r.prediction_dataset_id for r in result.items} == {state["publication_id"]}
            assert (
                canonical_sha256([r.prediction.model_dump(mode="json") for r in result.items])
                == state["publication_visible_functionals_sha256"]
            )
            state["checks"].append(
                "v12_published_functionals_scoped_view_and_immutable_receipt_survive_postgresql_mlflow_restart"
            )
            state["status"] = "passed"
            state_file.write_bytes(canonical_bytes(state) + b"\n")
            return
        registry = MLflowV12Registry(environment="test", port=control["mlflow_port"])
        publisher = PostgresV12Publisher(queue, registry)
        run_id = state["queue_run_id"]
        # A complete computation must remain private until all publication checks succeed.
        assert reader.read(ForecastQuery(), viewer).data_status == "no_data"
        with monkeypatch.context() as patch:
            patch.setattr(type(registry), "aliases", lambda *_: {})
            with pytest.raises(ValueError, match="registry_head_disagreement"):
                publisher.publish(run_id, principal)

        def crash_after_insert(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("INSERT INTO ai.v12_forecast_outputs"):
                raise RuntimeError("explicit_test_publication_post_insert_crash")

        event.listen(engine, "after_cursor_execute", crash_after_insert)
        try:
            with pytest.raises(RuntimeError, match="post_insert_crash"):
                publisher.publish(run_id, principal)
        finally:
            event.remove(engine, "after_cursor_execute", crash_after_insert)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM ai.v12_forecast_outputs")) == 0
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM ai.v12_batch_receipts WHERE run_id=:id"),
                    dict(id=run_id),
                )
                == 1
            )
        output = publisher.publish(run_id, principal)
        assert publisher.publish(run_id, principal) == output
        assert len(output.rows) == 560
        with pytest.raises(ValueError, match="complete_receipt_required"):
            publisher.publish(
                state["queue_cancelled_run_id"],
                actor(
                    PostgresInputStore(engine, "test")
                    .get(state["queue_cancelled_profile_id"])
                    .inputs
                ),
            )
        for sql in (
            "UPDATE ai.v12_forecast_outputs SET document=document",
            "DELETE FROM ai.v12_forecast_outputs",
        ):
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.exec_driver_sql(sql)
        partial = output.model_dump(mode="json")
        partial["rows"].pop()
        partial["artifact_id"] = "v12-forecasts-sha256-" + "b" * 64
        with pytest.raises(IntegrityError, match="incomplete_or_changed_functionals"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO ai.v12_forecast_outputs(artifact_id,run_id,environment,model_name,document) VALUES (:id,:run,'test',:model,CAST(:doc AS jsonb))"
                    ),
                    dict(
                        id=partial["artifact_id"],
                        run=run_id,
                        model=queue.model,
                        doc=json.dumps(partial),
                    ),
                )
        result = reader.read(ForecastQuery(inference_run_id=run_id, limit=200), viewer)
        assert (
            result.pagination.total == 28
            and {r.product_id for r in result.items} == viewer.product_ids
        )
        original = {r.prediction.key: r.prediction for r in output.rows}
        assert all(original[r.prediction.key] == r.prediction for r in result.items)
        assert (
            PostgresV12ForecastReader(engine, "test").read(ForecastQuery(), viewer).data_status
            == "no_data"
        )
        with monkeypatch.context() as patch:
            patch.setattr(v12_reader, "MAX_READ_BYTES", 1)
            with pytest.raises(ForecastReadError, match="read-budget"):
                reader.read(ForecastQuery(), viewer)

        def mutate(value):
            value["grants"][0]["scope"] = dict(
                product_ids=list(viewer.product_ids),
                selling_location_ids=list(viewer.selling_location_ids),
                channels=["store"],
            )

        policy, tokens = policy_file(tmp_path, mutate)
        with TestClient(
            create_app(
                Settings(APP_ENV="test", ARTIFACT_ROOT="./artifacts", API_AUTH_FILE=policy),
                v12_forecast_reader=reader,
            ),
            base_url="http://127.0.0.1",
        ) as client:
            uri = "/api/v1/forecasts/v12"
            headers = bearer(tokens["local-viewer"])
            problem(client.get(uri), 401)
            problem(client.get(uri, headers=bearer(tokens["local-admin"])), 403)
            first = client.get(uri, headers=headers, params=dict(limit=3))
            assert first.status_code == 200 and first.json()["pagination"]["total"] == 28
            assert (
                client.get(
                    uri,
                    headers=headers,
                    params=dict(limit=3, offset=3, view_sha256=first.json()["view_sha256"]),
                ).status_code
                == 200
            )
            problem(
                client.get(
                    uri, headers=headers, params=dict(product_id=profile.scope.product_ids[-1])
                ),
                422,
            )
            # Corruption outside the visible scope/page must fail the entire matching publication.
            corrupt = output.model_dump(mode="json")
            corrupt["rows"][-1]["prediction"]["candidate"]["mean"] = 12345
            corrupt["predictions_sha256"] = canonical_sha256(
                [
                    r["prediction"]
                    for r in sorted(corrupt["rows"], key=lambda r: r["prediction"]["key"])
                ]
            )
            corrupt["artifact_id"] = "v12-forecasts-sha256-" + canonical_sha256(
                {k: v for k, v in corrupt.items() if k != "artifact_id"}
            )

            def replace_document(document):
                with engine.begin() as connection:
                    connection.exec_driver_sql(
                        "ALTER TABLE ai.v12_forecast_outputs DISABLE TRIGGER v12_forecast_immutable"
                    )
                    connection.execute(
                        text(
                            "UPDATE ai.v12_forecast_outputs SET artifact_id=:id,document=CAST(:doc AS jsonb) WHERE run_id=:run"
                        ),
                        dict(id=document["artifact_id"], doc=json.dumps(document), run=run_id),
                    )
                    connection.exec_driver_sql(
                        "ALTER TABLE ai.v12_forecast_outputs ENABLE TRIGGER v12_forecast_immutable"
                    )

            try:
                replace_document(corrupt)
                problem(client.get(uri, headers=headers, params=dict(limit=1)), 503)
            finally:
                replace_document(output.model_dump(mode="json"))
        state["checks"].extend(
            [
                "v12_publication_registry_disagreement_and_post_insert_crash_leave_no_partial_output",
                "v12_atomic_idempotent_publication_preserves_560_exact_functional_rows_and_old_model_pin",
                "v12_sql_refuses_partial_publication_and_output_mutation_or_deletion",
                "v12_scoped_authenticated_asgi_http_pagination_and_pre_fetch_byte_budget",
                "v12_read_rejects_rehashed_functional_corruption_outside_visible_scope_and_page",
                "v12_mechanics_outputs_are_invisible_to_default_production_reader",
            ]
        )
        state.update(
            publication_id=output.artifact_id,
            publication_view_sha256=result.view_sha256,
            publication_visible_functionals_sha256=canonical_sha256(
                [r.prediction.model_dump(mode="json") for r in result.items]
            ),
            published_fixture_forecast_outputs=1,
            published_fixture_rows=560,
            published_forecast_outputs=0,
            persistent_source_stack_changed=False,
            status="restart_pending",
        )
        state_file.write_bytes(canonical_bytes(state) + b"\n")
    finally:
        engine.dispose()
