"""Development probe: shrink an additive mean using validation labels only.

The reference was selected by the existing validation procedure. This probe is
an internal calibration diagnostic, not an independent forecast qualification.
It never uses a development_holdout actual, candidate, or baseline for fitting.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from retailops_ai.forecasting.mean_validation_weights import (
    WEIGHTS,
    select_weight,
    validation_payload,
)

CAMPAIGN: Path
OUTPUT: Path
MINIMUM_TARGETS = 50
MAX_BINS = 1_000_000


def stamp() -> str:
    return datetime.now(UTC).isoformat()


def save(name: str, value: Any) -> None:
    with (OUTPUT / name).open("x") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def add_bin(bins: dict[float, list[int]], baseline: float, actual: int) -> None:
    values = bins.setdefault(baseline, [0, 0, 0])
    values[0] += 1
    values[1] += actual
    values[2] += actual * actual


def main() -> int:
    global CAMPAIGN, OUTPUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    CAMPAIGN, OUTPUT = args.campaign.resolve(strict=True), args.output.resolve()
    freeze_path = args.freeze.resolve(strict=True)
    OUTPUT.mkdir()
    freeze = json.loads(freeze_path.read_bytes())
    folds = {row["name"]: row for row in freeze["descriptor"]["split_policy"]["folds"]}
    windows = {}
    for name, fold in folds.items():
        start = date.fromisoformat(fold["validation"]["end"]) - timedelta(days=6)
        selection_start = datetime.combine(start, time.min, UTC)
        windows[name] = {
            "selection_start": selection_start,
            "prefix_label_cutoff": selection_start - timedelta(days=fold["purge_days"]),
            "outer_selection_cutoff": datetime.fromisoformat(fold["selection_cutoff"]),
        }
    save(
        "protocol-before-read.json",
        {
            "at": stamp(),
            "status": "development_only_not_frozen_qualification",
            "method": "forward_validation_mean_weight_selection",
            "weights": WEIGHTS,
            "tie_break": "lowest_selection_mse_then_smallest_weight",
            "selection_origins": "last_seven_calendar_days_of_validation",
            "prefix_labels": "available_before_selection_start_minus_existing_purge_days",
            "full_validation_constraints": "mean_mse_nonregression_and_existing_10pct_bias",
            "zero_volume": "existing_v12_category_prior_unchanged",
            "median_and_interval": "existing_reference_unchanged",
            "reference_selection": "existing_validation_selected_reference_not_independent_inner_backtest",
            "holdout_use_for_selection": False,
            "quality_threshold_changes": False,
            "new_source_generation": False,
            "input_campaign": str(CAMPAIGN),
            "freeze_sha256": hashlib.sha256(freeze_path.read_bytes()).hexdigest(),
            "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "campaign_manifest_sha256": hashlib.sha256(
                (CAMPAIGN / "campaign_manifest.json").read_bytes()
            ).hexdigest(),
            "windows": {
                name: {k: v.isoformat() for k, v in window.items()}
                for name, window in windows.items()
            },
        },
    )
    prefix: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
        lambda: {"rows": 0, "residual_sum": 0.0, "targets": {}}
    )
    full: dict[tuple[str, str, str], dict[float, list[int]]] = defaultdict(dict)
    selection: dict[tuple[str, str, str], dict[float, list[int]]] = defaultdict(dict)
    full_offsets = {
        fold: json.loads((CAMPAIGN / "calibration" / (fold + ".json")).read_bytes())["offsets"]
        for fold in folds
    }
    counts = {
        "validation_rows": 0,
        "non_validation_rows_skipped_before_value_access": 0,
        "excluded_validation_rows": 0,
        "zero_validation_rows_unchanged": 0,
        "prefix_rows": 0,
        "selection_rows": 0,
    }
    inputs = sorted((CAMPAIGN / "predictions").glob("*.jsonl.gz"))
    if len(inputs) != 64:
        raise RuntimeError("unexpected_development_input_population")
    for index, path in enumerate(inputs, 1):
        with gzip.open(path, "rt") as stream:
            for line in stream:
                row = json.loads(line)
                if row["role"] != "validation":
                    counts["non_validation_rows_skipped_before_value_access"] += 1
                    continue
                counts["validation_rows"] += 1
                observation = validation_payload(row)
                if observation is None:
                    counts["excluded_validation_rows"] += 1
                    continue
                if row["volume"] == "zero":
                    counts["zero_validation_rows_unchanged"] += 1
                    continue
                actual = observation["actual"]
                baseline = observation["baseline"]["mean"]
                if (
                    type(actual) is not int
                    or actual < 0
                    or baseline is None
                    or not math.isfinite(baseline)
                ):
                    raise RuntimeError("invalid_eligible_validation_point")
                fold, volume, category = row["fold"], row["volume"], row["category"]
                origin = datetime.fromisoformat(json.loads(row["key"])[2])
                available = datetime.fromisoformat(row["label_available_at"])
                window = windows[fold]
                if available > window["outer_selection_cutoff"]:
                    raise RuntimeError("validation_label_after_outer_cutoff")
                group = (fold, volume, category)
                add_bin(full[group], baseline, actual)
                if (
                    available <= window["prefix_label_cutoff"]
                    and origin < window["selection_start"]
                ):
                    parts = json.loads(row["key"])
                    target = (row["cohort_id"], parts[3], parts[4], row["channel"], parts[6])
                    for key in ((fold, "*", "*"), (fold, volume, "*"), group):
                        stats = prefix[key]
                        if target in stats["targets"] and stats["targets"][target] != actual:
                            raise RuntimeError("conflicting_unique_validation_target")
                        stats["targets"][target] = actual
                        stats["rows"] += 1
                        stats["residual_sum"] += actual - baseline
                    counts["prefix_rows"] += 1
                if origin >= window["selection_start"]:
                    add_bin(selection[group], baseline, actual)
                    counts["selection_rows"] += 1
        if (
            sum(len(bins) for bins in full.values()) + sum(len(bins) for bins in selection.values())
            > MAX_BINS
        ):
            raise RuntimeError("development_moment_bin_budget_exceeded")
        print(
            json.dumps(
                {
                    "at": stamp(),
                    "development_inputs_read": index,
                    "of": 64,
                    "validation_rows": counts["validation_rows"],
                }
            ),
            flush=True,
        )

    def offset(group: tuple[str, str, str]) -> float | None:
        fold, volume, category = group
        global_stats = prefix[(fold, "*", "*")]
        if not global_stats["rows"]:
            return None
        parent = global_stats["residual_sum"] / global_stats["rows"]
        for key in ((fold, volume, "*"), (fold, volume, category)):
            stats = prefix[key]
            if not stats["rows"]:
                continue
            n = len(stats["targets"])
            weight = n / (n + 50.0)
            parent = weight * (stats["residual_sum"] / stats["rows"]) + (1 - weight) * parent
        return float(parent)

    decisions: list[dict[str, Any]] = []
    for group in sorted(full):
        fold, volume, category = group
        prefix_offset = offset(group)
        cell = full_offsets[fold]["volumes"][volume]
        full_offset = cell["categories"].get(category, cell)["offset"]
        support = len(prefix[group]["targets"])
        if prefix_offset is None or support < MINIMUM_TARGETS:
            selection_result: dict[str, Any] = {
                "status": "insufficient_prefix_support",
                "selected_weight": None,
            }
        else:
            selection_result = select_weight(
                selection[group], full[group], prefix_offset, full_offset
            )
        decisions.append(
            {
                "fold": fold,
                "volume": volume,
                "category": category,
                "prefix_unique_targets": support,
                "prefix_offset": prefix_offset,
                "full_validation_offset": full_offset,
                **selection_result,
            }
        )
    unresolved = sum(row["selected_weight"] is None for row in decisions)
    result = {
        "at": stamp(),
        "status": "development_probe_complete",
        "qualification_status": "not_ready",
        "counts": counts,
        "decision_count": len(decisions),
        "unresolved_groups": unresolved,
        "weights": {
            str(weight): sum(row["selected_weight"] == weight for row in decisions)
            for weight in WEIGHTS
        },
        "decisions": decisions,
        "holdout_labels_used_for_selection": False,
        "quality_threshold_changes": False,
        "new_source_generation": False,
    }
    save("completion.json", result)
    print(
        json.dumps(
            {k: result[k] for k in ("status", "decision_count", "unresolved_groups", "weights")}
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
