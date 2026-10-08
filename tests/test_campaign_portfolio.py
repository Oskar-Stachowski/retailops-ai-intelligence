"""Full-scope metadata/durable controls; no Project generation, fit or final access."""

from copy import deepcopy
from datetime import date

import pytest
from pydantic import ValidationError
from test_ai09_campaign_journal import complete, protocol_document, selection

from retailops_ai.data_contracts.common import DateWindow
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign.campaign_contract import CampaignProtocol
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationPlan,
    CampaignGenerationResources,
)
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import (
    VARIANTS,
    CampaignPortfolioJournal,
    CampaignPortfolioProtocol,
    PortfolioSourceRecipe,
    parse_campaign_journal,
    parse_campaign_protocol,
)

CASES = ("forecast", "anomaly", "stockout")
KINDS = {
    "demand": "business-anomaly-plan-1.0.0",
    "physical": "business-physical-anomaly-plan-1.0.0",
}


def document(root):
    value = protocol_document(root)
    value["portfolio_version"] = "ai09-full-scenario-portfolio-1.0.0"
    sources = [
        PortfolioSourceRecipe.model_validate_json(
            canonical_bytes(
                base
                | {
                    "exporter_lock_sha256": canonical_sha256("controlled-exporter-lock"),
                    "variant": variant,
                    "scenario_plan_sha256": None
                    if variant == "ordinary"
                    else canonical_sha256({"contract_version": KINDS[variant], "fixture": True}),
                }
            )
        )
        for base in value["sources"]
        for variant in VARIANTS
    ]
    training = {case: sources[i].content_sha256() for i, case in enumerate(CASES)}
    operations = []

    def add(source, suffix, action, use_case="source", role="all_parent_data", parents=(), **extra):
        name = f"{source.phase}-{source.seed}-{source.variant}-{suffix}"
        operations.append(
            {
                "operation_id": name,
                "phase": source.phase,
                "action": action,
                "use_case": use_case,
                "role": role,
                "source_recipe_sha256": source.content_sha256(),
                "execution_recipe_sha256": canonical_sha256(["controlled-portfolio", name]),
                "prerequisites": list(parents),
            }
            | extra
        )
        return name

    readers = {}
    for source in sources[:3]:
        gen = add(source, "generate", "source_generate")
        readers[source.content_sha256()] = add(source, "read", "source_read", parents=(gen,))
    fits = {}
    for family in ("rf", "hgb", "tensorflow"):
        fits[family] = add(
            sources[0],
            "fit-" + family,
            "model_fit",
            "forecast",
            "train",
            (readers[training["forecast"]],),
            forecast_family=family,
            initialization_seed=42,
            maximum_attempts=3,
        )
    fits["forecast"] = fits["rf"]
    for index, case in ((1, "anomaly"), (2, "stockout")):
        fits[case] = add(
            sources[index],
            "fit-" + case,
            "model_fit",
            case,
            "train",
            (readers[training[case]],),
            initialization_seed=42,
            maximum_attempts=3,
        )
    calibrations = {}
    for index, case in enumerate(CASES):
        calibrations[case] = add(
            sources[index],
            "calibrate-" + case,
            "calibrator_fit",
            case,
            "calibration",
            (readers[training[case]], fits[case]),
        )
    for source in sources[:3]:
        for case in CASES:
            add(
                source,
                "evaluate-" + case,
                "model_score",
                case,
                "development_evaluation",
                (readers[source.content_sha256()], calibrations[case]),
            )
    for source in sources[3:]:
        gen = add(source, "generate", "source_generate")
        read = add(source, "read", "source_read", parents=(gen,))
        for case in CASES:
            add(source, "evaluate-" + case, "model_score", case, "final_evaluation", (read,))
    value.update(
        sources=[s.model_dump(mode="json") for s in sources],
        operations=operations,
        training_source_recipe_sha256=training,
        maximum_new_attempts=sum(o.get("maximum_attempts", 1) for o in operations),
        maximum_new_fit_attempts=sum(
            o.get("maximum_attempts", 1)
            for o in operations
            if o["action"] in ("model_fit", "calibrator_fit")
        ),
    )
    return value


def refresh_budget(value):
    value["maximum_new_attempts"] = sum(o.get("maximum_attempts", 1) for o in value["operations"])
    value["maximum_new_fit_attempts"] = sum(
        o.get("maximum_attempts", 1)
        for o in value["operations"]
        if o["action"] in ("model_fit", "calibrator_fit")
    )


