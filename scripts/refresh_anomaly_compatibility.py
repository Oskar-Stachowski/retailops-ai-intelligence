"""Renew only current compatibility after replaying the immutable model and frozen evaluation."""

import argparse
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from retailops_ai.anomaly_evaluation.verification import verify_quality
from retailops_ai.anomaly_portfolio.artifacts import immutable
from retailops_ai.anomaly_portfolio.lifecycle_contract import Qualification
from retailops_ai.anomaly_portfolio.model import load
from retailops_ai.anomaly_portfolio.registry import AnomalyRegistry
from retailops_ai.source_snapshot.files import canonical_json, decode_json, read_bytes
from retailops_ai.source_snapshot.protocol import resource_bytes


def refresh(source: Path, output: Path) -> dict[str, Any]:
    original = Qualification.model_validate_json(
        read_bytes(source, "qualification.json", 8 * 1024**2)
    )
    artifacts = {}
    for name, receipt in (
        ("model.json", original.model),
        ("config.json", original.config),
        ("signature.json", original.signature),
        ("input_example.json", original.input_example),
        *(("gate_" + k + ".json", g.report) for k, g in original.gates.items()),
    ):
        raw = read_bytes(source, name, 8 * 1024**2)
        if (len(raw), hashlib.sha256(raw).hexdigest()) != (receipt.size_bytes, receipt.sha256):
            raise ValueError("anomaly_compatibility_original_checksum")
        artifacts[name] = raw
    if (
        original.dependency_lock_sha256
        != hashlib.sha256(resource_bytes("dependencies.lock")).hexdigest()
    ):
        raise ValueError("anomaly_compatibility_dependency_lock_changed")
    AnomalyRegistry.smoke(original, artifacts)
    final = decode_json(artifacts["gate_segments.json"])
    model = load(source / "model.json", original.model.sha256)
    for name in final["evaluation_inputs"]:
        artifacts[name] = read_bytes(source, name, 8 * 1024**2)
    verify_quality(
        final,
        decode_json(artifacts["config.json"]),
        lambda name: artifacts[name],
        model.detector_id,
        original.model_family,
        model,
    )
    report = decode_json(artifacts["gate_freshness_drift_compatibility.json"])
    if (
        report["reference_id"] != original.reference_id
        or report["model_sha256"] != original.model.sha256
    ):
        raise ValueError("anomaly_compatibility_reference_binding")
    now = datetime.now(UTC)
    report.update(
        {
            "checked_at": now.isoformat(),
            "valid_until": (now + timedelta(days=7)).isoformat(),
            "schema_compatible": True,
            "model_load_predict": "verified",
            "six_frozen_case_saved_prediction_replay": "passed",
            "dependency_lock": "unchanged",
            "renewed_from_qualification_sha256": hashlib.sha256(
                read_bytes(source, "qualification.json", 8 * 1024**2)
            ).hexdigest(),
            "original_fit_times": "unchanged",
            "frozen_model_and_policy": "unchanged",
            "drift_monitoring": "not_attested",
            "deployment_attestation": "not_attested",
        }
    )
    raw = canonical_json(report) + b"\n"
    artifacts["gate_freshness_drift_compatibility.json"] = raw
    changed = original.model_dump(mode="json")
    changed["gates"]["freshness_drift_compatibility"]["report"] = {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size_bytes": len(raw),
    }
    qualification = Qualification.model_validate_json(json.dumps(changed))
    artifacts["qualification.json"] = canonical_json(qualification.model_dump(mode="json")) + b"\n"
    artifacts["model_card.json"] = read_bytes(source, "model_card.json", 8 * 1024**2)
    for name, value in artifacts.items():
        immutable(output / name, value, maximum=8 * 1024**2)
    return {
        "status": "passed",
        "qualification_sha256": hashlib.sha256(artifacts["qualification.json"]).hexdigest(),
        "model_sha256": original.model.sha256,
        "original_fit_times": "unchanged",
        "capsule": str(output),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capsule", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(refresh(args.capsule, args.output)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
