"""Build/verify an offline later-calibration development selection capsule; never changes serving."""

import argparse
import json
import sqlite3
from pathlib import Path

from pydantic import ValidationError
from sklearn.exceptions import ConvergenceWarning  # type: ignore[import-untyped]

from retailops_ai.source_snapshot.files import SnapshotError, canonical_json
from retailops_ai.stockout.artifacts import write_artifact
from retailops_ai.stockout_selection.bundle import build_selection
from retailops_ai.stockout_selection.contract import SelectionPolicy
from retailops_ai.stockout_temporal_storage.store import PartitionInputs
from retailops_ai.stockout_training.cli import read_bounded
from retailops_ai.stockout_training.contract import MAX_BYTES


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "verify"))
    for name in (
        "curated",
        "private",
        "features",
        "upstream",
        "labels",
        "temporal",
        "policy",
        "output",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--allow-evaluation-truth", action="store_true")
    args = parser.parse_args()
    try:
        if not args.allow_evaluation_truth:
            raise ValueError("stockout_selection_private_verification_opt_in_required")
        policy = SelectionPolicy.model_validate_json(
            canonical_json(read_bounded(args.policy, 1024**2))
        )
        inputs = PartitionInputs(
            args.curated, args.private, args.features, args.upstream, args.labels
        )
        result = build_selection(
            args.temporal, inputs, policy, allow_evaluation_truth=args.allow_evaluation_truth
        )
        if args.action == "verify":
            if read_bounded(args.output, MAX_BYTES) != result:
                raise ValueError("stockout_selection_capsule_full_replay_mismatch")
            status = "verified"
        else:
            status = write_artifact(result, args.output, max_bytes=MAX_BYTES)
        q = result["selection"]
        print(
            json.dumps(
                dict(
                    status=status,
                    selection_id=q["selection_id"],
                    card_id=result["model_card"]["card_id"],
                    development_selection_status=q["content"]["selected_development_gates"][
                        "status"
                    ],
                    final_test_outcomes_evaluated=False,
                    independent_quality_accepted=False,
                    model_ready=False,
                )
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
        sqlite3.Error,
        ConvergenceWarning,
    ):
        print(
            json.dumps(dict(status="failed", reason="invalid_stockout_selection_input_or_artifact"))
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
