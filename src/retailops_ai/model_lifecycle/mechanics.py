"""Explicit test capsule; never an approval of forecast model quality."""

import hashlib
import json
from datetime import UTC, datetime, timedelta

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.model_lifecycle.contracts import GATES, Qualification


def capsule(bias: float, evidence_id: str) -> tuple[Qualification, dict[str, bytes]]:
    def raw(value: object) -> bytes:
        return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()

    def receipt(value: bytes) -> dict[str, object]:
        return {"sha256": hashlib.sha256(value).hexdigest(), "size_bytes": len(value)}

    now = datetime.now(UTC)
    files = {
        "model.json": raw({"format": "mechanics-v1", "bias": bias}),
        "signature.json": raw({"input": "finite_scalar", "output": "nonnegative_units"}),
        "input_example.json": raw([0.0, 1.0, -2.0]),
        "config.json": raw({"purpose": "lifecycle_mechanics_only", "bias": bias}),
    }
    model_sha = hashlib.sha256(files["model.json"]).hexdigest()
    for gate in sorted(GATES):
        report: dict[str, object] = {
            "gate": gate,
            "status": "passed",
            "evidence_id": evidence_id,
            "model_sha256": model_sha,
            "purpose": "lifecycle_mechanics_only",
        }
        if gate == "freshness_drift_compatibility":
            report.update(
                reference_id="mechanics-reference-v1",
                valid_until=(now + timedelta(days=1)).isoformat(),
            )
        files["gate_" + gate + ".json"] = raw(report)
    qualification = Qualification.model_validate_json(
        json.dumps(
            {
                "purpose": "lifecycle_mechanics_only",
                "evidence_id": evidence_id,
                "evaluation_id": "mechanics-evaluation-v1",
                "reference_id": "mechanics-reference-v1",
                "original_run_kind": "mechanics_fixture",
                "original_started_at": now.isoformat(),
                "original_completed_at": now.isoformat(),
                "feature_set_id": "features-sha256-" + "a" * 64,
                "feature_schema_version": "forecast-features-v1",
                "config_sha256": hashlib.sha256(files["config.json"]).hexdigest(),
                "config": receipt(files["config.json"]),
                "source_dataset_id": "source-sha256-" + "a" * 64,
                "curated_dataset_id": "curated-sha256-" + "b" * 64,
                "label_dataset_id": "labels-sha256-" + "c" * 64,
                "split_id": "split-sha256-" + "d" * 64,
                "source_code_commit": "c" * 40,
                "ai_code_commit": "d" * 40,
                "dependency_lock_sha256": "e" * 64,
                "model_seed": 42,
                "data_seed": 42,
                "model_family": "mechanics",
                "flavor": "mechanics-json-v1",
                "model": receipt(files["model.json"]),
                "signature": receipt(files["signature.json"]),
                "input_example": receipt(files["input_example.json"]),
                "expected_output_sha256": canonical_sha256(
                    [max(x + bias, 0.0) for x in [0.0, 1.0, -2.0]]
                ),
                "gates": {
                    gate: {"status": "passed", "report": receipt(files["gate_" + gate + ".json"])}
                    for gate in GATES
                },
            }
        )
    )
    files["qualification.json"] = (qualification.model_dump_json() + "\n").encode()
    return qualification, files
