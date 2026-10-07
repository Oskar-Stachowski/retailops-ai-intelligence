"""Full replay of a later-calibrated development policy; no operational approval."""

import argparse
import json
import sqlite3
from pathlib import Path

from pydantic import ValidationError
from sklearn.exceptions import ConvergenceWarning  # type: ignore[import-untyped]

from retailops_ai.source_snapshot.files import SnapshotError, canonical_json
from retailops_ai.stockout.artifacts import write_artifact
from retailops_ai.stockout_policy.contract import PolicySpec
from retailops_ai.stockout_policy.selection import build_selected_proposal
from retailops_ai.stockout_selection.contract import SelectionPolicy
from retailops_ai.stockout_temporal_storage.store import PartitionInputs
from retailops_ai.stockout_training.cli import read_bounded


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
        "selection-policy",
        "operator-proposal",
        "output",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--allow-evaluation-truth", action="store_true")
    args = parser.parse_args()
    try:
        if not args.allow_evaluation_truth:
            raise ValueError("stockout_policy_private_verification_opt_in_required")
        qp = SelectionPolicy.model_validate_json(
            canonical_json(read_bounded(args.selection_policy, 1024**2))
        )
        spec = PolicySpec.model_validate_json(
            canonical_json(read_bounded(args.operator_proposal, 1024**2))
        )
        result = build_selected_proposal(
            args.temporal,
            PartitionInputs(args.curated, args.private, args.features, args.upstream, args.labels),
            qp,
            spec,
            allow_evaluation_truth=True,
        )
        if args.action == "verify":
            if read_bounded(args.output, 1024**2) != result:
                raise ValueError("stockout_policy_full_replay_mismatch")
            status = "verified"
        else:
            status = write_artifact(result, args.output, max_bytes=1024**2)
        print(
            json.dumps(
                dict(
                    status=status,
                    proposal_id=result["proposal_id"],
                    development_selection_status=result["content"]["development_selection_gates"][
                        "status"
                    ],
                    independent_quality_accepted=False,
                    thresholds_approved=False,
                    serving_eligible=False,
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
        print(json.dumps(dict(status="failed", reason="invalid_stockout_policy_input_or_artifact")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
