"""Private idempotent publication of already successful, immutable v12 computations."""

import json

from sqlalchemy import text

from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs.queue import BatchError, checked, clock
from retailops_ai.forecast_jobs.v12_batch import V12BatchReceipt
from retailops_ai.forecast_jobs.v12_publication import (
    V12Publication,
    publication,
    verify_publication,
)
from retailops_ai.forecast_jobs.v12_queue import PostgresV12Queue, record
from retailops_ai.model_lifecycle.v12_registry import MLflowV12Registry


class PostgresV12Publisher:
    def __init__(self, queue: PostgresV12Queue, registry: MLflowV12Registry) -> None:
        self.queue, self.registry = queue, registry

    def publish(self, run_id: str, principal: Principal) -> V12Publication:
        if "pipeline" not in principal.roles or "forecast:run" not in principal.capabilities:
            raise BatchError(403, "forecast-run-denied")
        run = self.queue.get(run_id, principal)
        if run.requested_by != principal.principal_id:
            raise BatchError(404, "forecast-run-not-found")
        receipt = self.queue.output(run_id, principal)
        with self.queue.engine.begin() as connection:
            checked(connection)
            # Publication is separate from computation, globally serialized and all-or-nothing.
            connection.execute(text("SELECT pg_advisory_xact_lock(505070)"))
            current = record(
                connection.scalar(
                    text(
                        "SELECT record FROM ai.v12_batch_runs WHERE run_id=:id AND environment=:env FOR UPDATE"
                    ),
                    dict(id=run_id, env=self.queue.environment),
                )
            )
            stored = V12BatchReceipt.model_validate_json(
                json.dumps(
                    connection.scalar(
                        text("SELECT receipt FROM ai.v12_batch_receipts WHERE run_id=:id"),
                        dict(id=run_id),
                    )
                )
            )
            if current != run or stored != receipt:
                raise ValueError("v12_publication_computation_changed")
            existing = connection.scalar(
                text("SELECT document FROM ai.v12_forecast_outputs WHERE run_id=:id"),
                dict(id=run_id),
            )
            if existing is not None:
                result = V12Publication.model_validate_json(json.dumps(existing))
                verify_publication(result, run, receipt)
                return result
            # The same model lock used by lifecycle prevents a concurrent decision across HTTP checks.
            self.queue._guard(connection, receipt.release)
            active = connection.scalar(
                text(
                    "SELECT r.release FROM ai.v12_model_heads h JOIN ai.v12_model_releases r USING(release_id) WHERE h.model_name=:model"
                ),
                dict(model=self.queue.model),
            )
            if active is None:
                raise ValueError("v12_publication_no_model_head")
            aliases = self.registry.aliases(self.queue.model)
            if (
                aliases.get("champion") != active["binding"]["model_version"]
                or aliases.get("rollback") != active["previous_version"]
            ):
                raise ValueError("v12_publication_registry_head_disagreement")
            self.registry.validate(run.resolved_model)
            self.queue._guard(connection, receipt.release)
            profile = self.queue._profile(connection, run.input_ref.profile_id)
            result = publication(run, profile, receipt, clock(connection))
            self.queue._guard(connection, receipt.release)
            connection.execute(
                text(
                    "INSERT INTO ai.v12_forecast_outputs(artifact_id,run_id,environment,model_name,document) VALUES (:id,:run,:env,:model,CAST(:document AS jsonb))"
                ),
                dict(
                    id=result.artifact_id,
                    run=run_id,
                    env=self.queue.environment,
                    model=self.queue.model,
                    document=result.model_dump_json(),
                ),
            )
            return result
