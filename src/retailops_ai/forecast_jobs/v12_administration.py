"""Public projection over the private v12 queue, with verified publication references."""

import json
from typing import Protocol

from sqlalchemy import text

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs.contracts import BatchRequest
from retailops_ai.forecast_jobs.queue import checked
from retailops_ai.forecast_jobs.read_contracts import Pagination
from retailops_ai.forecast_jobs.reader import ForecastReadError
from retailops_ai.forecast_jobs.source_freshness import SourceFreshness
from retailops_ai.forecast_jobs.v12_batch import MAX_RECEIPT_BYTES, V12BatchReceipt, V12BatchRun
from retailops_ai.forecast_jobs.v12_job_contracts import (
    V12JobAttempts,
    V12JobModel,
    V12JobOutput,
    V12JobRun,
)
from retailops_ai.forecast_jobs.v12_publication import (
    MAX_OUTPUT_BYTES,
    V12Publication,
    verify_publication,
)
from retailops_ai.forecast_jobs.v12_queue import PostgresV12Queue, authorize_read
from retailops_ai.forecast_jobs.v12_reader import MAX_READ_BYTES


class V12JobAdministration(Protocol):
    def submit(self, request: BatchRequest, principal: Principal, key: str) -> V12JobRun: ...
    def get(self, run_id: str, principal: Principal) -> V12JobRun: ...
    def attempts(self, run_id: str, principal: Principal) -> V12JobAttempts: ...


def job_projection(
    run: V12BatchRun,
    principal: Principal,
    output: V12Publication | None = None,
    receipt: V12BatchReceipt | None = None,
) -> V12JobRun:
    run = V12BatchRun.model_validate_json(run.model_dump_json())
    authorize_read(run, principal)
    if (output is None) != (receipt is None):
        raise ValueError("v12_job_incomplete_publication")
    if output is not None and receipt is not None:
        verify_publication(output, run, receipt)
    binding = run.resolved_model
    return V12JobRun(
        run_id=run.run_id,
        status=run.status,
        attempt=run.attempt,
        requested_at=run.requested_at,
        requested_by=run.requested_by,
        started_at=run.started_at,
        completed_at=run.completed_at,
        input_ref=run.input_ref,
        resolved_model=V12JobModel(
            model_name=binding.model_name,
            model_version=binding.model_version,
            approval_sha256=binding.approval_sha256,
            runtime_pin_sha256=canonical_sha256(
                binding.approval.qualification.pin.model_dump(mode="json")
            ),
            approval_valid_until=binding.approval.qualification.valid_until,
        ),
        release_id=run.release_id,
        image_digest=run.image_digest,
        policy=run.policy,
        computation_receipt_id=run.output_id,
        output_ref=V12JobOutput(artifact_id=output.artifact_id) if output is not None else None,
        publication_status="published"
        if output is not None
        else "awaiting_publication"
        if run.status == "succeeded"
        else "not_computed",
        error=run.error,
    )


class PostgresV12JobAdministration:
    def __init__(self, queue: PostgresV12Queue) -> None:
        self.queue = queue

    def _project(self, run: V12BatchRun, principal: Principal) -> V12JobRun:
        authorize_read(run, principal)
        output = receipt = None
        if run.status == "succeeded":
            with self.queue.engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as conn:
                with conn.begin():
                    conn.execute(text("SET TRANSACTION READ ONLY"))
                    now = checked(conn)
                    conn.execute(text("SET LOCAL statement_timeout='3s'"))
                    header = conn.execute(
                        text("""
SELECT o.artifact_id, o.environment, o.model_name, octet_length(o.document::text) output_bytes,
       octet_length(c.receipt::text) receipt_bytes
FROM ai.v12_forecast_outputs o LEFT JOIN ai.v12_batch_receipts c USING(run_id)
WHERE o.run_id=:id
"""),
                        dict(id=run.run_id),
                    ).first()
                    if header is not None:
                        if header.receipt_bytes is None:
                            raise ValueError("v12_job_publication_receipt_missing")
                        if (
                            header.environment != self.queue.environment
                            or header.model_name != self.queue.model
                        ):
                            raise ValueError("v12_job_publication_namespace")
                        # JSONB text can be larger than canonical contract bytes due to whitespace.
                        if (
                            header.output_bytes > 2 * MAX_OUTPUT_BYTES
                            or header.receipt_bytes > 2 * MAX_RECEIPT_BYTES
                            or header.output_bytes + header.receipt_bytes > MAX_READ_BYTES
                        ):
                            raise ForecastReadError(429, "forecast-read-budget")
                        row = conn.execute(
                            text("""
SELECT o.document,c.receipt,p.profile->>'schema_version' input_version,
       p.profile->'source_freshness' input_freshness
FROM ai.v12_forecast_outputs o JOIN ai.v12_batch_receipts c USING(run_id)
JOIN ai.v12_batch_runs r USING(run_id)
JOIN ai.forecast_prepared_inputs p ON p.environment=o.environment AND p.profile_id=r.profile_id
WHERE o.artifact_id=:id
"""),
                            dict(id=header.artifact_id),
                        ).one()
                        output = V12Publication.model_validate_json(json.dumps(row.document))
                        receipt = V12BatchReceipt.model_validate_json(json.dumps(row.receipt))
                        source = SourceFreshness.model_validate_json(
                            json.dumps(row.input_freshness)
                        )
                        if (
                            output.artifact_id != header.artifact_id
                            or output.generated_at > now
                            or row.input_version != "1.1"
                            or source.scoped(output.scope) != output.source_freshness
                        ):
                            raise ValueError("v12_job_publication_identity")
        return job_projection(run, principal, output, receipt)

    def submit(self, request: BatchRequest, principal: Principal, key: str) -> V12JobRun:
        return self._project(self.queue.submit(request, principal, key), principal)

    def get(self, run_id: str, principal: Principal) -> V12JobRun:
        return self._project(self.queue.get(run_id, principal), principal)

    def attempts(self, run_id: str, principal: Principal) -> V12JobAttempts:
        current = self.queue.get(run_id, principal)
        runs = self.queue.attempts(run_id, principal)
        if len(runs) > current.policy.max_attempts or any(
            any(
                getattr(r, key) != getattr(current, key)
                for key in (
                    "run_id",
                    "requested_at",
                    "requested_by",
                    "input_ref",
                    "resolved_model",
                    "release_id",
                    "image_digest",
                    "environment",
                    "policy",
                )
            )
            for r in runs
        ):
            raise ValueError("v12_job_attempt_pin")
        items = tuple(self._project(r, principal) for r in runs)
        with self.queue.engine.begin() as conn:
            now = checked(conn)
        return V12JobAttempts(
            run_id=run_id,
            items=items,
            pagination=Pagination(limit=5, offset=0, total=len(items), next_offset=None),
            generated_at=now,
        )
