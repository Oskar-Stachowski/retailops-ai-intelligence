"""Freeze public lineage before opening final truth; replay saved models over all six cases."""

import argparse
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from retailops_ai.anomaly_detectors.protocol import series_key
from retailops_ai.anomaly_evaluation.contract import EvaluationPolicy
from retailops_ai.anomaly_evaluation.evaluator import evaluate
from retailops_ai.anomaly_evaluation.quality import QualityPolicy, assess
from retailops_ai.anomaly_evaluation.verification import verify_scores
from retailops_ai.anomaly_portfolio.artifacts import immutable_json
from retailops_ai.anomaly_portfolio.cli import prepared, protocol
from retailops_ai.anomaly_portfolio.inputs import VerifiedFeatures
from retailops_ai.anomaly_portfolio.model import load, score, scoring_row
from retailops_ai.anomaly_portfolio.protocol import PortfolioProtocol
from retailops_ai.qualified_anomalies.contract import Point
from retailops_ai.source_snapshot.files import decode_json, json_sha256, read_bytes


def document(path: str | Path) -> tuple[dict[str, Any], str]:
    path = Path(path)
    raw = read_bytes(path.parent, path.name, 8 * 1024**2)
    return decode_json(raw), hashlib.sha256(raw).hexdigest()


def case_public(entry: dict[str, Any]) -> dict[str, Any]:
    receipt, digest = document(entry["prepared_receipt"])
    bundle, bundle_digest = document(entry["source_bundle"])
    if (
        receipt["status"] != "passed"
        or receipt["truth_access"] != "excluded"
        or receipt["final_test"] != "not_opened"
        or receipt["source_bundle_sha256"] != bundle_digest
        or receipt["source_dataset_id"] != bundle["source_dataset_id"]
    ):
        raise ValueError("anomaly_qualification_preparation_binding")
    snapshot, snapshot_digest = document(
        Path(receipt["parents"][3]) / "snapshot/snapshot_manifest.json"
    )
    source = snapshot["source"]
    parameters = source["descriptor"]["resolved_parameters"]
    if parameters["seed"] != entry["seed"] or parameters["profile"] not in {
        "ai-07-portfolio-v1",
        "ai-07-portfolio-v2",
        "ai-07-portfolio-v3",
        "ai-07-portfolio-v4",
    }:
        raise ValueError("anomaly_qualification_profile_binding")
    capture, capture_digest = document(Path(bundle["capture"]) / "dq_manifest.json")
    return {
        "seed": entry["seed"],
        "scenario": entry["scenario"],
        "source_profile": parameters["profile"],
        "source_dataset_id": receipt["source_dataset_id"],
        "source_snapshot_sha256": snapshot_digest,
        "source_scenario_sha256": source["scenario"]["sha256"],
        "qualified_anomaly_input_id": receipt["feature_id"],
        "feature_manifest_sha256": receipt["feature_manifest_sha256"],
        "feature_rows_sha256": receipt["feature_rows_sha256"],
        "prepared_receipt_sha256": digest,
        "source_bundle_sha256": bundle_digest,
        "raw_fixture_id": capture["fixture_id"],
        "raw_fixture_manifest_sha256": capture_digest,
    }


