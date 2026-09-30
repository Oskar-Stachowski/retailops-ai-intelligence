"""Self-contained v2 evidence export, with immutable parents and explicit quality blockers."""

import argparse
import hashlib
import json
import os
import shutil
import stat
import tempfile
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

from retailops_ai.curated.builder import verify_curated
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecasting.functional_campaign import load_campaign, verify_campaign, write_json
from retailops_ai.forecasting.run import _copy_file
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    file_hash,
    inventory,
    read_json,
)
from retailops_ai.source_snapshot.importer import verify_import
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

MAX_FILES = 10000
MAX_BYTES = 8 * 1024**3


def paths(root: Path) -> list[str]:
    checked_directory(root)
    names = []
    for base, dirs, filenames in os.walk(root, followlinks=False):
        for name in (*dirs, *filenames):
            path = Path(base) / name
            mode = path.lstat().st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise SnapshotError("functional_run_symlink_or_special_file")
            if stat.S_ISREG(mode):
                names.append(path.relative_to(root).as_posix())
                if len(names) > MAX_FILES:
                    raise SnapshotError("functional_run_file_budget")
    return sorted(names)


def export_run(
    source: Path,
    curated: Path,
    features: Path,
    split: Path,
    campaign: Path,
    output: Path,
    code_commit: str,
) -> Path:
    parents = {
        "source": source,
        "curated": curated,
        "features": features,
        "split": split,
        "campaign": campaign,
    }
    for parent in parents.values():
        if output.absolute().is_relative_to(parent.absolute()):
            raise SnapshotError("functional_run_output_inside_parent")
    listed = {part: paths(parent) for part, parent in parents.items()}
    count = sum(len(names) for names in listed.values())
    size = sum(
        (parents[part] / name).stat().st_size for part, names in listed.items() for name in names
    )
    if count + 3 > MAX_FILES or size > MAX_BYTES:
        raise SnapshotError("functional_run_archive_budget")
    output.mkdir(parents=True, exist_ok=True)
    checked_directory(output)
    if shutil.disk_usage(output).free < size + 16 * 1024**3:
        raise SnapshotError("functional_run_replay_and_archive_space_budget")
    # One independent replay from all recorded parents, without model fitting.
    verified = verify_campaign(campaign, features, split)
    imported = verify_import(source)
    curated_manifest = verify_curated(curated)
    parent = verified["descriptor"]["parent"]
    if (
        parent["source_dataset_id"] != imported.source_id
        or parent["snapshot_id"] != imported.snapshot_id
        or parent["curated_dataset_id"] != curated_manifest["curated_dataset_id"]
    ):
        raise SnapshotError("functional_run_source_lineage")
    with tempfile.TemporaryDirectory(prefix=".functional-run-", dir=output) as temporary:
        root = Path(temporary).resolve()
        receipts = {}
        for part, names in listed.items():
            for name in names:
                target = part + "/" + name
                receipts[target] = _copy_file(parents[part], root / target, name).model_dump(
                    mode="json"
                )
        # Verification after copying rejects changes to any parent during export.
        for name, receipt in receipts.items():
            if file_hash(root, name) != (receipt["size_bytes"], receipt["sha256"]):
                raise SnapshotError("functional_run_copy_checksum")
        card = {
            "target": "observed_sales_units",
            "median_objective": "MAE",
            "mean_objective": "MSE_and_absolute_normalized_bias",
            "interval": "central_90_percent_proper_interval_score_and_empirical_coverage",
            "quality_status": verified["descriptor"]["gates"]["status"],
            "gates": verified["descriptor"]["gates"],
            "source": parent,
            "synthetic_source": True,
            "portfolio_final_test": "not_included_not_opened",
            "deployment": "not_promoted",
            "old_quality_results": "retained_separately_without_reclassification",
        }
        handoff = {
            "format": "forecast-functional-run-2.0.0",
            "campaign_id": verified["campaign_id"],
            "feature_set_id": verified["descriptor"]["feature_set_id"],
            "split_id": verified["descriptor"]["split_id"],
            "label_dataset_id": verified["descriptor"]["label_dataset_id"],
            "model_ids": verified["descriptor"]["model_ids"],
            "freeze": verified["descriptor"]["freeze"],
            "quality_status": card["quality_status"],
            "independent_replay": "passed",
            "training_tracking_run_id": None,
            "import_time_must_not_replace_training_time": True,
            "prediction_contract": "candidate_and_baseline_each_with_median_mean_interval",
            "legacy_single_output_importer_compatible": False,
            "mlflow_or_registry_written": False,
        }
        for name, body in (("model_card.json", card), ("handoff.json", handoff)):
            write_json(root / name, body)
            length, digest = file_hash(root, name)
            receipts[name] = {"size_bytes": length, "sha256": digest}
        descriptor = {
            "schema_version": "2.0.0",
            "campaign_id": verified["campaign_id"],
            "quality_status": card["quality_status"],
            "ai_code_commit": code_commit,
            "replay_status": "passed",
            "files": receipts,
            "bytes": sum(r["size_bytes"] for r in receipts.values()),
            "export_code_sha256": hashlib.sha256(
                files("retailops_ai.forecasting").joinpath("functional_run.py").read_bytes()
            ).hexdigest(),
        }
        manifest = {
            "run_id": "functional-run-sha256-" + canonical_sha256(descriptor),
            "descriptor": descriptor,
            "exported_at": datetime.now(UTC).isoformat(),
        }
        write_json(root / "run_manifest.json", manifest)
        fsync_tree(root)
        destination = output / str(manifest["run_id"])
        publish_noreplace(root, destination)
    load_run(destination)
    return destination


def load_run(root: Path) -> dict[str, Any]:
    manifest = read_json(root, "run_manifest.json")
    desc = manifest["descriptor"]
    if manifest["run_id"] != "functional-run-sha256-" + canonical_sha256(desc):
        raise SnapshotError("functional_run_identity")
    inventory(root, {"run_manifest.json", *desc["files"]})
    for name, receipt in desc["files"].items():
        if file_hash(root, name) != (receipt["size_bytes"], receipt["sha256"]):
            raise SnapshotError("functional_run_file_checksum")
    campaign = load_campaign(root / "campaign")
    if (
        campaign["campaign_id"] != desc["campaign_id"]
        or campaign["descriptor"]["gates"]["status"] != desc["quality_status"]
    ):
        raise SnapshotError("functional_run_quality_binding")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "curated", "features", "split", "campaign", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--code-commit", required=True)
    args = parser.parse_args(argv)
    destination = export_run(
        args.source,
        args.curated,
        args.features,
        args.split,
        args.campaign,
        args.output,
        args.code_commit,
    )
    print(json.dumps({"destination": str(destination), "run_id": load_run(destination)["run_id"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
