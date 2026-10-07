"""Build/verify an offline independent development capsule; never changes serving."""

import argparse
import json
import sqlite3
from pathlib import Path

from pydantic import ValidationError
from sklearn.exceptions import ConvergenceWarning  # type: ignore[import-untyped]

from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.stockout.artifacts import write_artifact
from retailops_ai.stockout_qualification.bundle import build_qualification
from retailops_ai.stockout_qualification.contract import QualificationPolicy
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
        policy = QualificationPolicy.model_validate(read_bounded(args.policy, 1024**2))
        inputs = PartitionInputs(
            args.curated, args.private, args.features, args.upstream, args.labels
        )
        result = build_qualification(
            args.temporal, inputs, policy, allow_evaluation_truth=args.allow_evaluation_truth
        )
        if args.action == "verify":
            if read_bounded(args.output, MAX_BYTES) != result:
                raise ValueError("stockout_qualification_capsule_full_replay_mismatch")
            status = "verified"
        else:
            status = write_artifact(result, args.output, max_bytes=MAX_BYTES)
        q = result["qualification"]
        print(
            json.dumps(
                dict(
                    status=status,
                    qualification_id=q["qualification_id"],
                    card_id=result["model_card"]["card_id"],
                    quality_status=q["content"]["gates"]["status"],
                    final_test_outcomes_evaluated=False,
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
            json.dumps(
                dict(status="failed", reason="invalid_stockout_qualification_input_or_artifact")
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
