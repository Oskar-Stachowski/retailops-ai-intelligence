"""Owned real PostgreSQL queue/publication/read/backup; synthetic model mechanics only."""

import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from check_stockout_lifecycle import owned_control
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from test_stockout_batch import backend as backend
from test_stockout_batch import conditional as conditional
from test_stockout_batch import context as context
from test_stockout_batch import job as job
from test_stockout_batch import records as records
from test_stockout_batch import source as source

from retailops_ai.adapters.database import EXPECTED_REVISION
from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.domain.access import Principal, StockoutAccess
from retailops_ai.forecast_jobs.contracts import QueuePolicy
from retailops_ai.forecast_jobs.queue import LeaseLost
from retailops_ai.stockout_jobs.batch import compute
from retailops_ai.stockout_jobs.contracts import StockoutRequest
from retailops_ai.stockout_jobs.input_store import StockoutError
from retailops_ai.stockout_jobs.queue import PostgresStockoutQueue
from retailops_ai.stockout_jobs.read_contracts import StockoutQuery
from retailops_ai.stockout_jobs.reader import PostgresStockoutReader
from retailops_ai.stockout_lifecycle.contract import TEST_MODEL
from retailops_ai.stockout_lifecycle.journal import PostgresStockoutJournal


def pipeline(inputs, *, foreign=False):
    return Principal(
        "stockout-test-pipeline",
        frozenset({"pipeline"}),
        frozenset({"stockout:read", "stockout:run"}),
        frozenset(),
        frozenset(),
        frozenset(),
        stockout=StockoutAccess(
            frozenset({"outside"}) if foreign else frozenset(inputs.scope.product_ids),
            frozenset(inputs.scope.stock_location_ids),
        ),
    )


