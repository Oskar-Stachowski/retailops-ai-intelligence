"""Raw functionals, complete populations, sparse segments and frozen bounded attempts."""

from contextlib import contextmanager
from dataclasses import replace

import pytest
from pydantic import ValidationError
from test_forecast_features import tables as tables
from test_forecast_manifests import timeline as timeline
from test_tensorflow_challenger import FEATURE_ID, SPLIT_ID
from test_tensorflow_challenger import development as development

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign.development_contract import (
    MODELS,
    DevelopmentComparisonPolicy,
    DevelopmentPrediction,
    DevelopmentProtocol,
)
from retailops_ai.evaluation_campaign.development_metrics import (
    baseline_predictions,
    comparison_metrics,
)
from retailops_ai.forecasting.contract import Parent
from retailops_ai.forecasting.quality_v2_contract import FunctionalForecast
from retailops_ai.source_snapshot.files import SnapshotError


def predictions(validation):
    output = baseline_predictions(validation)
    for name in ("rf_mean", "hgb", "tensorflow"):
        output[name] = tuple(
            DevelopmentPrediction(
                **s.row.model_dump(include=set(DevelopmentPrediction.model_fields)),
                value=FunctionalForecast(
                    mean=5.0, median=None if name == "rf_mean" else 4.0, interval=None
                ),
            )
            for w in validation
            for s in w.samples
        )
    return output


def test_rf_mean_is_not_reported_as_a_median_and_sparse_groups_remain(development):
    _, train, validation = development
    output = comparison_metrics(validation, predictions(validation), train)
    rf = output["models"]["rf_mean"]["global"]["candidate"]
    assert rf["mean"]["complete"] and rf["mean"]["mse"] > 0
    assert rf["median"]["mae"] is None and rf["interval"]["coverage"] is None
    assert set(output["models"]) == set(MODELS)
    assert output["prediction_rows_per_model"] == 14
    for model in output["models"].values():
        assert set(model["segments"]["volume"]) == {"zero", "low", "medium", "high"}
        assert set(model["segments"]["horizon"]) == {str(h) for h in range(1, 15)}
        assert "insufficient_sample" in model["segments"]["volume"]["zero"]["not_ready_reasons"]
    assert output["deployment_status"] == "not_ready"
    assert not output["final_test_accessed"] and not output["promotion_allowed"]


@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "different_grain", "rf_median", "missing_model"]
)
def test_invalid_population_or_functional_fails_closed(development, mutation):
    _, _, validation = development
    output = predictions(validation)
    if mutation == "missing":
        output["tensorflow"] = output["tensorflow"][:-1]
    elif mutation == "duplicate":
        output["tensorflow"] = (*output["tensorflow"], output["tensorflow"][0])
    elif mutation == "different_grain":
        output["hgb"] = (
            output["hgb"][0].model_copy(update={"channel": "online"}),
            *output["hgb"][1:],
        )
    elif mutation == "rf_median":
        output["rf_mean"] = (
            output["rf_mean"][0].model_copy(
                update={"value": FunctionalForecast(mean=5.0, median=5.0, interval=None)}
            ),
            *output["rf_mean"][1:],
        )
    else:
        del output["history7"]
    with pytest.raises(SnapshotError):
        comparison_metrics(validation, output)


def test_zero_actuals_are_not_reported_as_perfect_ratios(development):
    _, _, validation = development
    modified = tuple(
        replace(
            w,
            samples=tuple(
                replace(s, label=s.label.model_copy(update={"observed_sales_units": 0}))
                for s in w.samples
            ),
        )
        for w in validation
    )
    report = comparison_metrics(modified, predictions(modified))
    for name in ("rf_mean", "hgb", "tensorflow"):
        metrics = report["models"][name]["global"]
        assert metrics["candidate"]["mean"]["wape"] is None
        assert "positive_mean_forecast_on_all_zero_actuals" in metrics["failed_reasons"]


def test_excluded_points_stay_in_the_population_and_cannot_have_predictions(development):
    _, _, validation = development
    first = validation[0].samples[0]
    excluded = replace(
        first,
        membership=first.membership.model_copy(
            update={"eligible": False, "reasons": ("closed_target",)}
        ),
    )
    changed = (replace(validation[0], samples=(excluded, *validation[0].samples[1:])),)
    output = predictions(changed)
    with pytest.raises(SnapshotError, match="excluded_prediction"):
        comparison_metrics(changed, output)
    for name in ("rf_mean", "hgb", "tensorflow"):
        output[name] = (
            output[name][0].model_copy(
                update={"value": FunctionalForecast(mean=None, median=None, interval=None)}
            ),
            *output[name][1:],
        )
    report = comparison_metrics(changed, output)
    for model in report["models"].values():
        assert model["global"]["total_rows"] == 14 and model["global"]["eligible_rows"] == 13


