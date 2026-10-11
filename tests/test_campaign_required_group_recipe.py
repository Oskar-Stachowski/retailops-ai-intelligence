"""Prospective ownership with real binding checks on explicit small source doubles."""

from copy import deepcopy

import pytest
import test_campaign_segments as controls
from pydantic import ValidationError
from test_campaign_evaluation_data import evaluation_plan
from test_campaign_required_groups import preregistration

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign.campaign_required_group_contract import STRUCTURAL_DIAGNOSTICS
from retailops_ai.evaluation_campaign.campaign_required_group_recipe import (
    CampaignRequiredGroupRecipe,
    parse_required_group_policy,
)
from retailops_ai.evaluation_campaign.campaign_segment_contract import required_population_ids
from retailops_ai.source_snapshot.files import SnapshotError


@pytest.mark.parametrize("procedural", [False, True])
def test_known_policy_versions_parse_without_downgrade(tmp_path, procedural):
    _, policy = preregistration(tmp_path, procedural=procedural)
    parsed = parse_required_group_policy(policy.model_dump_json())
    assert type(parsed) is type(policy) and parsed == policy


@pytest.mark.parametrize("raw", [b"[]", b"null", b"{}", b'{"version":"future"}'])
def test_unknown_policy_version_never_infers_old_format(raw):
    with pytest.raises(SnapshotError, match="version_invalid"):
        parse_required_group_policy(raw)


@pytest.mark.parametrize(
    "attack",
    [
        "missing_variant",
        "duplicate_variant",
        "unordered_variant",
        "duplicate_group",
        "unordered_group",
        "unknown_group",
        "missing_common",
        "missing_scenario_owner",
        "missing_cold_start",
        "missing_anomaly",
        "ready_flag",
    ],
)
def test_incomplete_recipe_rejected_before_source_generation(tmp_path, attack):
    root = tmp_path / "unopened"
    _, policy = preregistration(root, procedural=True)
    raw = policy.model_dump(mode="json")
    variants = raw["variants"]
    if attack == "missing_variant":
        variants.pop()
    elif attack == "duplicate_variant":
        variants[-1] = deepcopy(variants[0])
    elif attack == "unordered_variant":
        variants.reverse()
    elif attack == "duplicate_group":
        variants[0]["required"].append(deepcopy(variants[0]["required"][0]))
    elif attack == "unordered_group":
        variants[0]["required"].reverse()
    elif attack == "unknown_group":
        variants[0]["required"].append({"dimension": "volume", "value": "undeclared"})
        variants[0]["required"].sort(key=lambda g: (g["dimension"], g["value"]))
    elif attack == "ready_flag":
        raw["stage_ready"] = True
    else:
        identity = {
            "missing_common": ("category", "c2"),
            "missing_scenario_owner": ("scenario", "promotion"),
            "missing_cold_start": ("history", "cold_start"),
            "missing_anomaly": ("anomaly", "multi_day_spike"),
        }[attack]
        for variant in variants[:1] if attack == "missing_common" else variants:
            variant["required"] = [
                g for g in variant["required"] if (g["dimension"], g["value"]) != identity
            ]
    with pytest.raises(ValidationError):
        CampaignRequiredGroupRecipe.model_validate_json(canonical_bytes(raw))
    assert not root.exists()


def declared_scope(protocol, policy, source):
    segment = policy.segment_policy
    role = "final_test" if source.phase == "final" else "development_evaluation"
    scope = controls.scope(
        segment,
        source_recipe_sha256=source.content_sha256(),
        source_scenario_plan_sha256=source.scenario_plan_sha256,
        data_seed=source.seed,
        role=role,
        dataset_id="ai09-"
        + ("final" if source.phase == "final" else "physical")
        + "-forecast-sha256-"
        + "a" * 64,
    )
    census = controls.census(
        [controls.context(*controls.inputs(), p=segment, s=scope)], p=segment, s=scope
    )
    plan = evaluation_plan(
        role=role,
        source_recipe_sha256=source.content_sha256(),
        segment_policy_sha256=segment.content_sha256(),
    )
    return plan, census


def test_every_seed_requires_same_complete_ownership_bound_to_actual_source(tmp_path):
    protocol, recipe = preregistration(tmp_path / "unopened", procedural=True)
    required = set(required_population_ids(recipe.segment_policy)) - STRUCTURAL_DIAGNOSTICS
    seen = {}
    for source in protocol.sources:
        plan, census = declared_scope(protocol, recipe, source)
        groups = recipe.groups_for(protocol, plan, census)
        if source.variant in seen:
            assert seen[source.variant] == groups
        seen[source.variant] = groups
        assert groups <= set(required_population_ids(recipe.segment_policy))
    assert required <= set().union(*seen.values())
    assert not (tmp_path / "unopened").exists()


@pytest.mark.parametrize(
    "attack", ["protocol", "policy", "source", "scenario", "seed", "role", "segment"]
)
def test_binding_cannot_switch_source_or_scope_after_freeze(tmp_path, attack):
    protocol, recipe = preregistration(tmp_path, procedural=True)
    source = next(
        s
        for s in protocol.sources
        if s.phase == "final" and s.seed == 137 and s.variant == "physical"
    )
    plan, census = declared_scope(protocol, recipe, source)
    if attack == "protocol":
        protocol = protocol.model_copy(update={"selection_policy_sha256": "0" * 64})
    elif attack == "policy":
        raw = recipe.model_dump(mode="json")
        raw["variants"][0]["required"].append({"dimension": "history", "value": "cold_start"})
        raw["variants"][0]["required"].sort(key=lambda g: (g["dimension"], g["value"]))
        recipe = CampaignRequiredGroupRecipe.model_validate_json(canonical_bytes(raw))
    else:
        change = {
            "source": {"source_recipe_sha256": "0" * 64},
            "scenario": {"source_scenario_plan_sha256": "0" * 64},
            "seed": {"data_seed": 2026},
            "role": {"role": "development_evaluation"},
            "segment": {"segment_policy_sha256": "0" * 64},
        }[attack]
        census = census.model_copy(update={"scope": census.scope.model_copy(update=change)})
    with pytest.raises((SnapshotError, ValidationError)):
        recipe.groups_for(protocol, plan, census)
