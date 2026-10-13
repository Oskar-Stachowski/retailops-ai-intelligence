"""Seal original comparison artifacts and reload frozen models without fitting."""

import shutil
import time
from pathlib import Path
from typing import Any

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.development import _inventory, comparison_code
from retailops_ai.evaluation_campaign.development_contract import DevelopmentComparisonManifest
from retailops_ai.forecasting.functional_contract import FunctionalPipeline
from retailops_ai.forecasting.functional_models import functional_code
from retailops_ai.forecasting.model_contract import MAX_MODEL_BYTES
from retailops_ai.forecasting.model_trees import TreePredictor
from retailops_ai.source_snapshot.files import SnapshotError, read_bytes, read_json
from retailops_ai.tensorflow_challenger.pipeline import LoadedChallenger

MAXIMUM = 1024**3


def seal_models(comparison: Path, output: Path) -> dict[str, Any]:
    manifest = DevelopmentComparisonManifest.model_validate_json(
        read_bytes(comparison, "manifest.json")
    )
    if (
        manifest.protocol.implementation_sha256 != comparison_code()
        or _inventory(comparison, manifest.protocol.policy.max_output_bytes)
        != {name: value.model_dump() for name, value in manifest.files.items()}
        or read_json(comparison, "attempt.json")["status"] != "completed_development_diagnostic"
    ):
        raise SnapshotError("portfolio_original_comparison_not_verified")
    output.mkdir(mode=0o700)
    for directory in ("trees", "tensorflow"):
        shutil.copytree(comparison / directory, output / directory)
    for name in ("protocol.json", "report.json", "resources.json", "trials.jsonl"):
        shutil.copyfile(comparison / name, output / name)
    document = {
        "version": "ai09-portfolio-frozen-model-bundle-1.0.0",
        "original_comparison_id": manifest.comparison_id,
        "model_ids": read_json(comparison, "report.json")["model_ids"],
        "files": _inventory(output, MAXIMUM),
        "new_fits": 0,
    }
    (output / "manifest.json").write_bytes(canonical_bytes(document) + b"\n")
    return document


def verify_models(root: Path) -> dict[str, Any]:
    document = read_json(root, "manifest.json")
    if (
        document["version"] != "ai09-portfolio-frozen-model-bundle-1.0.0"
        or document["new_fits"] != 0
        or _inventory(root, MAXIMUM) != document["files"]
    ):
        raise SnapshotError("portfolio_model_bundle_changed")
    return document


def load_models(
    root: Path,
) -> tuple[LoadedChallenger, dict[str, tuple[FunctionalPipeline, TreePredictor]], dict[str, Any]]:
    document = verify_models(root)
    started = time.monotonic()
    tensorflow = LoadedChallenger(root / "tensorflow")
    tf_seconds = time.monotonic() - started
    if tensorflow.manifest["model_id"] != document["model_ids"]["tensorflow"]:
        raise SnapshotError("portfolio_tensorflow_identity_changed")
    trees = {}
    for head in ("rf_mean", "hgb_mean", "hgb_median"):
        pipeline = FunctionalPipeline.model_validate_json(
            read_bytes(root, "trees/" + head + ".json", MAX_MODEL_BYTES)
        )
        if pipeline.model_id != document["model_ids"][
            head
        ] or pipeline.code_sha256 != canonical_sha256(functional_code()):
            raise SnapshotError("portfolio_tree_identity_or_code_changed")
        trees[head] = pipeline, TreePredictor(pipeline.estimator)
    return (
        tensorflow,
        trees,
        {
            "tensorflow_cold_load_seconds": tf_seconds,
            "all_models_cold_load_seconds": time.monotonic() - started,
            "new_fits": 0,
        },
    )
