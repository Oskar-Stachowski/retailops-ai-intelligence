"""Profile/binding controls, not generated-world or critical-coverage evidence."""

from copy import deepcopy
from uuid import UUID

import pytest
from pydantic import ValidationError

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_contract import CampaignSourceRecipe
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationResources,
)
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import PortfolioSourceRecipe
from retailops_ai.evaluation_campaign.development_profiles import (
    DevelopmentProfile,
    DevelopmentProfilePreparation,
    prepare_development_profile,
)


def plans():
    """Valid native plan shapes with explicitly controlled, non-Project IDs."""
    result = {}
    for variant, types in (
        ("demand", ("one_day_spike", "multi_day_spike", "sustained_drop")),
        ("physical", ("return_spike", "inventory_censored_episode")),
    ):
        physical = variant == "physical"
        generator = "business-" + ("physical-" if physical else "") + "anomaly-generator-1.0.0"
        injections, controls = [], []
        for i, kind in enumerate(types):
            grain = dict(
                product_id=str(UUID(int=i + (10 if physical else 1))),
                selling_location_id=str(UUID(int=100)),
                channel="online",
            )
            injection = dict(
                id=str(UUID(int=200 + i)),
                **grain,
                start_date="2026-02-02",
                end_date="2026-02-02" if kind == "one_day_spike" else "2026-02-04",
                injection_type=kind,
                shape="constant_multiplier",
                magnitude="0.2" if kind == "sustained_drop" else "3",
                affected_fields=["expected_rate", "latent_units"],
                seed=42,
                generator_version=generator,
            )
            if kind == "return_spike":
                injection.update(
                    shape="return_probability_multiplier",
                    affected_fields=["return_selection_probability"],
                )
            if kind == "inventory_censored_episode":
                injection.update(
                    stock_location_id=str(UUID(int=500)),
                    shape="available_stock_cap",
                    magnitude=0,
                    affected_fields=["available_qty", "observed_sales_units"],
                )
            injections.append(injection)
            controls.append(
                dict(
                    id=str(UUID(int=300 + i)),
                    **grain,
                    start_date="2026-01-05",
                    end_date="2026-01-07",
                    control_type="clean",
                )
            )
        result[variant] = dict(
            contract_version="business-" + ("physical-" if physical else "") + "anomaly-plan-1.0.0",
            data_class="simulation_truth",
            generator_version=generator,
            seed=42,
            business_timezone="UTC",
            boundary_policy="inclusive_business_dates",
            overlap_policy="reject_shared_stock_and_return_spillover" if physical else "reject",
            injections=injections,
            controls=controls,
        )
        if not physical:
            result[variant]["minimum_history_observations"] = 28
    return result


def preparation(products=25, *, scenarios=None, resources=None):
    return prepare_development_profile(
        DevelopmentProfile(name=f"ai09-development-{products}-v1", products=products),
        producer_commit="1" * 40,
        producer_lock_sha256="2" * 64,
        exporter_lock_sha256="3" * 64,
        scenario_plans=plans() if scenarios is None else scenarios,
        resources=CampaignGenerationResources(
            wall_seconds=10800,
            tree_rss_bytes=12 * 1024**3,
            scratch_bytes=8 * 1024**3,
            minimum_free_disk_bytes=6 * 1024**3,
            minimum_available_memory_bytes=1024**3,
        )
        if resources is None
        else resources,
    )


@pytest.mark.parametrize("products,upper", [(25, 45625), (50, 91250)])
def test_all_native_variants_keep_full_history_and_locations(products, upper):
    value = preparation(products)
    assert value.profile.requested_parameters()["max_daily_rows"] == upper
    for source, generation in zip(value.sources, value.generations, strict=True):
        generation.bind(source)
        assert (source.products, source.selling_pairs, source.stock_locations) == (products, 5, 3)
        assert (source.history.end - source.history.start).days + 1 == 365
        assert generation.resolved_parameters["forecast_plan_days"] == 14
        assert generation.resolved_parameters["label_tail_days"] == 0
        assert not generation.include_truth
    assert not value.model_fit_authorized and not value.actual_coverage_verified
    assert DevelopmentProfilePreparation.model_validate_json(value.model_dump_json()) == value


