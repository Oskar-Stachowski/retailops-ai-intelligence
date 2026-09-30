"""Disposable SQL/worker transaction fixtures. Stub gates are NEVER model-quality evidence."""

import argparse
import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from psycopg.errors import CheckViolation
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from retailops_ai.config import load_settings
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs.contracts import BatchRequest, BatchScope, QueuePolicy
from retailops_ai.forecast_jobs.execution_contracts import RuntimeResult
from retailops_ai.forecast_jobs.input_store import PostgresInputStore
from retailops_ai.forecast_jobs.inputs import (
    MAX_INPUT_BYTES,
    InputContent,
    PreparedInputs,
    prepared,
    scoped_inputs,
)
from retailops_ai.forecast_jobs.publication import publication, receipt, scope_key
from retailops_ai.forecast_jobs.queue import (
    BatchError,
    Claim,
    LeaseLost,
    PostgresBatchQueue,
    checked,
    record,
)
from retailops_ai.forecast_jobs.worker import run_forecast_attempt
from retailops_ai.forecasting.features_contract import HistoryContext, InputRow
from retailops_ai.model_lifecycle.acceptance import require
from retailops_ai.model_lifecycle.contracts import (
    MODEL,
    Binding,
    Qualification,
    Release,
    release_for,
)
from retailops_ai.model_lifecycle.mechanics import capsule
from retailops_ai.security.local import strict_json

TABLES = (
    "forecast_prepared_inputs",
    "model_decisions",
    "model_steps",
    "model_versions",
    "model_releases",
    "model_heads",
    "forecast_batch_runs",
    "forecast_batch_attempts",
    "forecast_output_manifests",
    "forecast_output_partitions",
    "forecast_output_heads",
)


def expanded_fixture(inputs: PreparedInputs) -> PreparedInputs:
    """Twenty synthetic copies test the 256-row partition boundary; no lineage evidence."""
    histories = []
    rows = []
    products = tuple(f"fixture-product-{i:02}" for i in range(20))
    for product in products:
        history_raw = inputs.histories[0].model_dump(mode="json")
        history_raw["product_id"] = product
        for point in history_raw["points"]:
            point["product_id"] = product
        history = HistoryContext.model_validate_json(json.dumps(history_raw))
        histories.append(history)
        for original in inputs.rows:
            raw = original.model_dump(mode="json")
            raw.update(product_id=product, history_context_sha256=history.content_sha256())
            rows.append(InputRow.model_validate_json(json.dumps(raw)))
    return prepared(
        InputContent(
            feature_manifest=inputs.feature_manifest,
            as_of_time=inputs.as_of_time,
            scope=BatchScope(
                product_ids=products,
                selling_location_ids=inputs.scope.selling_location_ids,
                channel=inputs.scope.channel,
            ),
            horizon_days=14,
            rows=tuple(rows),
            histories=tuple(histories),
        )
    )


def stub_release(inputs: PreparedInputs, image: str, version: str = "1") -> Release:
    """SQL-only stub. No MLflow version, model file, real gates or promotion is created."""
    qualification, _ = capsule(1.0, "sql-publication-fixture-only")
    raw = qualification.model_dump(mode="json")
    raw.update(
        purpose="qualified_forecast",
        original_run_kind="historical_evidence",
        model_family="baseline",
        flavor="baseline-json-v1",
        dependency_lock_sha256=inputs.feature_manifest.descriptor.code.dependency_lock_sha256,
    )
    q = Qualification.model_validate_json(json.dumps(raw))
    binding = Binding(
        model_name="retailops-demand-forecast",
        model_version=version,
        mlflow_run_id="a" * 32,
        source_uri="mlflow-artifacts:/1/" + "a" * 32 + "/artifacts/sql-fixture-no-model",
        qualification_sha256=hashlib.sha256(q.model_dump_json().encode()).hexdigest(),
        qualification=q,
    )
    return release_for(
        decision_id="decision-sql-fixture-only-" + version,
        binding=binding.model_dump(mode="json"),
        image_digest=image,
        previous_release_id=None,
        previous_version=None,
        restored_from_release_id=None,
    )


