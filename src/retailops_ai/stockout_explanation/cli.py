"""Build/verify a development-only model card after full replay of all inputs."""

import argparse
import json
from pathlib import Path

from pydantic import ValidationError
from sklearn.exceptions import ConvergenceWarning  # type: ignore[import-untyped]

from retailops_ai.source_snapshot.files import SnapshotError, read_json
from retailops_ai.stockout.artifacts import write_artifact
from retailops_ai.stockout.dataset import verify_labels
from retailops_ai.stockout.feature_dataset import (
    MAX_FEATURE_BYTES,
    load_features_input,
    verify_features,
)
from retailops_ai.stockout.upstream_dataset import verify_upstream
from retailops_ai.stockout_explanation.card import build_card
from retailops_ai.stockout_explanation.diagnostics import MAX_CARD_BYTES, ExplanationPolicy
from retailops_ai.stockout_training.cli import read_bounded
from retailops_ai.stockout_training.contract import MAX_BYTES
from retailops_ai.stockout_training.inputs import assemble_development


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
        "development",
        "output",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--allow-evaluation-truth", action="store_true")
    args = parser.parse_args()
    try:
        if not args.allow_evaluation_truth:
            raise ValueError("stockout_card_requires_label_verification_opt_in")
        stored = read_bounded(args.output, MAX_CARD_BYTES) if args.action == "verify" else None
        policy = ExplanationPolicy.model_validate(stored["descriptor"]["policy"] if stored else {})
        parent = read_bounded(args.development, MAX_BYTES)
        features = verify_features(args.features, args.curated)
        upstream = verify_upstream(args.upstream, args.curated, features)
        comparison = read_bounded(args.comparison, MAX_FEATURE_BYTES)
        labels = verify_labels(args.labels, args.source, allow_evaluation_truth=True)
        split = read_json(args.split.parent, args.split.name)
        _, facts = load_features_input(args.curated)
        data = assemble_development(
            features, upstream, comparison, labels, split, facts["product_catalog"]
        )
        document = build_card(data, parent, features, policy=policy)
        if stored is None:
            status = write_artifact(document, args.output, max_bytes=MAX_CARD_BYTES)
        elif stored != document:
            raise ValueError("stockout_card_full_replay_mismatch")
        else:
            status = "verified"
        print(
            json.dumps(
                dict(status=status, id=document["card_id"], report=document["content"]["readiness"])
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
        print(json.dumps(dict(status="error", reason="invalid_stockout_card_artifact_or_input")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