def test_smaller_sources_cannot_enter_an_unchanged_canonical_wire():
    small, larger = preparation(), preparation(50)
    assert {s.content_sha256() for s in small.sources}.isdisjoint(
        s.content_sha256() for s in larger.sources
    )
    for source in small.sources:
        for model in (CampaignSourceRecipe, PortfolioSourceRecipe):
            with pytest.raises(ValidationError):
                model.model_validate_json(source.model_dump_json())
        raw = source.model_dump(mode="json")
        raw.pop("development_profile")
        with pytest.raises(ValidationError, match="development_profile_mismatch"):
            PortfolioSourceRecipe.model_validate_json(canonical_bytes(raw))


@pytest.mark.parametrize(
    "patch",
    [
        {"name": "ai09-development-50-v1"},
        {"days": 300},
        {"selling_pairs": 2},
        {"stock_locations": 2},
        {"data_seed": 137},
        {"history": {"start": "2026-08-01", "end": "2027-07-31"}},
        {"forecast_plan_days": 0},
        {"variants": ["ordinary", "demand"]},
        {"quality_qualified": True},
    ],
)
def test_resealed_profile_cannot_reduce_time_space_or_claim_qualification(patch):
    raw = preparation().profile.model_dump(mode="json") | patch
    with pytest.raises(ValidationError):
        DevelopmentProfile.model_validate_json(canonical_bytes(raw))


@pytest.mark.parametrize("variant", ["demand", "physical"])
def test_missing_anomaly_type_or_past_control_is_not_a_complete_profile(variant):
    scenario = plans()
    scenario[variant]["injections"].pop()
    with pytest.raises(
        ValidationError, match="all_planned_anomaly_types_required|variant_or_plan_mismatch"
    ):
        preparation(scenarios=scenario)
    scenario = plans()
    scenario[variant]["controls"][0]["start_date"] = "2025-07-01"
    with pytest.raises(ValidationError, match="outside_history"):
        preparation(scenarios=scenario)


def test_resealing_a_reduced_or_changed_generation_does_not_restore_binding():
    raw = preparation().model_dump(mode="json")
    raw["generations"][1]["resolved_parameters"]["products"] = 20
    raw["sources"][1]["generation_config_sha256"] = canonical_sha256(
        raw["generations"][1]["resolved_parameters"]
    )
    with pytest.raises(ValidationError, match="source_scope_mismatch"):
        DevelopmentProfilePreparation.model_validate_json(canonical_bytes(raw))
    raw = preparation().model_dump(mode="json")
    raw["generations"][2]["scenario_plan"]["injections"][0]["magnitude"] = "4"
    with pytest.raises(ValidationError, match="variant_or_plan_mismatch"):
        DevelopmentProfilePreparation.model_validate_json(canonical_bytes(raw))


@pytest.mark.parametrize(
    "field,changed",
    [
        ("tree_rss_bytes", 13 * 1024**3),
        ("scratch_bytes", 9 * 1024**3),
        ("wall_seconds", 10801),
        ("minimum_free_disk_bytes", 5 * 1024**3),
        ("minimum_available_memory_bytes", 1024**3 - 1),
    ],
)
def test_profiles_cannot_relax_resources_or_reserves(field, changed):
    resources = preparation().generations[0].resources.model_copy(update={field: changed})
    with pytest.raises(ValidationError, match="resource_ceiling"):
        preparation(resources=resources)


def test_compiler_does_not_change_the_supplied_scenario_documents():
    original = plans()
    before = deepcopy(original)
    preparation(scenarios=original)
    assert original == before
