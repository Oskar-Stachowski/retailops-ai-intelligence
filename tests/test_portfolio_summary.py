"""Three complete frozen reports, equal weights and conservative candidate decisions."""

from copy import deepcopy

import pytest
from test_portfolio_assessment import rows

from retailops_ai.evaluation_campaign.portfolio_assessment import fit_intervals, metrics
from retailops_ai.evaluation_campaign.portfolio_summary import SEEDS, aggregate
from retailops_ai.source_snapshot.files import SnapshotError

FREEZE = "a" * 64
GROUPS = {"volume": ["low", "high"]}


@pytest.fixture(scope="module")
def original_reports():
    report = metrics(rows("final_test"), fit_intervals(rows()), GROUPS)
    return {
        seed: deepcopy(report) | {"seed": seed, "freeze_sha256": FREEZE, "final_test_opened": True}
        for seed in SEEDS
    }


def test_equal_seed_weights_do_not_pool_different_population_sizes(original_reports):
    reports = deepcopy(original_reports)
    for seed, value, count in zip(SEEDS, [1.0, 10.0, 100.0], [1400, 14000, 2800], strict=True):
        reports[seed]["rows_per_model"] = count
        reports[seed]["models"]["tensorflow"]["global"]["candidate"]["mean"]["mae"] = value
    result = aggregate(reports, freeze_sha256=FREEZE, groups=GROUPS)
    metric = result["models"]["tensorflow"]["mean"]["mae"]
    assert metric["equal_seed_mean"] == 37
    assert metric["minimum"] == 1 and metric["maximum"] == 100
    assert metric["population_stddev_across_three_fixed_seeds"] == pytest.approx(44.6989932773)
    assert result["models"]["rf_mean"]["median"]["mae"]["equal_seed_mean"] is None
    assert result["tensorflow_decision"] == "reject_for_new_promotion_retain_existing_portfolio"
    assert any(
        r["group"] == "volume:high" and r["status"] == "not_ready"
        for r in result["decision_reasons"]
    )
    assert result["stage_ready_granted_by_summary"] is False


@pytest.mark.parametrize("change", ["missing_seed", "different_freeze", "missing_group"])
def test_incomplete_or_mixed_final_reports_cannot_support_a_decision(original_reports, change):
    reports = deepcopy(original_reports)
    if change == "missing_seed":
        reports.pop(137)
    elif change == "different_freeze":
        reports[137]["freeze_sha256"] = "b" * 64
    else:
        reports[137]["models"]["tensorflow"]["segments"]["volume"].pop("high")
    with pytest.raises(SnapshotError):
        aggregate(reports, freeze_sha256=FREEZE, groups=GROUPS)


def test_one_undefined_ratio_does_not_silently_reweight_two_remaining_seeds(original_reports):
    reports = deepcopy(original_reports)
    reports[137]["models"]["tensorflow"]["global"]["candidate"]["mean"]["wape"] = None
    result = aggregate(reports, freeze_sha256=FREEZE, groups=GROUPS)
    metric = result["models"]["tensorflow"]["mean"]["wape"]
    assert metric["per_seed"]["42"] is not None
    assert metric["equal_seed_mean"] is None and metric["complete"] is False