def freeze(args: argparse.Namespace) -> int:
    inventory, _ = document(args.inventory)
    entries = inventory["cases"]
    quality, _ = document(args.quality_policy)
    policy = QualityPolicy.model_validate_json(json.dumps(quality))
    record, record_digest = document(args.fit_record)
    model = load(Path(record["model_path"]), record["model_sha256"])
    comparison, comparison_digest = document(args.validation_comparison)
    if comparison["final_test"] != "not_scored" or not any(
        (r["model"]["detector_id"], r["family"]) == (model.detector_id, args.family)
        for r in comparison["results"]
    ):
        raise ValueError("anomaly_selection_requires_saved_validation_comparison")
    public = [case_public(entry) for entry in entries]
    profiles = {e["source_profile"] for e in public}
    if len(profiles) != 1 or policy.qualification_scope != "synthetic_" + next(
        iter(profiles)
    ).replace("-", "_"):
        raise ValueError("anomaly_selection_qualification_profile_mismatch")
    expected = {
        (seed, scenario) for seed in policy.source_seeds for scenario in policy.source_scenarios
    }
    if len(public) != 6 or {(e["seed"], e["scenario"]) for e in public} != expected:
        raise ValueError("anomaly_selection_complete_inventory_required")
    first = next(e for e in entries if (e["seed"], e["scenario"]) == (42, "demand"))
    frame = prepared(Path(first["prepared_receipt"]))
    split = protocol(frame)
    if (
        split.train != model.descriptor.train
        or split.validation != model.descriptor.validation
        or split.training_cutoff != model.descriptor.training_cutoff
        or split.selection_cutoff != model.descriptor.selection_cutoff
    ):
        raise ValueError("anomaly_selection_training_protocol_binding")
    frozen = {
        "version": "anomaly-selection-1.0.0",
        "detector_id": model.detector_id,
        "family": args.family,
        "model_sha256": record["model_sha256"],
        "fit_record_sha256": record_digest,
        "original_started_at": record["original_started_at"],
        "original_completed_at": record["original_completed_at"],
        "validation_comparison_sha256": comparison_digest,
        "selection_reason": args.reason,
        "quality_policy": policy.model_dump(mode="json"),
        "evaluation_policy": EvaluationPolicy().model_dump(mode="json"),
        "protocol": split.model_dump(mode="json"),
        "final_as_of": (
            datetime.combine(split.test.end, datetime.min.time(), UTC) + timedelta(days=4)
        ).isoformat(),
        "data_inventory": sorted(public, key=lambda e: (e["seed"], e["scenario"])),
        "frozen_at": datetime.now(UTC).isoformat(),
        "final_test_at_freeze": "not_scored",
        "truth_access_during_selection": "validation_only_no_final_truth",
    }
    immutable_json(
        args.output,
        {"selection_id": "anomaly-selection-sha256-" + json_sha256(frozen), "descriptor": frozen},
    )
    print(json.dumps({"selection": "frozen", "final_test": "not_scored"}), flush=True)
    return 0