def test_real_stockout_queue_fences_complete_publication_and_scoped_reads(request):
    control = owned_control()
    engine = create_engine(
        control["database_url"], hide_parameters=True, connect_args={"connect_timeout": 3}
    )
    state_file = Path(control["state_file"] + ".stockout-jobs.json")
    try:
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT value FROM ai.service_metadata WHERE name='v12_acceptance_owner'")
                )
                == control["owner"]
            )
            assert (
                connection.scalar(text("SELECT version_num FROM ai.alembic_version"))
                == EXPECTED_REVISION
            )
        queue = PostgresStockoutQueue(engine, "test", mechanics=True)
        reader = PostgresStockoutReader(engine, "test", mechanics=True)
        journal = PostgresStockoutJournal(engine)
        if os.environ.get("AI05_V12_RESTART_INSPECT") == "1":
            state = json.loads(state_file.read_bytes())
            from retailops_ai.stockout_runtime.inputs import PreparedStockoutInputs

            inputs = PreparedStockoutInputs.model_validate_json(
                canonical_bytes(state["input_fixture"])
            )
            principal = pipeline(inputs)
            output = queue.output(state["run_id"], principal)
            assert output.output_id == state["output_id"]
            assert queue.get(state["run_id"], principal).status == "succeeded"
            page = reader.list(StockoutQuery(inference_run_id=state["run_id"]), principal)
            assert len(page.items) == len(output.items)
            assert page.items[0].release_id == state["release_id"]
            assert page.items[0].quality_status == "mechanics_only"
            assert len(queue.attempts(state["run_id"], principal)) == 1
            state["checks"].append(
                "real_restart_or_full_backup_restore_preserves_inputs_run_history_output_and_scoped_read"
            )
            state["status"] = "passed"
            state_file.write_bytes(canonical_bytes(state) + b"\n")
            return
        _, inputs, _, _ = request.getfixturevalue("job")
        principal = pipeline(inputs)
        with journal.locked(TEST_MODEL):
            release = journal.active(TEST_MODEL)
            assert release is not None
        with engine.begin() as connection:
            # Explicit synthetic input mechanics: this does not claim source-parent qualification.
            connection.execute(
                text(
                    "INSERT INTO ai.stockout_prepared_inputs(environment,inputs_id,registered_by,inputs) VALUES ('test',:id,:principal,CAST(:inputs AS jsonb))"
                ),
                dict(
                    id=inputs.inputs_id,
                    principal=principal.principal_id,
                    inputs=inputs.model_dump_json(),
                ),
            )
        body = StockoutRequest(profile_id=inputs.inputs_id, as_of=inputs.as_of, scope=inputs.scope)
        run = queue.submit(body, principal, "stockout-durable-one")
        assert queue.submit(body, principal, "stockout-durable-one") == run
        with pytest.raises(StockoutError, match="stockout-idempotency-conflict"):
            queue.submit(
                body.model_copy(update={"profile_id": "stockout-inputs-sha256-" + "f" * 64}),
                principal,
                "stockout-durable-one",
            )
        with pytest.raises(StockoutError, match="stockout-run-not-found"):
            queue.get(run.run_id, pipeline(inputs, foreign=True))
        with pytest.raises(StockoutError, match="stockout-scope-denied"):
            queue.submit(body, pipeline(inputs, foreign=True), "outside")
        assert reader.list(StockoutQuery(), principal).data_status == "no_data"
        claim = queue.claim(release_id=release.release_id)
        assert claim is not None and claim.run.run_id == run.run_id
        assert queue.claim(release_id=release.release_id) is None
        assert claim.run.release == release
        queue.heartbeat(claim)
        assert 0 < queue.execution_budget(claim) <= 120
        with pytest.raises(LeaseLost):
            queue.heartbeat(replace(claim, token=str(uuid4())))
        output = compute(claim.run, claim.profile, claim.release, generated_at=datetime.now(UTC))
        with pytest.raises(ValueError):
            queue.complete(claim, output.model_copy(update={"items": ()}))
        assert queue.get(run.run_id, principal).status == "running"
        assert reader.list(StockoutQuery(), principal).data_status == "no_data"
        with pytest.raises(IntegrityError, match="stockout_batch_completion_requires_history"):
            with engine.begin() as connection:
                raw = claim.run.model_dump(mode="json")
                raw.update(
                    status="succeeded",
                    completed_at=datetime.now(UTC).isoformat(),
                    output_id=output.output_id,
                )
                connection.execute(
                    text(
                        "UPDATE ai.stockout_batch_runs SET record=CAST(:record AS jsonb),lease_token=NULL,lease_expires=NULL,attempt_deadline=NULL WHERE run_id=:run"
                    ),
                    dict(record=json.dumps(raw), run=run.run_id),
                )
        queue.complete(claim, output)
        done = queue.get(run.run_id, principal)
        assert done.status == "succeeded" and queue.output(run.run_id, principal) == output
        assert len(queue.attempts(run.run_id, principal)) == 1
        with pytest.raises(LeaseLost):
            queue.complete(claim, output)
        page = reader.list(StockoutQuery(), principal)
        assert len(page.items) == len(inputs.points)
        assert page.items[0].freshness_status == "stale"
        assert page.items[0].quality_status == "mechanics_only"
        assert reader.get(page.items[0].risk_id, principal).risk_id == page.items[0].risk_id
        foreign_page = reader.list(StockoutQuery(), pipeline(inputs, foreign=True))
        assert foreign_page.items == ()
        with pytest.raises(StockoutError, match="stockout-risk-not-found"):
            reader.get(page.items[0].risk_id, pipeline(inputs, foreign=True))
        with pytest.raises(StockoutError, match="stockout-view-changed"):
            reader.list(StockoutQuery(offset=1, view_sha256="f" * 64), principal)
        for statement in (
            "UPDATE ai.stockout_prepared_inputs SET inputs=inputs",
            "UPDATE ai.stockout_batch_attempts SET record=record",
            "UPDATE ai.stockout_batch_outputs SET output=output",
        ):
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.exec_driver_sql(statement)
        # Retryable failed attempts close durably before a new attempt. Old lease is fenced.
        retry_queue = PostgresStockoutQueue(
            engine, "test", QueuePolicy(retry_backoff_seconds=0), mechanics=True
        )
        retry_run = retry_queue.submit(body, principal, "stockout-durable-retry")
        first = retry_queue.claim(release_id=release.release_id)
        assert first is not None and first.run.run_id == retry_run.run_id
        retry_queue.fail(first, reason="explicit_test_failure", retryable=True)
        second = retry_queue.claim(release_id=release.release_id)
        assert second is not None and second.run.attempt == 2 and second.token != first.token
        with pytest.raises(LeaseLost):
            retry_queue.complete(
                first,
                compute(first.run, first.profile, first.release, generated_at=datetime.now(UTC)),
            )
        retry_queue.cancel(retry_run.run_id)
        assert [a.status for a in retry_queue.attempts(retry_run.run_id, principal)] == [
            "failed",
            "cancelled",
        ]
        # Default production queue never admits the private mechanics model.
        with pytest.raises(StockoutError, match="stockout-model-not-approved"):
            PostgresStockoutQueue(engine, "test").submit(body, principal, "production-guard")
        state_file.write_bytes(
            canonical_bytes(
                dict(
                    version="stockout-postgres-jobs-acceptance-1.0.0",
                    status="restart_pending",
                    purpose="synthetic_stockout_jobs_mechanics_only",
                    run_id=run.run_id,
                    output_id=output.output_id,
                    release_id=release.release_id,
                    input_fixture=inputs.model_dump(mode="json"),
                    migration_revision=EXPECTED_REVISION,
                    checks=[
                        "real_sql_idempotency_and_physical_scope_authorization",
                        "real_sql_lease_heartbeat_and_old_attempt_fencing",
                        "atomic_output_success_and_history_no_partial_publication",
                        "scoped_latest_risk_and_stable_pagination",
                        "append_only_inputs_attempts_and_outputs",
                        "retry_then_cancel_records_closed_attempts",
                        "production_rejects_mechanics_namespace",
                    ],
                    real_postgres=True,
                    real_mlflow_stockout_registry=False,
                    independent_quality_accepted=False,
                    final_test_outcomes_evaluated=False,
                    production_model_promoted=False,
                    ai08_ready=False,
                )
            )
            + b"\n"
        )
    finally:
        engine.dispose()
