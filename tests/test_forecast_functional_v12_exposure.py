from pathlib import Path

import pytest

from retailops_ai.forecasting.functional_v12_exposure import (
    make_freeze,
    open_holdout,
    require_complete_exposure,
    reserve_plan,
)
from retailops_ai.source_snapshot.files import SnapshotError


def _freeze(seeds: list[int], method: str = "v12") -> dict:
    return make_freeze(
        {
            "version": "forecast-functional-cohort-plan-1.0.0",
            "holdout_metrics_evaluated_before_freeze": False,
            "seeds": seeds,
            "previously_used_seeds": [42, 710001],
            "previously_used_source_ids": ["source-sha256-" + "a" * 64],
            "previously_used_snapshot_ids": ["snapshot-sha256-" + "b" * 64],
            "method": method,
        }
    )


def test_new_output_or_changed_recipe_does_not_make_test_fresh(tmp_path: Path) -> None:
    registry = tmp_path / "shared-registry"
    freeze = _freeze([720001, 720002])
    reserve_plan(registry, freeze)
    assert reserve_plan(registry, freeze) == freeze
    with pytest.raises(SnapshotError, match="already_reserved_for_different_freeze"):
        reserve_plan(registry, _freeze([720001], "new-output-new-model"))
    with pytest.raises(SnapshotError, match="incomplete_preregistered_inventory"):
        require_complete_exposure(registry, freeze)
    for seed, digest in ((720001, "c"), (720002, "d")):
        arguments = dict(
            seed=seed,
            source_dataset_id="source-sha256-" + digest * 64,
            snapshot_id="snapshot-sha256-" + digest * 64,
            fitted_recipes_sha256="e" * 64,
        )
        first = open_holdout(registry, freeze, **arguments)
        assert open_holdout(registry, freeze, **arguments) == first
        with pytest.raises(SnapshotError, match="already_exposed"):
            open_holdout(registry, freeze, **(arguments | {"fitted_recipes_sha256": "f" * 64}))
    assert len(require_complete_exposure(registry, freeze)) == 2


def test_old_data_cannot_be_requalified_with_a_new_seed(tmp_path: Path) -> None:
    with pytest.raises(SnapshotError, match="previous_exposure"):
        _freeze([42])
    freeze = _freeze([720001])
    reserve_plan(tmp_path, freeze)
    with pytest.raises(SnapshotError, match="not_independent_or_not_planned"):
        open_holdout(
            tmp_path,
            freeze,
            seed=720001,
            source_dataset_id="source-sha256-" + "a" * 64,
            snapshot_id="snapshot-sha256-" + "c" * 64,
            fitted_recipes_sha256="e" * 64,
        )
