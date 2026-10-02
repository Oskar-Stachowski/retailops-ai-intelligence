"""Bounded, JSON-only mechanics child. Qualified forecast execution belongs to AI 05.5."""

import hashlib
import json
import resource
import signal
import sys

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecast_jobs.contracts import BatchRun, MechanicsOutput, MechanicsProfile
from retailops_ai.forecast_jobs.queue import selected
from retailops_ai.model_lifecycle.mlflow import MLflowRegistry


def execute(run: BatchRun, profile: MechanicsProfile, *, compose: bool) -> MechanicsOutput:
    if run.environment != "test" or run.purpose != "lifecycle_mechanics_only":
        raise ValueError("mechanics_executor_requires_test")
    client = MLflowRegistry(compose=compose, environment="test")
    client.validate(run.resolved_model)
    raw_model = client.artifact(run.resolved_model.source_uri, "model.json")
    receipt = run.resolved_model.qualification.model
    if (
        len(raw_model) != receipt.size_bytes
        or hashlib.sha256(raw_model).hexdigest() != receipt.sha256
    ):
        raise ValueError("mechanics_loaded_model_checksum_mismatch")
    model = json.loads(raw_model)
    if model.get("format") != "mechanics-v1":
        raise ValueError("mechanics_model_required")
    raw = {
        "kind": "predictions",
        "complete": True,
        "purpose": "lifecycle_mechanics_only",
        "forecast_quality_approved": False,
        "run_id": run.run_id,
        "release_id": run.release_id,
        "profile_id": profile.profile_id,
        "predictions": [
            dict(
                r.model_dump(mode="json", exclude={"value"}),
                predicted_units=max(r.value + model["bias"], 0.0),
            )
            for r in selected(profile, run)
        ],
    }
    raw["artifact_id"] = "predictions-sha256-" + canonical_sha256(raw)
    return MechanicsOutput.model_validate_json(json.dumps(raw))


def main() -> int:
    # The supervisor supplies only immutable JSON pins, never a DB URL or lease token.
    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
    try:
        raw = sys.stdin.buffer.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise ValueError("mechanics_input_limit")
        payload = json.loads(raw)
        run = BatchRun.model_validate_json(json.dumps(payload["run"]))
        signal.alarm(run.policy.attempt_timeout_seconds + 5)
        profile = MechanicsProfile.model_validate_json(json.dumps(payload["profile"]))
        if profile.profile_id != run.input_ref.profile_id:
            raise ValueError("mechanics_profile_pin_mismatch")
        print(execute(run, profile, compose=payload["compose"]).model_dump_json())
        return 0
    except Exception:
        # No model paths, connection strings or raw exception text cross the process boundary.
        print("mechanics_execution_failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
