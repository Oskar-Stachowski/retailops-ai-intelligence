"""Explicit same-data recovery cannot add seeds, fits or silently replace receipts."""

from copy import deepcopy

import pytest

from scripts import run_ai09_portfolio_final_repair as repair


@pytest.mark.parametrize("seed", [42, 137, 2026])
def test_repair_binds_each_original_failure_and_unchanged_final_freeze(seed):
    plan = repair.frozen_plan(seed)
    original = plan["seeds"][str(seed)]
    assert original["original"]["run_id"] == 38384080156
    assert original["original"]["head"] == "598172aa1672b9e4f68c277cdadecadfdbfdf728"
    assert set(original["receipt_sha256"]) == {
        "result.json",
        "intent.json",
        "identity.json",
        "import.json",
        "curation.json",
        "prepare.log",
    }
    assert plan["repeat_final_parent_access_disclosed"] is True


@pytest.mark.parametrize(
    "change", ["new_seed", "more_attempts", "refit", "calibration", "new_source"]
)
def test_repair_cannot_expand_the_experiment(change, monkeypatch):
    plan = deepcopy(repair.frozen_plan(42))
    seed = 42
    if change == "new_seed":
        seed = 43
    elif change == "more_attempts":
        plan["maximum_additional_assessment_attempts_per_seed"] = 2
    else:
        plan[
            {
                "refit": "models_refitted",
                "calibration": "calibration_changed",
                "new_source": "source_regenerated",
            }[change]
        ] = True
    read = repair.probe.read
    monkeypatch.setattr(
        repair.probe, "read", lambda path: plan if path == repair.PLAN else read(path)
    )
    with pytest.raises(ValueError, match="scope_changed"):
        repair.frozen_plan(seed)


def test_changed_original_receipt_blocks_before_any_parent_access(tmp_path):
    original = tmp_path / "original/final"
    original.mkdir(parents=True)
    (original / "result.json").write_text("{}\n")
    with pytest.raises(ValueError, match="original_receipt_changed"):
        repair.original_binding(tmp_path, 42, repair.frozen_plan(42))
