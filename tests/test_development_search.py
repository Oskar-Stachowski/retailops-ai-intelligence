"""Frozen compiler/durability controls; no empirical winner or Project fit claimed."""

import pytest
from pydantic import ValidationError
from test_campaign_portfolio import document
from test_development_profiles import preparation

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import CampaignPortfolioProtocol
from retailops_ai.evaluation_campaign.development_search import (
    ForecastDevelopmentSearch,
    ForecastSearchRecipe,
    compile_forecast_search_fits,
    freeze_forecast_search,
)
from retailops_ai.forecasting.model_contract import HGBConfig, RFConfig


def design(tmp_path):
    small, medium = preparation(), preparation(50)
    canonical = CampaignPortfolioProtocol.model_validate_json(
        canonical_bytes(document(tmp_path / "canonical-journal"))
    )
    budget = small.generations[0].resources.model_copy(update={"wall_seconds": 1200})
    policy = ForecastDevelopmentSearch(
        preparation_25_sha256=small.content_sha256(),
        preparation_50_sha256=medium.content_sha256(),
        canonical_portfolio_protocol_sha256=canonical.content_sha256(),
        partition_policy_sha256="4" * 64,
        critical_group_policy_sha256=canonical.selection_policy_sha256,
        recipes=(
            ForecastSearchRecipe(name="rf-a", family="rf"),
            ForecastSearchRecipe(name="rf-b", family="rf", rf=RFConfig(n_estimators=128)),
            ForecastSearchRecipe(name="hgb-a", family="hgb"),
            ForecastSearchRecipe(name="hgb-b", family="hgb", hgb=HGBConfig(max_iter=200)),
            ForecastSearchRecipe(name="tf-a", family="tensorflow"),
            ForecastSearchRecipe(name="tf-b", family="tensorflow", learning_rate=0.003),
        ),
        worker_environment_lock_sha256={"rf": "6" * 64, "hgb": "6" * 64, "tensorflow": "7" * 64},
        resources_by_products={str(n): budget for n in (25, 50, 100)},
    )
    return policy, small, medium, canonical


def test_compiled_native_plans_preserve_equal_budget_and_original_finalists(tmp_path):
    policy, small, medium, canonical = design(tmp_path)
    first = compile_forecast_search_fits(
        policy, source=small.sources[0], export_operation_id="read-25", preparation=small
    )
    assert [p.family for p in first] == ["rf", "rf", "hgb", "hgb", "tensorflow", "tensorflow"]
    assert {p.initialization_seed for p in first} == {policy.training_initialization_seed}
    assert all(p.training_population == "all_eligible_train_keys_no_sampling" for p in first)
    finalists = ("rf-b", "hgb-a", "tf-b")
    for source, extra in (
        (medium.sources[0], {"preparation": medium}),
        (canonical.sources[0], {"canonical_protocol": canonical}),
    ):
        fitted = compile_forecast_search_fits(
            policy,
            source=source,
            export_operation_id="read-finalists",
            finalist_names=finalists,
            **extra,
        )
        assert [p.family for p in fitted] == ["rf", "hgb", "tensorflow"]
        assert fitted[0].rf == first[1].rf
        assert fitted[1].hgb == first[2].hgb
        assert fitted[2].learning_rate == first[5].learning_rate
        assert len({p.resources.model_dump_json() for p in fitted}) == 1
        assert {p.source_recipe_sha256 for p in fitted} == {source.content_sha256()}
    assert policy.maximum_fit_attempts == len(first) + 3 + 3 == 12
    assert not policy.execution_authorized and not policy.stage_ready


@pytest.mark.parametrize(
    "names",
    [
        None,
        ("rf-a", "hgb-a"),
        ("rf-a", "hgb-a", "unknown"),
        ("rf-a", "rf-b", "tf-a"),
        ("tf-a", "hgb-a", "rf-a"),
    ],
)
def test_missing_new_or_unfair_finalists_are_rejected_before_any_fit(tmp_path, names):
    policy, _, medium, _ = design(tmp_path)
    with pytest.raises(ValueError, match="finalists"):
        compile_forecast_search_fits(
            policy,
            source=medium.sources[0],
            preparation=medium,
            export_operation_id="read-50",
            finalist_names=names,
        )


def test_initial_screen_cannot_skip_a_preregistered_recipe(tmp_path):
    policy, small, _, _ = design(tmp_path)
    with pytest.raises(ValueError, match="cannot_drop_screening"):
        compile_forecast_search_fits(
            policy,
            source=small.sources[0],
            preparation=small,
            export_operation_id="read-25",
            finalist_names=("rf-a", "hgb-a", "tf-a"),
        )


@pytest.mark.parametrize(
    "mutation", ["unequal", "same_effect", "longer", "less_reserve", "new_seed"]
)
def test_resealing_cannot_make_an_unfair_or_ineffective_search_valid(tmp_path, mutation):
    policy, *_ = design(tmp_path)
    raw = policy.model_dump(mode="json")
    if mutation == "unequal":
        raw["recipes"][1]["family"] = "hgb"
    elif mutation == "same_effect":
        # A TF-only knob must not count as another effective RF trial.
        raw["recipes"][1]["rf"] = raw["recipes"][0]["rf"]
        raw["recipes"][1]["learning_rate"] = 0.004
    elif mutation == "longer":
        raw["resources_by_products"]["25"]["wall_seconds"] = 1201
    elif mutation == "less_reserve":
        raw["resources_by_products"]["50"]["minimum_available_memory_bytes"] = 1000
    else:
        raw["training_initialization_seed"] = 137
    with pytest.raises(ValidationError):
        ForecastDevelopmentSearch.model_validate_json(canonical_bytes(raw))


def test_source_and_canonical_protocol_are_independent_required_bindings(tmp_path):
    policy, small, medium, canonical = design(tmp_path)
    with pytest.raises(ValueError, match="preparation_or_source_changed"):
        compile_forecast_search_fits(
            policy, source=small.sources[0], preparation=medium, export_operation_id="read"
        )
    with pytest.raises(ValueError, match="canonical_portfolio_required"):
        compile_forecast_search_fits(
            policy,
            source=canonical.sources[0],
            export_operation_id="read",
            finalist_names=("rf-a", "hgb-a", "tf-a"),
        )
    with pytest.raises(ValueError, match="declared_development_scale"):
        compile_forecast_search_fits(
            policy, source=canonical.sources[3], export_operation_id="unopened-final"
        )


def test_design_freeze_is_private_durable_and_cannot_be_overwritten(tmp_path):
    policy, *_ = design(tmp_path)
    path = tmp_path / "search.json"
    before = freeze_forecast_search(path, policy)
    assert path.stat().st_mode & 0o777 == 0o600
    assert ForecastDevelopmentSearch.model_validate_json(path.read_bytes()) == policy
    assert before == policy.content_sha256()
    with pytest.raises(FileExistsError):
        freeze_forecast_search(path, policy)
