"""Seal a real final evaluation and saved model into a bounded MLflow qualification capsule."""

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from retailops_ai.anomaly_detectors.protocol import Scope, Window, series_key
from retailops_ai.anomaly_evaluation.contract import Decision
from retailops_ai.anomaly_evaluation.verification import verify_quality
from retailops_ai.anomaly_portfolio.artifacts import immutable, immutable_json
from retailops_ai.anomaly_portfolio.cli import prepared
from retailops_ai.anomaly_portfolio.lifecycle_contract import Qualification
from retailops_ai.anomaly_portfolio.model import load, row, score
from retailops_ai.anomaly_portfolio.registry import AnomalyRegistry
from retailops_ai.model_lifecycle.contracts import GATES
from retailops_ai.qualified_anomalies.contract import Point
from retailops_ai.source_snapshot.files import canonical_json, decode_json, json_sha256, read_bytes

ROOT = Path(__file__).resolve().parents[1]


def document(path: Path) -> dict[str, Any]:
    return decode_json(read_bytes(path.parent, path.name, 8 * 1024**2))


def receipt(raw: bytes) -> dict[str, Any]:
    return {"size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def committed_code(commit: str) -> str:
    # Verify the actual fit fingerprint against an immutable checkout, not today's HEAD.
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        raise ValueError("anomaly_capsule_commit")
    hashes = {}
    executable = shutil.which("git")
    if executable is None:
        raise ValueError("anomaly_capsule_git_missing")
    for package in ("anomaly_portfolio", "anomaly_detectors", "qualified_anomalies"):
        directory = ROOT / "src/retailops_ai" / package
        for path in directory.glob("*.py"):
            result = subprocess.run(  # noqa: S603 - fixed git, validated commit and repository path
                [executable, "show", commit + ":" + str(path.relative_to(ROOT))],
                cwd=ROOT,
                check=True,
                capture_output=True,
            )
            hashes[package + "/" + path.name] = hashlib.sha256(result.stdout).hexdigest()
    return json_sha256(hashes)


def build(args: argparse.Namespace) -> dict[str, Any]:
    final = document(args.final / "final_quality.json")
    frozen = final["selection"]["descriptor"]
    fit = document(args.fit_record)
    model_raw = read_bytes(args.model.parent, args.model.name, 8 * 1024**2)
    model = load(args.model, frozen["model_sha256"])
    if (
        hashlib.sha256(canonical_json(fit) + b"\n").hexdigest() != frozen["fit_record_sha256"]
        or model.descriptor.code_sha256 != committed_code(args.ai_commit)
        or model.descriptor.dependency_lock_sha256
        != hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest()
        or (fit["original_started_at"], fit["original_completed_at"])
        != (frozen["original_started_at"], frozen["original_completed_at"])
    ):
        raise ValueError("anomaly_capsule_original_fit_binding")
    audit = document(args.security_audit)
    if (
        audit["status"] != "passed"
        or audit["secret_scan"]["exit_code"] != 0
        or audit["dependency_lock_sha256"] != model.descriptor.dependency_lock_sha256
        or not audit["license_inventory"]
        or audit["license_policy"]["status"] != "passed"
    ):
        raise ValueError("anomaly_capsule_security_license_unqualified")
    reference = "anomaly-reference-" + json_sha256(
        {"selection": final["selection"], "feature_schema": Point.model_json_schema()}
    )
    config = {
        "version": "anomaly-serving-config-1.0.0",
        "selection": final["selection"],
        "reference_id": reference,
        "truth_access": "excluded_from_model_and_batch",
        "serving": "pinned_saved_artifact_read_only",
    }
    quality = verify_quality(
        final,
        config,
        lambda name: read_bytes(args.final, name, 8 * 1024**2),
        model.detector_id,
        frozen["family"],
        model,
    )
    inventory = document(args.inventory)["cases"]
    first = next(e for e in inventory if (e["seed"], e["scenario"]) == (42, "demand"))
    frame = prepared(Path(first["prepared_receipt"]))
    points = list(frame.points())
    point = next(
        p
        for p in points
        if model.descriptor.validation.start <= p.business_date <= model.descriptor.validation.end
        and row(p) is not None
    )
    scope = Scope.model_validate({k: getattr(point, k) for k in Scope.model_fields})
    window = Window(start=point.business_date, end=point.business_date)
    smoke_points = [
        p
        for p in points
        if series_key(p) == series_key(point)
        and point.business_date - timedelta(days=6) <= p.business_date <= point.business_date
    ]
    example = {
        "points": [p.model_dump(mode="json") for p in smoke_points],
        "scopes": [scope.model_dump(mode="json")],
        "window": window.model_dump(mode="json"),
        "as_of": model.descriptor.selection_cutoff.isoformat(),
    }
    output_sha = json_sha256(
        [
            d.model_dump(mode="json")
            for d in score(
                model,
                smoke_points,
                (scope,),
                window,
                frozen["family"],
                "validation",
                model.descriptor.selection_cutoff,
            )
        ]
    )
    signature = {
        "point_schema_sha256": json_sha256(Point.model_json_schema()),
        "feature_schema_version": "qualified-anomaly-inputs-1.0.0",
        "detector_id": model.detector_id,
        "family": frozen["family"],
        "output_schema_sha256": json_sha256(Decision.model_json_schema()),
    }
    resources = fit["resources"]
    policy = model.descriptor.policy
    if not resources or any(
        r["cpu_seconds"] > policy.fit_cpu_seconds
        or r["wall_seconds"] > policy.fit_wall_seconds
        or r["peak_rss_bytes"] > policy.fit_rss_bytes
        or r["native_max_score_error"] > 1e-10
        for r in resources
    ):
        raise ValueError("anomaly_capsule_native_fit_resources")
    preparations = [document(Path(e["prepared_receipt"])) for e in inventory]
    if len(preparations) != 6 or any(
        p["status"] != "passed" or p["truth_access"] != "excluded" for p in preparations
    ):
        raise ValueError("anomaly_capsule_native_preparations")
    snapshot = document(Path(first["prepared_receipt"]))
    public = document(Path(snapshot["parents"][3]) / "snapshot/snapshot_manifest.json")["source"]
    source_commit = public["provenance"]["git_commit"]
    if public["provenance"]["code_state"] != "clean":
        raise ValueError("anomaly_capsule_uncommitted_source")
    evidence_id = "anomaly-evidence-" + json_sha256(
        {"model": frozen["model_sha256"], "selection": final["selection"]["selection_id"]}
    )
    card = {
        "model_family": frozen["family"],
        "detector_id": model.detector_id,
        "selection_reason": frozen["selection_reason"],
        "scope": frozen["quality_policy"]["qualification_scope"],
        "metrics": quality["descriptor"],
        "features": policy.features,
        "original_fit": fit,
        "limits": [
            "Synthetic portfolio: 128 days, 8 products, one selling location, store and online.",
            "V2 uses supply-adequate inventory and a declared whole-process demand density of 0.75.",
            "At least three final episodes per type; estimates have small-sample uncertainty.",
            "Scores describe observations and do not establish causes or intent.",
            "Daily evidence has a causal 24h sale / 72h return origin; no realtime detection claim.",
            "Historical July inputs are stale at the October evaluation date.",
            "Offline replay only; broker ACK/DLQ durability and business projections belong to AI10.",
        ],
    }
    previous_freshness = args.output / "gate_freshness_drift_compatibility.json"
    now = (
        datetime.fromisoformat(document(previous_freshness)["checked_at"])
        if previous_freshness.exists()
        else datetime.now(UTC)
    )
    if now + timedelta(days=7) <= datetime.now(UTC):
        raise ValueError("anomaly_capsule_compatibility_expired")
    proofs: dict[str, dict[str, Any]] = {
        "source": {
            "public_inventory": frozen["data_inventory"],
            "source_code_commit": source_commit,
        },
        "features": {"native_preparations": preparations, "allowlist_schema": signature},
        "pit": {
            "native_independent_verification": [p["feature_manifest_sha256"] for p in preparations],
            "cutoffs": frozen["protocol"],
            "truth_access": "excluded_from_model_and_batch",
        },
        "protocol": {"selection": final["selection"], "original_fit": fit},
        "segments": final,
        "signature": {"signature": signature, "expected_output_sha256": output_sha},
        "resources": {"native_fit": resources, "policy": policy.model_dump(mode="json")},
        "security_license": audit,
        "model_card": card,
        "freshness_drift_compatibility": {
            "reference_id": reference,
            "checked_at": now.isoformat(),
            "valid_until": (now + timedelta(days=7)).isoformat(),
            "schema_compatible": True,
            "model_load_predict": "verified",
            "historical_input_freshness": "stale",
            "drift_monitoring": "not_attested",
            "deployment_attestation": "not_attested",
            "runtime_freshness_policy": "scoring_origin_7_days_unknown_preserved",
        },
    }
    artifacts = {
        "model.json": model_raw,
        "config.json": canonical_json(config) + b"\n",
        "signature.json": canonical_json(signature) + b"\n",
        "input_example.json": canonical_json(example) + b"\n",
    }
    for name in GATES:
        report = {
            **proofs[name],
            "gate": name,
            "status": "passed",
            "evidence_id": evidence_id,
            "model_sha256": frozen["model_sha256"],
        }
        artifacts["gate_" + name + ".json"] = canonical_json(report) + b"\n"
    qualification = Qualification.model_validate_json(
        json.dumps(
            {
                "evidence_id": evidence_id,
                "evaluation_id": quality["quality_id"],
                "reference_id": reference,
                "source_dataset_id": model.descriptor.source_dataset_id,
                "qualified_anomaly_input_id": model.descriptor.qualified_anomaly_input_id,
                "original_started_at": frozen["original_started_at"],
                "original_completed_at": frozen["original_completed_at"],
                "source_code_commit": source_commit,
                "ai_code_commit": args.ai_commit,
                "dependency_lock_sha256": model.descriptor.dependency_lock_sha256,
                "model_family": frozen["family"],
                "model_seed": policy.model_seed,
                "model": receipt(artifacts["model.json"]),
                "config": receipt(artifacts["config.json"]),
                "signature": receipt(artifacts["signature.json"]),
                "input_example": receipt(artifacts["input_example.json"]),
                "expected_output_sha256": output_sha,
                "qualification_scope": frozen["quality_policy"]["qualification_scope"],
                "gates": {
                    name: {
                        "status": "passed",
                        "report": receipt(artifacts["gate_" + name + ".json"]),
                    }
                    for name in GATES
                },
            }
        )
    )
    AnomalyRegistry.smoke(qualification, artifacts)
    for name, expected in final["evaluation_inputs"].items():
        raw = read_bytes(args.final, name, 8 * 1024**2)
        if receipt(raw) != expected:
            raise ValueError("anomaly_capsule_evaluation_checksum")
        artifacts[name] = raw
    artifacts["qualification.json"] = canonical_json(qualification.model_dump(mode="json")) + b"\n"
    for name, raw in artifacts.items():
        immutable(args.output / name, raw, maximum=8 * 1024**2)
    immutable_json(args.output / "model_card.json", card)
    result = {
        "status": "passed",
        "evidence_id": evidence_id,
        "evaluation_id": qualification.evaluation_id,
        "qualification_sha256": receipt(artifacts["qualification.json"])["sha256"],
        "capsule": str(args.output),
        "artifact_count": len(artifacts),
        "truth_access": "offline_evaluation_artifacts_only",
    }
    immutable_json(args.output / "capsule_receipt.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("final", "fit-record", "model", "inventory", "security-audit", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--ai-commit", required=True)
    print(json.dumps(build(parser.parse_args())), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
