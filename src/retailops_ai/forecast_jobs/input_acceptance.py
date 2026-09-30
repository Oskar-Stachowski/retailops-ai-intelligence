"""Store races, rollback, immutable data and refusal in a fresh disposable database only."""

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from retailops_ai.config import load_settings
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecast_jobs.input_store import PostgresInputStore, RegistrationLimits
from retailops_ai.forecast_jobs.inputs import (
    MAX_INPUT_BYTES,
    InputContent,
    PreparedInputs,
    prepared,
)
from retailops_ai.model_lifecycle.acceptance import require
from retailops_ai.security.local import strict_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    if settings.app_env != "test" or settings.database_url is None or settings.image_digest is None:
        raise ValueError("input_acceptance_requires_disposable_test_database")
    engine = create_engine(
        settings.database_url.get_secret_value(), connect_args={"connect_timeout": 3}
    )
    store = PostgresInputStore(engine, "test")
    try:
        if args.inspect:
            with engine.connect() as connection:
                rows = (
                    connection.execute(
                        text(
                            "SELECT * FROM ai.forecast_prepared_inputs ORDER BY environment,profile_id"
                        )
                    )
                    .mappings()
                    .all()
                )
                snapshot = [
                    dict(row, registered_at=row["registered_at"].isoformat()) for row in rows
                ]
                require(len(rows) == 3, "registered_input_state_missing")
                require(
                    connection.scalar(text("SELECT count(*) FROM ai.forecast_batch_runs")) == 0,
                    "unexpected_forecast_run",
                )
                require(
                    connection.scalar(text("SELECT count(*) FROM ai.model_releases")) == 0,
                    "unexpected_model_release",
                )
            print(
                json.dumps(
                    {
                        "profiles": len(rows),
                        "state_sha256": canonical_sha256(snapshot),
                        "forecast_runs": 0,
                        "model_releases": 0,
                        "published_forecast_outputs": 0,
                    }
                )
            )
            return 0
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("input_acceptance_payload_limit")
        strict_json(raw)
        inputs = PreparedInputs.model_validate_json(raw)
        require(inputs.horizon_days == 14, "input_acceptance_requires_fourteen_day_package")
        with engine.connect() as connection:
            require(
                connection.scalar(text("SELECT count(*) FROM ai.forecast_prepared_inputs")) == 0,
                "input_acceptance_requires_empty_store",
            )
        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts = list(pool.map(lambda _: store.register(inputs), range(2)))
        require(receipts[0] == receipts[1], "registration_replay_changed_receipt")
        first = receipts[0]
        require(store.get(inputs.profile_id) == first, "registration_read_changed_data")
        checks = ["concurrent_replay_preserves_single_record_bytes_hash_and_database_timestamp"]

        other = PostgresInputStore(engine, "local")
        try:
            other.get(inputs.profile_id)
        except ValueError:
            pass
        else:
            raise ValueError("registered_inputs_crossed_environment")
        other.register(inputs)
        checks.append("local_and_test_registration_namespaces_are_independent")

        content = inputs.model_dump(mode="json", exclude={"profile_id"})
        content.update(horizon_days=7, rows=[r for r in content["rows"] if r["horizon_days"] <= 7])
        shorter = prepared(InputContent.model_validate_json(json.dumps(content)))
        # Private lower budgets exercise both pre-insert refusal and post-insert rollback.
        for limits in (
            RegistrationLimits(max_profiles=1),
            RegistrationLimits(
                max_storage_bytes=first.storage_bytes
                + len(canonical_bytes(shorter.model_dump(mode="json")))
            ),
        ):
            try:
                PostgresInputStore(engine, "test", limits).register(shorter)
            except ValueError as exc:
                require(
                    str(exc) == "prepared_inputs_capacity_exceeded",
                    "unexpected_registration_refusal",
                )
            else:
                raise ValueError("registered_input_budget_not_enforced")
            with engine.connect() as connection:
                require(
                    connection.scalar(
                        text(
                            "SELECT count(*) FROM ai.forecast_prepared_inputs WHERE environment='test'"
                        )
                    )
                    == 1,
                    "registration_budget_left_partial_write",
                )
        store.register(shorter)
        require(store.register(inputs) == first, "replay_at_capacity_changed_receipt")
        checks.append("profile_and_actual_jsonb_byte_budgets_refuse_without_partial_registration")

        for statement in (
            "UPDATE ai.forecast_prepared_inputs SET profile=profile",
            "UPDATE ai.forecast_prepared_inputs SET registered_at=registered_at",
            "DELETE FROM ai.forecast_prepared_inputs",
        ):
            try:
                with engine.begin() as connection:
                    connection.execute(text(statement))
            except IntegrityError:
                pass
            else:
                raise ValueError("immutable_registered_inputs_mutation_allowed")
        checks.append("database_blocks_input_and_registration_receipt_update_and_delete")

        child_env = {k: v for k, v in os.environ.items() if k in {"PATH", "LANG", "LC_ALL"}}
        child_env.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
        kernel_child = subprocess.run(  # noqa: S603 - fixed isolated executor with deliberately invalid input
            [sys.executable, "-I", "-m", "retailops_ai.forecast_jobs.runtime_executor"],
            input=b"{}",
            capture_output=True,
            timeout=10,
            check=False,
            env=child_env,
        )
        require(
            kernel_child.returncode == 2
            and kernel_child.stderr.strip() == b"forecast_runtime_input_invalid",
            "bounded_executor_failed_before_input_validation",
        )
        checks.append(
            "isolated_linux_executor_imports_under_kernel_limits_and_refuses_invalid_input"
        )

        # The real installed worker reads its registered profile, then refuses an absent release.
        env = dict(os.environ)
        result = subprocess.run(  # noqa: S603 - fixed private worker, disposable database
            [
                sys.executable,
                "-m",
                "retailops_ai.forecast_jobs.worker",
                "--preflight",
                "--profile-id",
                inputs.profile_id,
                "--release-id",
                "model-release-sha256-" + "0" * 64,
            ],
            capture_output=True,
            timeout=15,
            check=False,
            env=env,
        )
        require(
            result.returncode == 2
            and json.loads(result.stdout) == {"error": "forecast_worker_unavailable"},
            "worker_accepted_unapproved_release",
        )
        checks.append(
            "registered_input_worker_refuses_missing_approved_release_without_run_or_output"
        )
        with engine.connect() as connection:
            require(
                connection.scalar(text("SELECT count(*) FROM ai.forecast_batch_runs")) == 0,
                "preflight_created_forecast_run",
            )
        report: dict[str, Any] = {
            "status": "passed",
            "purpose": "verified_inputs_only",
            "profile_id": inputs.profile_id,
            "rows": len(inputs.rows),
            "histories": len(inputs.histories),
            "profile_sha256": first.profile_sha256,
            "storage_bytes": first.storage_bytes,
            "checks": checks,
            "qualified_release_loaded": False,
            "forecast_quality_approved": False,
            "published_forecast_outputs": 0,
        }
        print(json.dumps(report))
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