def final_case(
    task: tuple[dict[str, Any], dict[str, Any], str, str],
    shared_features: VerifiedFeatures | None = None,
) -> dict[str, Any]:
    # The private adapter is intentionally reachable only in this offline final evaluator.
    from retailops_ai.anomaly_evaluation.source_truth import source_truth

    entry, frozen, model_path, output = task
    now = datetime.now(UTC)
    if now <= datetime.fromisoformat(frozen["frozen_at"]):
        raise ValueError("anomaly_final_evaluation_before_freeze")
    actual = case_public(entry)
    expected = next(
        e
        for e in frozen["data_inventory"]
        if (e["seed"], e["scenario"]) == (entry["seed"], entry["scenario"])
    )
    if actual != expected:
        raise ValueError("anomaly_final_frozen_input_changed")
    frame = shared_features or prepared(Path(entry["prepared_receipt"]))
    if (
        frame.manifest_sha256 != expected["feature_manifest_sha256"]
        or frame.manifest.qualified_anomaly_input_id != expected["qualified_anomaly_input_id"]
        or frame.manifest.descriptor.rows_sha256 != expected["feature_rows_sha256"]
        or frame.manifest.descriptor.coverage.descriptor.source_dataset_id
        != expected["source_dataset_id"]
    ):
        raise ValueError("anomaly_final_shared_verified_features_changed")
    split = PortfolioProtocol.model_validate_json(json.dumps(frozen["protocol"]))
    if protocol(frame) != split:
        raise ValueError("anomaly_final_scopes_or_protocol_changed")
    points = list(frame.points())
    model = load(Path(model_path), frozen["model_sha256"])
    as_of = datetime.fromisoformat(frozen["final_as_of"])
    decisions = score(
        model, points, split.scopes, split.test, frozen["family"], "final_test", as_of
    )
    indexed: dict[tuple[object, ...], Point] = {
        (*series_key(p), p.business_date): p for p in points
    }
    model_rows = [
        r.model_dump(mode="json")
        if (p := indexed.get((*series_key(d), d.business_date))) is not None
        and (r := scoring_row(model, p, indexed)) is not None
        else None
        for d in decisions
    ]
    verify_scores(model, decisions, model_rows)
    receipt, _ = document(entry["prepared_receipt"])
    snapshot, _ = document(Path(receipt["parents"][3]) / "snapshot/snapshot_manifest.json")
    bundle, _ = document(entry["source_bundle"])
    truth = source_truth(
        Path(bundle["private_scenario"]), snapshot["source"], split.scopes, split.test
    )
    report = evaluate(
        decisions,
        truth,
        split.test,
        as_of,
        EvaluationPolicy.model_validate_json(json.dumps(frozen["evaluation_policy"])),
        source_dataset_id=actual["source_dataset_id"],
    )
    name = f"evaluation_inputs_{entry['seed']}_{entry['scenario']}.json"
    value = {
        "seed": entry["seed"],
        "scenario": entry["scenario"],
        "source_dataset_id": actual["source_dataset_id"],
        "public_input_lineage": actual,
        "window": split.test.model_dump(mode="json"),
        "as_of": as_of.isoformat(),
        "decisions": [d.model_dump(mode="json") for d in decisions],
        "model_rows": model_rows,
        "truth": truth.model_dump(mode="json"),
        "final_started_at": now.isoformat(),
        "final_completed_at": datetime.now(UTC).isoformat(),
    }
    path = Path(output) / name
    if path.exists():
        previous, _ = document(path)
        started = datetime.fromisoformat(previous["final_started_at"])
        completed = datetime.fromisoformat(previous["final_completed_at"])
        if not datetime.fromisoformat(frozen["frozen_at"]) < started <= completed <= now:
            raise ValueError("anomaly_final_original_times_invalid")
        # Recompute every numerical/truth binding, preserving original times.
        # immutable_json then rejects any changed input or saved prediction.
        value["final_started_at"] = previous["final_started_at"]
        value["final_completed_at"] = previous["final_completed_at"]
    immutable_json(path, value)
    raw = read_bytes(path.parent, path.name, 8 * 1024**2)
    case = {
        "seed": entry["seed"],
        "scenario": entry["scenario"],
        "source_dataset_id": actual["source_dataset_id"],
        "report": report,
    }
    immutable_json(Path(output) / f"evaluation_{entry['seed']}_{entry['scenario']}.json", case)
    return {
        "case": case,
        "name": name,
        "receipt": {"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)},
    }


def publish_final(selection: dict[str, Any], results: list[dict[str, Any]], output: Path) -> int:
    quality = assess(
        [r["case"] for r in results],
        QualityPolicy.model_validate_json(json.dumps(selection["descriptor"]["quality_policy"])),
    )
    result = {
        "quality": quality,
        "selection": selection,
        "evaluation_inputs": {r["name"]: r["receipt"] for r in results},
        "evaluated_at": datetime.now(UTC).isoformat(),
        "status": quality["descriptor"]["status"],
    }
    immutable_json(output / "final_quality.json", result)
    print(json.dumps({"quality_id": quality["quality_id"], "status": result["status"]}), flush=True)
    return 0 if result["status"] == "passed" else 1


def final(args: argparse.Namespace) -> int:
    selection, _ = document(args.selection)
    frozen = selection["descriptor"]
    if selection["selection_id"] != "anomaly-selection-sha256-" + json_sha256(frozen):
        raise ValueError("anomaly_final_selection_identity")
    inventory, _ = document(args.inventory)
    wanted = {(e["seed"], e["scenario"]) for e in frozen["data_inventory"]}
    entries = inventory["cases"]
    if len(entries) != 6 or {(e["seed"], e["scenario"]) for e in entries} != wanted:
        raise ValueError("anomaly_final_inventory_changed")
    tasks = [(e, frozen, str(args.model), str(args.output)) for e in entries]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(final_case, tasks))
    return publish_final(selection, results, args.output)