def test_complete_canonical_inventory_keeps_three_sources_per_seed_and_one_journal(tmp_path):
    protocol = parse_campaign_protocol(canonical_bytes(document(tmp_path / "campaign")))
    assert isinstance(protocol, CampaignPortfolioProtocol)
    assert len(protocol.sources) == 12 and len(protocol.operations) == 68
    assert protocol.maximum_new_attempts == 78 and protocol.maximum_new_fit_attempts == 18
    assert len({s.content_sha256() for s in protocol.sources}) == 12
    assert all(s.products == (100 if s.phase == "development" else 200) for s in protocol.sources)
    ledger = journal.initialize(tmp_path / "campaign", protocol)
    restored = journal.inspect(tmp_path / "campaign")
    assert isinstance(restored, CampaignPortfolioJournal) and restored == ledger
    assert restored.protocol == protocol and len(restored.protocol.sources) == 12
    assert parse_campaign_journal(canonical_bytes(restored.model_dump(mode="json"))) == ledger
    assert not restored.stage_ready and not restored.quality_qualified


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_source",
        "duplicate_variant",
        "small_profile",
        "missing_exporter_lock",
        "different_base_configuration",
        "different_final_window",
        "final_training",
        "wrong_fit_source",
        "missing_dev_use_case",
        "missing_final_use_case",
        "wrong_cross_use_case",
        "borrowed_source_read",
        "extra_budget",
        "unfair_tf_budget",
        "generation_retry",
        "unknown_extension",
    ],
)
def test_incomplete_or_unfair_portfolio_rejected_before_any_io(tmp_path, mutation):
    value = document(tmp_path / "never-created")
    operations = value["operations"]
    if mutation == "missing_source":
        value["sources"].pop()
    elif mutation == "duplicate_variant":
        value["sources"][2] = deepcopy(value["sources"][1])
    elif mutation == "small_profile":
        value["sources"][4]["products"] = 8
    elif mutation == "missing_exporter_lock":
        value["sources"][4]["exporter_lock_sha256"] = None
    elif mutation == "different_base_configuration":
        value["sources"][2]["generation_config_sha256"] = "b" * 64
    elif mutation == "different_final_window":
        value["sources"][4]["evaluation_origins"]["end"] = "2026-09-16"
    elif mutation == "final_training":
        value["training_source_recipe_sha256"]["anomaly"] = (
            PortfolioSourceRecipe.model_validate_json(
                canonical_bytes(value["sources"][4])
            ).content_sha256()
        )
    elif mutation == "wrong_fit_source":
        next(o for o in operations if o["action"] == "model_fit")["source_recipe_sha256"] = value[
            "training_source_recipe_sha256"
        ]["anomaly"]
    elif mutation in ("missing_dev_use_case", "missing_final_use_case"):
        phase = "development" if mutation == "missing_dev_use_case" else "final"
        operations.remove(
            next(o for o in operations if o["phase"] == phase and o["action"] == "model_score")
        )
        refresh_budget(value)
    elif mutation == "wrong_cross_use_case":
        operation = next(
            o for o in operations if o["operation_id"] == "development-42-ordinary-evaluate-anomaly"
        )
        operation["prerequisites"][-1] = "development-42-physical-calibrate-stockout"
    elif mutation == "borrowed_source_read":
        operations[3]["prerequisites"] = [operations[0]["operation_id"]]
    elif mutation == "extra_budget":
        value["maximum_new_attempts"] += 1
    elif mutation == "unfair_tf_budget":
        next(o for o in operations if o.get("forecast_family") == "tensorflow")[
            "maximum_attempts"
        ] = 2
        refresh_budget(value)
    elif mutation == "generation_retry":
        operations[0]["maximum_attempts"] = 2
        refresh_budget(value)
    else:
        value["portfolio_version"] = "unknown-extension"
    with pytest.raises((ValidationError, ValueError)):
        parse_campaign_protocol(canonical_bytes(value))
    assert not (tmp_path / "never-created").exists()


def test_global_freeze_requires_every_variant_then_final_close_requires_all_seeds_and_variants(
    tmp_path,
):
    root = tmp_path / "campaign"
    protocol = CampaignPortfolioProtocol.model_validate_json(canonical_bytes(document(root)))
    journal.initialize(root, protocol)
    development = [o for o in protocol.operations if o.phase == "development"]
    for operation in development[:-1]:
        complete(root, operation.operation_id)
    before = (root / "journal.json").read_bytes()
    with pytest.raises(ValueError, match="full_development_inventory"):
        journal.freeze_selection(root, selection(root))
    assert (root / "journal.json").read_bytes() == before
    with pytest.raises(ValueError, match="phase_mismatch"):
        journal.reserve(root, "final-42-ordinary-generate")
    complete(root, development[-1].operation_id)
    journal.freeze_selection(root, selection(root))
    with pytest.raises(ValueError, match="phase_mismatch"):
        journal.reserve(root, development[0].operation_id)
    final = [o for o in protocol.operations if o.phase == "final"]
    for operation in final[:-1]:
        complete(root, operation.operation_id)
    with pytest.raises(ValueError, match="resolved_final_execution"):
        journal.close(root, canonical_sha256("controlled-report"))
    complete(root, final[-1].operation_id)
    journal.close(root, canonical_sha256("controlled-report"))
    ledger = journal.inspect(root)
    assert isinstance(ledger, CampaignPortfolioJournal) and ledger.events[-1].kind == "closed"
    assert not ledger.stage_ready and not ledger.final_holdout_freshness_qualified