def seed_stub(engine: Engine, release: Release) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO ai.model_decisions(decision_id,model_name,request_sha256,record) VALUES (:id,:name,:sha,CAST(:record AS jsonb))"
            ),
            {
                "id": release.decision_id,
                "name": MODEL,
                "sha": "a" * 64,
                "record": json.dumps(
                    {
                        "request": {"decision_id": release.decision_id, "model_name": MODEL},
                        "purpose": "sql_fixture_only",
                    }
                ),
            },
        )
        connection.execute(
            text(
                "INSERT INTO ai.model_steps(decision_id,phase,record) VALUES (:id,'completed','{}'::jsonb)"
            ),
            {"id": release.decision_id},
        )
        connection.execute(
            text(
                "INSERT INTO ai.model_versions(model_name,model_version,binding,decision_id) VALUES (:name,:version,CAST(:binding AS jsonb),:id)"
            ),
            {
                "name": MODEL,
                "version": release.binding.model_version,
                "binding": release.binding.model_dump_json(),
                "id": release.decision_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO ai.model_releases(release_id,model_name,model_version,release,decision_id) VALUES (:release,:name,:version,CAST(:record AS jsonb),:id)"
            ),
            {
                "release": release.release_id,
                "name": MODEL,
                "version": release.binding.model_version,
                "record": release.model_dump_json(),
                "id": release.decision_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO ai.model_heads(model_name,release_id) VALUES (:name,:id) ON CONFLICT(model_name) DO UPDATE SET release_id=EXCLUDED.release_id"
            ),
            {"name": MODEL, "id": release.release_id},
        )


def result_for(claim: Claim) -> RuntimeResult:
    if not isinstance(claim.profile, PreparedInputs):
        raise ValueError("fixture_requires_prepared_inputs")
    inputs = scoped_inputs(
        claim.profile, claim.run.input_ref.scope, max(claim.run.input_ref.request.horizons_days)
    )
    values = tuple(2.0 for _ in inputs.rows)
    return RuntimeResult(
        purpose="qualified_forecast_computation",
        profile_id=inputs.profile_id,
        release_id=claim.run.release_id,
        quantities=values,
        quantities_sha256=canonical_sha256(values),
        cold_load_seconds=0.0,
        compute_seconds=0.0,
        peak_rss_bytes=1024,
    )


