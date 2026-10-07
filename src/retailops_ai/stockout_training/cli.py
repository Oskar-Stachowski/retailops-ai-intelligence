"""Offline development comparison after full replay of every prepared parent."""

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sklearn.exceptions import ConvergenceWarning  # type: ignore[import-untyped]

from retailops_ai.source_snapshot.files import SnapshotError, decode_json, read_bytes, read_json
from retailops_ai.stockout.artifacts import write_artifact
from retailops_ai.stockout.dataset import verify_labels
from retailops_ai.stockout.feature_dataset import (
    MAX_FEATURE_BYTES,
    load_features_input,
    verify_features,
)
from retailops_ai.stockout.upstream_dataset import verify_upstream
from retailops_ai.stockout_training.contract import MAX_BYTES, TrainingPolicy
from retailops_ai.stockout_training.development import build_development
from retailops_ai.stockout_training.inputs import assemble_development


def read_bounded(path: Path, limit: int) -> dict[str, Any]:
    value = decode_json(read_bytes(path.parent, path.name, limit), limit=limit)
    if not isinstance(value, dict):
        raise ValueError("stockout_training_requires_JSON_object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "verify"))
    for name in (
        "curated",
        "features",
        "upstream",
        "comparison",
        "labels",
        "split",
        "source",
        "output",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--allow-evaluation-truth", action="store_true")
    args = parser.parse_args()
    try:
        if not args.allow_evaluation_truth:
            raise ValueError("stockout_training_requires_label_verification_opt_in")
        stored = read_bounded(args.output, MAX_BYTES) if args.action == "verify" else None
        policy = TrainingPolicy.model_validate(stored["descriptor"]["policy"] if stored else {})
        features = verify_features(args.features, args.curated)
        upstream = verify_upstream(args.upstream, args.curated, features)
        comparison = read_bounded(args.comparison, MAX_FEATURE_BYTES)
        labels = verify_labels(args.labels, args.source, allow_evaluation_truth=True)
        split = read_json(args.split.parent, args.split.name)
        _, facts = load_features_input(args.curated)
        development = assemble_development(
            features, upstream, comparison, labels, split, facts["product_catalog"]
        )
        document = build_development(development, policy=policy)
        if stored is None:
            status = write_artifact(document, args.output, max_bytes=MAX_BYTES)
        elif stored != document:
            raise ValueError("stockout_development_full_replay_mismatch")
        else:
            status = "verified"
        print(
            json.dumps(
                dict(status=status, id=document["development_id"], report=document["report"])
            )
        )
        return 0
    except (
        SnapshotError,
        ValidationError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        ConvergenceWarning,
    ):
        print(
            json.dumps(dict(status="error", reason="invalid_stockout_training_artifact_or_input"))
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