def shared_final_cases(
    task: tuple[dict[str, Any], list[tuple[dict[str, Any], str, str]]],
) -> list[dict[str, Any]]:
    entry, versions = task
    if len(versions) != 2:
        raise ValueError("anomaly_shared_final_two_frozen_versions_required")
    frame = prepared(Path(entry["prepared_receipt"]))
    return [final_case((entry, frozen, model, output), frame) for frozen, model, output in versions]


def many_final(args: argparse.Namespace) -> int:
    if (
        len(args.selection) != 2
        or len(args.model) != 2
        or len(args.output) != 2
        or len({p.resolve() for p in args.output}) != 2
    ):
        raise ValueError("anomaly_shared_final_two_distinct_outputs_required")
    selections = [document(p)[0] for p in args.selection]
    frozen = [s["descriptor"] for s in selections]
    for selection, desc in zip(selections, frozen, strict=True):
        if (
            selection["selection_id"] != "anomaly-selection-sha256-" + json_sha256(desc)
            or desc["final_test_at_freeze"] != "not_scored"
        ):
            raise ValueError("anomaly_final_selection_identity")
    if any(
        frozen[0][name] != frozen[1][name]
        for name in (
            "data_inventory",
            "protocol",
            "final_as_of",
            "quality_policy",
            "evaluation_policy",
        )
    ):
        raise ValueError("anomaly_shared_final_requires_same_frozen_inputs_and_gates")
    inventory, _ = document(args.inventory)
    entries = inventory["cases"]
    wanted = {(e["seed"], e["scenario"]) for e in frozen[0]["data_inventory"]}
    if len(entries) != 6 or {(e["seed"], e["scenario"]) for e in entries} != wanted:
        raise ValueError("anomaly_final_inventory_changed")
    versions = [
        (desc, str(model), str(output))
        for desc, model, output in zip(frozen, args.model, args.output, strict=True)
    ]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        combined = list(pool.map(shared_final_cases, [(entry, versions) for entry in entries]))
    statuses = [
        publish_final(selection, [r[i] for r in combined], output)
        for i, (selection, output) in enumerate(zip(selections, args.output, strict=True))
    ]
    return max(statuses)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    one = modes.add_parser("freeze")
    one.add_argument("--inventory", type=Path, required=True)
    one.add_argument("--quality-policy", type=Path, required=True)
    one.add_argument("--fit-record", type=Path, required=True)
    one.add_argument("--validation-comparison", type=Path, required=True)
    one.add_argument("--family", choices=("seasonal_residual", "isolation_forest"), required=True)
    one.add_argument("--reason", required=True)
    one.add_argument("--output", type=Path, required=True)
    two = modes.add_parser("final")
    two.add_argument("--inventory", type=Path, required=True)
    two.add_argument("--selection", type=Path, required=True)
    two.add_argument("--model", type=Path, required=True)
    two.add_argument("--output", type=Path, required=True)
    two.add_argument("--workers", type=int, choices=(1, 2, 3), default=2)
    many = modes.add_parser("many-final")
    many.add_argument("--inventory", type=Path, required=True)
    for name in ("selection", "model", "output"):
        many.add_argument("--" + name, type=Path, action="append", required=True)
    many.add_argument("--workers", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    return {"freeze": freeze, "final": final, "many-final": many_final}[args.mode](args)


if __name__ == "__main__":
    raise SystemExit(main())
