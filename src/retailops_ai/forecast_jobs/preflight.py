"""Trusted database release lookup and bounded private preflight; no alias lookup or queue writes."""

import hashlib
import re
from typing import Any, Literal

from sqlalchemy import Engine

from retailops_ai.forecast_jobs.execution_contracts import RuntimeExecution, RuntimeResult
from retailops_ai.forecast_jobs.inputs import PreparedInputs
from retailops_ai.forecast_jobs.runtime import RuntimePin
from retailops_ai.forecast_jobs.supervisor import supervise
from retailops_ai.model_lifecycle.contracts import MODEL
from retailops_ai.model_lifecycle.journal import PostgresJournal
from retailops_ai.source_snapshot.protocol import resource_bytes


def run_preflight(
    engine: Engine,
    inputs: PreparedInputs,
    *,
    release_id: str,
    image_digest: str,
    environment: Literal["local", "test"],
    compose: bool,
) -> RuntimeResult:
    if re.fullmatch(r"model-release-sha256-[0-9a-f]{64}", release_id) is None:
        raise ValueError("runtime_preflight_release_id_invalid")
    journal = PostgresJournal(engine)
    with journal.locked(MODEL):
        release = journal.release(release_id)
    pin = RuntimePin(
        image_digest=image_digest,
        dependency_lock_sha256=hashlib.sha256(resource_bytes("dependencies.lock")).hexdigest(),
    )
    return supervise(
        RuntimeExecution(
            environment=environment,
            compose=compose,
            inputs=inputs,
            release=release,
            runtime_pin=pin,
        )
    )


def result_report(result: RuntimeResult) -> dict[str, Any]:
    return {
        "status": "passed",
        "purpose": result.purpose,
        "profile_id": result.profile_id,
        "release_id": result.release_id,
        "prediction_count": len(result.quantities),
        "predictions_sha256": result.quantities_sha256,
        "cold_load_seconds": result.cold_load_seconds,
        "compute_seconds": result.compute_seconds,
        "peak_rss_bytes": result.peak_rss_bytes,
        "published_forecast_outputs": 0,
    }