def test_failed_generation_cost_and_slot_survive_reload_without_refund(tmp_path):
    root = tmp_path / "campaign"
    protocol = CampaignPortfolioProtocol.model_validate_json(canonical_bytes(document(root)))
    journal.initialize(root, protocol)
    with pytest.raises(RuntimeError, match="owned-control-failure"):
        with journal.audited_operation(root, "development-42-demand-generate"):
            raise RuntimeError("owned-control-failure")
    ledger = journal.inspect(root)
    assert ledger.events[-1].result == "failed" and ledger.events[-1].cost.wall_seconds >= 0
    with pytest.raises(ValueError, match="budget_exhausted"):
        journal.reserve(root, "development-42-demand-generate")
    assert journal.inspect(root) == ledger


def test_old_v10_protocol_remains_old_and_rejects_extension_as_old_wire(tmp_path):
    old = protocol_document(tmp_path / "old")
    assert type(parse_campaign_protocol(canonical_bytes(old))) is CampaignProtocol
    with pytest.raises(ValidationError):
        CampaignProtocol.model_validate_json(canonical_bytes(document(tmp_path / "portfolio")))


@pytest.mark.parametrize("variant", VARIANTS)
def test_actual_generation_binding_requires_exact_original_plan_kind_and_hash(variant):
    parameters = {
        "profile": "ai-dev",
        "seed": 42,
        "days": 365,
        "products": 100,
        "stores": 5,
        "warehouses": 3,
        "start_date": "2025-08-01",
        "end_date": "2026-07-31",
        "business_timezone": "UTC",
        "forecast_plan_days": 14,
    }
    scenario = (
        None if variant == "ordinary" else {"contract_version": KINDS[variant], "fixture": True}
    )
    if variant == "physical":
        scenario["injections"] = [{"injection_type": "inventory_censored_episode"}]
    source = PortfolioSourceRecipe(
        phase="development",
        seed=42,
        producer_commit="a" * 40,
        producer_lock_sha256="b" * 64,
        exporter_lock_sha256="c" * 64,
        generation_config_sha256=canonical_sha256(parameters),
        profile="ai-dev",
        history=DateWindow(start=date(2025, 8, 1), end=date(2026, 7, 31)),
        products=100,
        selling_pairs=5,
        stock_locations=3,
        variant=variant,
        scenario_plan_sha256=None if scenario is None else canonical_sha256(scenario),
    )
    plan = CampaignGenerationPlan(
        source_recipe_sha256=source.content_sha256(),
        exporter_lock_sha256="c" * 64,
        requested_parameters=parameters,
        resolved_parameters=parameters,
        entrypoint="cached_inventory_v2" if variant == "ordinary" else "planned_anomaly",
        scenario_plan=scenario,
        snapshot_schema_version="1.1.0" if variant == "ordinary" else "1.2.0",
        required_use_cases=("forecast_source", "inventory_source")
        + (() if variant == "ordinary" else ("anomaly_source",)),
        resources=CampaignGenerationResources(
            wall_seconds=3600,
            tree_rss_bytes=8 * 1024**3,
            scratch_bytes=8 * 1024**3,
            minimum_free_disk_bytes=6 * 1024**3,
            minimum_available_memory_bytes=1024**3,
        ),
    )
    plan.bind(source)
    if variant != "ordinary":
        changed = plan.model_dump(mode="json")
        changed["scenario_plan"]["fixture"] = False
        with pytest.raises(ValueError, match="variant_or_plan_mismatch"):
            CampaignGenerationPlan.model_validate_json(canonical_bytes(changed)).bind(source)
    if variant == "physical":
        changed = plan.model_dump(mode="json")
        changed["scenario_plan"]["injections"] = [{"injection_type": "return_spike"}]
        changed_source = source.model_dump(mode="json")
        changed_source["scenario_plan_sha256"] = canonical_sha256(changed["scenario_plan"])
        changed_recipe = PortfolioSourceRecipe.model_validate_json(canonical_bytes(changed_source))
        changed["source_recipe_sha256"] = changed_recipe.content_sha256()
        with pytest.raises(ValueError, match="variant_or_plan_mismatch"):
            CampaignGenerationPlan.model_validate_json(canonical_bytes(changed)).bind(
                changed_recipe
            )