def test_train_population_cannot_be_scored_as_validation(development):
    _, train, _ = development
    with pytest.raises(SnapshotError, match="requires_validation"):
        comparison_metrics(train, predictions(train))


@pytest.mark.parametrize(
    "flags",
    [
        {"promotion_allowed": True},
        {"final_test_accessed": True},
        {"tree_heads": ("rf_mean",)},
        {"reference": "select_best_on_results"},
        {"max_output_bytes": 129 * 1024**2},
    ],
)
def test_policy_cannot_relax_scope_or_choose_reference_after_results(flags):
    with pytest.raises(ValidationError):
        DevelopmentComparisonPolicy(**flags)


def protocol_for(fold, policy=None):
    """Controlled internal fixture, never represented as a qualified source run."""
    return DevelopmentProtocol(
        policy=policy or DevelopmentComparisonPolicy(),
        feature_set_id=FEATURE_ID,
        split_id=SPLIT_ID,
        parent=Parent(
            source_dataset_id="source-sha256-" + "a" * 64,
            curated_dataset_id="curated-sha256-" + "b" * 64,
            snapshot_id="snapshot-sha256-" + "c" * 64,
            curated_descriptor_sha256="d" * 64,
            business_timezone="UTC",
            forecast_source_status="passed",
        ),
        source_parameters={"profile": "controlled-internal-fixture", "seed": 42},
        source_schema_version="2.7.0",
        fold=fold,
        feature_descriptor_sha256="a" * 64,
        split_descriptor_sha256="b" * 64,
        train_population_sha256="c" * 64,
        validation_population_sha256="d" * 64,
        core_environment={"scope": "controlled-internal-fixture"},
        tensorflow_lock_sha256="e" * 64,
        implementation_sha256="f" * 64,
    )


def test_failed_worker_keeps_frozen_protocol_and_refuses_retry(development, tmp_path, monkeypatch):
    import json

    from retailops_ai.evaluation_campaign import development as benchmark

    fold, train, validation = development
    protocol = protocol_for(fold)

    @contextmanager
    def parents(*args):
        yield None, None, fold, train, validation

    monkeypatch.setattr(benchmark, "development_parents", parents)
    monkeypatch.setattr(benchmark, "_protocol", lambda *args: protocol)

    def fail(*args, **kwargs):
        raise SnapshotError("controlled_worker_failure")

    monkeypatch.setattr(benchmark, "_trees", fail)
    output = tmp_path / "attempt"
    inputs = {
        "features": tmp_path / "features",
        "split": tmp_path / "split",
        "curated": tmp_path / "curated",
        "fold_name": fold.name,
        "output": output,
    }
    with pytest.raises(SnapshotError, match="controlled_worker_failure"):
        benchmark.run_development_comparison(**inputs)
    assert json.loads((output / "attempt.json").read_text())["status"] == "failed"
    assert canonical_sha256(json.loads((output / "protocol.json").read_text())) == canonical_sha256(
        protocol.model_dump(mode="json")
    )
    assert '"event":"failed"' in (output / "trials.jsonl").read_text()
    assert not (output / "manifest.json").exists()
    before = (output / "trials.jsonl").read_bytes()
    with pytest.raises(FileExistsError):
        benchmark.run_development_comparison(**inputs)
    assert (output / "trials.jsonl").read_bytes() == before


def test_tree_matrix_budget_is_checked_before_allocation(development):
    from retailops_ai.evaluation_campaign.development import _tree_state
    from retailops_ai.forecasting.functional_contract import FunctionalPolicy
    from retailops_ai.forecasting.model_contract import ModelPolicy

    fold, train, _ = development
    policy = DevelopmentComparisonPolicy(
        trees=FunctionalPolicy(model=ModelPolicy(max_matrix_bytes=1024))
    )
    with pytest.raises(SnapshotError, match="tree_matrix_budget"):
        _tree_state(train, protocol_for(fold, policy))