def snapshot(engine: Engine) -> dict[str, Any]:
    with engine.connect() as connection:
        checked(connection)
        rows = {
            table: connection.scalars(
                text(f"SELECT to_jsonb(t) FROM ai.{table} t ORDER BY to_jsonb(t)::text")  # noqa: S608 - closed table allowlist
            ).all()
            for table in TABLES
        }  # noqa: S608 - closed table allowlist
    return {
        "counts": {table: len(values) for table, values in rows.items()},
        "state_sha256": canonical_sha256(rows),
        "heads_sha256": canonical_sha256(rows["forecast_output_heads"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    if settings.app_env != "test" or settings.database_url is None or settings.image_digest is None:
        raise ValueError("publication_acceptance_requires_disposable_test_database")
    engine = create_engine(
        settings.database_url.get_secret_value(), connect_args={"connect_timeout": 3}
    )
    try:
        if args.inspect:
            print(json.dumps(snapshot(engine)))
            return 0
        require(
            all(count == 0 for count in snapshot(engine)["counts"].values()),
            "publication_acceptance_requires_empty_database",
        )
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        strict_json(raw)
        original = PreparedInputs.model_validate_json(raw)
        inputs = expanded_fixture(original)
        store = PostgresInputStore(engine, "test")
        store.register(inputs)
        actor = Principal(
            "fixture-pipeline",
            frozenset({"pipeline"}),
            frozenset({"forecast:run"}),
            frozenset(inputs.scope.product_ids),
            frozenset(inputs.scope.selling_location_ids),
            frozenset({inputs.scope.channel}),
        )
        request = BatchRequest(
            profile_id=inputs.profile_id, as_of=inputs.as_of_time, channel=inputs.scope.channel
        )
        queue = PostgresBatchQueue(
            engine,
            "test",
            QueuePolicy(
                lease_seconds=30,
                heartbeat_seconds=0.5,
                attempt_timeout_seconds=60,
                run_timeout_seconds=300,
                retry_backoff_seconds=0,
            ),
            mechanics=False,
        )
        checks = []
        try:
            queue.submit(request, actor, "absent-model")
        except BatchError as exc:
            require(exc.code == "model-not-approved", "unexpected_admission_refusal")
        else:
            raise ValueError("qualified_admission_without_release")
        checks.append("registered_inputs_without_approved_release_create_no_run")
        release = stub_release(inputs, settings.image_digest)
        seed_stub(engine, release)
        with ThreadPoolExecutor(max_workers=2) as pool:
            runs = list(pool.map(lambda _: queue.submit(request, actor, "first"), range(2)))
        require(runs[0] == runs[1], "qualified_replay_created_duplicate")
        claim = queue.claim()
        require(claim is not None, "qualified_claim_missing")
        if claim is None:
            raise ValueError("qualified_claim_missing")
        seed_stub(engine, stub_release(inputs, settings.image_digest, "2"))
        require(
            claim.release == release
            and queue.submit(request, actor, "first")
            == claim.run.model_copy(update={"status": "running"}),
            "release_pin_changed",
        )
        # A manifest without a successful run/history cannot commit, even through raw SQL.
        if not isinstance(claim.profile, PreparedInputs):
            raise ValueError("qualified_profile_missing")
        with engine.connect() as connection:
            now = connection.scalar(text("SELECT clock_timestamp()"))
        output = publication(claim.run, claim.profile, result_for(claim), now)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO ai.forecast_output_manifests(artifact_id,run_id,environment,scope_key,manifest) VALUES (:id,:run,'test',:scope,CAST(:manifest AS jsonb))"
                    ),
                    {
                        "id": output.manifest.artifact_id,
                        "run": claim.run.run_id,
                        "scope": scope_key(output.manifest.scope, 14),
                        "manifest": output.manifest.model_dump_json(),
                    },
                )
        except IntegrityError as exc:
            require(
                isinstance(exc.orig, CheckViolation)
                and exc.orig.diag.message_primary == "forecast_output_requires_success_and_history",
                "unexpected_orphan_manifest_refusal",
            )
        else:
            raise ValueError("partial_manifest_committed")
        # Even a raw SQL success/history update cannot commit an incomplete dataset.
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO ai.forecast_output_manifests(artifact_id,run_id,environment,scope_key,manifest) VALUES (:id,:run,'test',:scope,CAST(:manifest AS jsonb))"
                    ),
                    {
                        "id": output.manifest.artifact_id,
                        "run": claim.run.run_id,
                        "scope": scope_key(output.manifest.scope, 14),
                        "manifest": output.manifest.model_dump_json(),
                    },
                )
                part = output.partitions[0]
                connection.execute(
                    text(
                        "INSERT INTO ai.forecast_output_partitions(artifact_id,ordinal,partition,sha256) VALUES (:id,:ordinal,CAST(:partition AS jsonb),:sha)"
                    ),
                    {
                        "id": output.manifest.artifact_id,
                        "ordinal": part.ordinal,
                        "partition": part.model_dump_json(),
                        "sha": receipt(part).sha256,
                    },
                )
                raw_run = claim.run.model_dump(mode="json")
                raw_run.update(
                    status="succeeded",
                    completed_at=connection.scalar(text("SELECT clock_timestamp()")).isoformat(),
                    output_ref={
                        "kind": "predictions",
                        "artifact_id": output.manifest.artifact_id,
                        "complete": True,
                    },
                )
                done = record(raw_run)
                queue._write(connection, done)
                queue._history(connection, done, "fixture_incomplete_dataset")
        except IntegrityError as exc:
            require(
                isinstance(exc.orig, CheckViolation)
                and exc.orig.diag.message_primary == "forecast_output_incomplete_grain_or_receipts",
                "unexpected_incomplete_dataset_refusal",
            )
        else:
            raise ValueError("incomplete_success_committed")
        checks.append(
            "sql_deferred_constraint_rolls_back_success_and_history_for_incomplete_partitions"
        )
        queue.complete_forecast(claim, result_for(claim))
        first = snapshot(engine)
        require(first["counts"]["forecast_output_partitions"] == 2, "bounded_partitions_missing")
        checks.extend(
            [
                "concurrent_admission_is_idempotent_and_release_remains_pinned_after_head_change",
                "sql_deferred_constraint_refuses_orphan_manifest",
                "complete_manifest_two_partitions_run_history_and_pointer_commit_together",
            ]
        )

        queued = queue.submit(request, actor, "second")
        failed_claim = queue.claim()
        require(
            failed_claim is not None and failed_claim.run.run_id == queued.run_id,
            "second_claim_missing",
        )
        if failed_claim is None:
            raise ValueError("second_claim_missing")

        def inject_failure(
            conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
        ) -> None:
            if (
                "INSERT INTO ai.forecast_output_partitions" in statement
                and parameters.get("ordinal") == 1
            ):
                raise RuntimeError("injected_second_partition_failure")

        event.listen(engine, "before_cursor_execute", inject_failure)
        try:
            queue.complete_forecast(failed_claim, result_for(failed_claim))
        except RuntimeError:
            pass
        else:
            raise ValueError("partition_failure_not_injected")
        finally:
            event.remove(engine, "before_cursor_execute", inject_failure)
        require(
            snapshot(engine)["counts"]["forecast_output_manifests"] == 1
            and snapshot(engine)["counts"]["forecast_output_partitions"] == 2,
            "partial_output_survived_rollback",
        )
        queue.fail(failed_claim, reason="injected_partition_failure", retryable=True)
        require(
            snapshot(engine)["heads_sha256"] == first["heads_sha256"],
            "failed_run_removed_previous_output",
        )
        retry = queue.claim()
        if retry is None:
            raise ValueError("retry_claim_missing")
        require(
            retry.run.attempt == 2 and retry.run.release_id == failed_claim.run.release_id,
            "retry_changed_pins",
        )
        try:
            queue.complete_forecast(failed_claim, result_for(failed_claim))
        except LeaseLost:
            pass
        else:
            raise ValueError("stale_claim_published")
        queue.complete_forecast(retry, result_for(retry))
        checks.extend(
            [
                "second_partition_failure_rolls_back_all_output_and_retains_previous_pointer",
                "bounded_retry_keeps_pins_and_stale_token_cannot_publish",
            ]
        )

        queue.submit(request, actor, "race-complete")
        concurrent = queue.claim()
        if concurrent is None:
            raise ValueError("concurrent_claim_missing")

        def complete(_: int) -> str:
            try:
                queue.complete_forecast(concurrent, result_for(concurrent))
                return "succeeded"
            except LeaseLost:
                return "lease_lost"

        with ThreadPoolExecutor(max_workers=2) as pool:
            require(
                sorted(pool.map(complete, range(2))) == ["lease_lost", "succeeded"],
                "duplicate_publication_race",
            )
        checks.append("concurrent_completion_commits_exactly_one_output")

        # Complete an earlier request after a later one; preserve the later pointer.
        queue.submit(request, actor, "ordered-older")
        older = queue.claim()
        queue.submit(request, actor, "ordered-newer")
        newer = queue.claim()
        if older is None or newer is None:
            raise ValueError("ordered_claim_missing")
        queue.complete_forecast(newer, result_for(newer))
        latest = snapshot(engine)["heads_sha256"]
        queue.complete_forecast(older, result_for(older))
        require(
            snapshot(engine)["heads_sha256"] == latest, "late_old_request_replaced_newer_pointer"
        )
        checks.append("late_older_request_cannot_displace_newer_successful_pointer")

        queue.submit(request, actor, "expires-during-publication")
        expiring = queue.claim()
        if expiring is None:
            raise ValueError("expiring_claim_missing")
        before_expiry = snapshot(engine)

        def expire_after_first(
            conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
        ) -> None:
            if (
                "INSERT INTO ai.forecast_output_partitions" in statement
                and parameters.get("ordinal") == 0
            ):
                conn.execute(
                    text(
                        "UPDATE ai.forecast_batch_runs SET lease_expires=clock_timestamp()+interval '50 milliseconds' WHERE run_id=:id"
                    ),
                    {"id": expiring.run.run_id},
                )
                time.sleep(0.1)

        event.listen(engine, "after_cursor_execute", expire_after_first)
        try:
            queue.complete_forecast(expiring, result_for(expiring))
        except LeaseLost:
            pass
        else:
            raise ValueError("expired_publication_committed")
        finally:
            event.remove(engine, "after_cursor_execute", expire_after_first)
        require(snapshot(engine) == before_expiry, "expired_publication_left_partial_state")
        queue.cancel(expiring.run.run_id)
        checks.append("lease_expiry_after_first_insert_rolls_back_all_publication_state")

        request7 = request.model_copy(
            update={"product_ids": (inputs.scope.product_ids[0],), "horizons_days": (7,)}
        )
        queue.submit(request7, actor, "numeric-refusal")
        refused = queue.claim()
        if refused is None:
            raise ValueError("numeric_claim_missing")
        before = snapshot(engine)
        status = run_forecast_attempt(
            queue, refused, compose=True, image_digest=settings.image_digest
        )
        require(status == "failed", "unqualified_registry_fixture_entered_runtime")
        require(
            snapshot(engine)["counts"]["forecast_output_manifests"]
            == before["counts"]["forecast_output_manifests"],
            "refused_runtime_published",
        )
        checks.append(
            "installed_numeric_child_refuses_missing_real_registry_evidence_and_publishes_nothing"
        )
        for table in ("forecast_output_manifests", "forecast_output_partitions"):
            for operation in ("UPDATE", "DELETE"):
                statement = (
                    f"UPDATE ai.{table} SET artifact_id=artifact_id"  # noqa: S608 - closed table allowlist
                    if operation == "UPDATE"
                    else f"DELETE FROM ai.{table}"  # noqa: S608 - closed table allowlist
                )  # noqa: S608 - closed allowlist
                try:
                    with engine.begin() as connection:
                        connection.execute(text(statement))
                except IntegrityError:
                    pass
                else:
                    raise ValueError("forecast_output_mutation_allowed")
        checks.append("database_blocks_published_manifest_and_partition_mutation")
        print(
            json.dumps(
                {
                    "status": "passed",
                    "purpose": "publication_transaction_fixture_only",
                    "forecast_quality_approved": False,
                    "published_forecast_outputs": 0,
                    "fixture_output_manifests": snapshot(engine)["counts"][
                        "forecast_output_manifests"
                    ],
                    "checks": checks,
                    **snapshot(engine),
                }
            )
        )
        return 0
    except SQLAlchemyError as exc:
        marker = type(exc.orig).__name__.lower() if hasattr(exc, "orig") else "sqlalchemy_error"
        raise RuntimeError("publication_database_" + marker) from None
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
