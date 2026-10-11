"""Original canonical contracts and real controllers with declared Source worker doubles."""

import pytest
from pydantic import ValidationError
from test_development_planning import case as small_case
from test_development_planning import controlled_worker, generate, plan

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign import development_planning as runner
from retailops_ai.evaluation_campaign.campaign_contract import CampaignSourceRecipe
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import (
    PortfolioSourceRecipe,
    parse_campaign_protocol,
)
from retailops_ai.evaluation_campaign.canonical_development_contract import (
    CanonicalDevelopmentPlanningJournal,
    CanonicalDevelopmentPlanningProtocol,
    ResolvedCanonicalDevelopmentPlanningReceipt,
    canonical_requested_parameters,
    compile_canonical_development_planning,
)
from retailops_ai.evaluation_campaign.development_planning_contract import (
    DevelopmentPlanningContext,
    NativeDevelopmentPlanningProtocol,
    ResolvedNativeDevelopmentPlanningReceipt,
    parse_native_planning_receipt,
    planning_operations,
)
from retailops_ai.source_snapshot.files import SnapshotError


def case(tmp_path):
    template = tmp_path / "template"
    template.mkdir()
    _, _, small = small_case(template)
    source = small.sources[0]
    root = tmp_path / "canonical-journal"
    protocol = compile_canonical_development_planning(
        root,
        context=DevelopmentPlanningContext.model_validate(
            {k: getattr(small, k) for k in DevelopmentPlanningContext.model_fields}
        ),
        producer_commit=source.producer_commit,
        producer_lock_sha256=source.producer_lock_sha256,
        exporter_lock_sha256=source.exporter_lock_sha256,
        producer_planner_sha256=small.planning.producer_planner_sha256,
        partitions=small.planning.partitions,
        generation_resources=small.generation.resources,
        planning_resources=small.planning.resources,
        maximum_total_wall_seconds=small.maximum_total_wall_seconds,
    )
    journal.initialize(root, protocol)
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    return root, output, protocol


def test_original_canonical_source_wire_and_full_profile_are_preserved(tmp_path):
    root, _, protocol = case(tmp_path)
    assert isinstance(journal.inspect(root), CanonicalDevelopmentPlanningJournal)
    assert parse_campaign_protocol(protocol.model_dump_json().encode()) == protocol
    source = protocol.sources[0]
    assert (
        type(source) is PortfolioSourceRecipe and "development_profile" not in source.model_dump()
    )
    assert PortfolioSourceRecipe.model_validate_json(source.model_dump_json()) == source
    base = source.model_dump(exclude={"variant", "scenario_plan_sha256"}, mode="json")
    assert CampaignSourceRecipe.model_validate_json(canonical_bytes(base)).products == 100
    assert protocol.generation.requested_parameters == canonical_requested_parameters()
    assert protocol.maximum_new_attempts == 2 and protocol.maximum_new_fit_attempts == 0
    assert not protocol.promotion_allowed and not protocol.quality_qualified
    with pytest.raises(ValidationError):
        NativeDevelopmentPlanningProtocol.model_validate_json(protocol.model_dump_json())


@pytest.mark.parametrize("change", ["products", "history", "ram", "reserve", "attempts"])
def test_changed_scope_or_budget_is_rejected_before_any_model_trial(tmp_path, change):
    _, _, protocol = case(tmp_path)
    document = protocol.model_dump(mode="json")
    if change == "products":
        document["sources"][0]["products"] = 50
    elif change == "history":
        document["sources"][0]["history"] = {"start": "2025-07-01", "end": "2026-06-30"}
    elif change == "ram":
        document["planning"]["resources"]["tree_rss_bytes"] = 13 * 1024**3
    elif change == "reserve":
        document["planning"]["resources"]["minimum_available_memory_bytes"] = 1024**3 - 1
    else:
        document["maximum_new_attempts"] = 3
    with pytest.raises(ValidationError):
        CanonicalDevelopmentPlanningProtocol.model_validate_json(canonical_bytes(document))


def test_completed_original_generation_and_native_planning_keep_receipts_and_costs(
    tmp_path, monkeypatch
):
    root, output, protocol = case(tmp_path)
    generated = generate(monkeypatch, root, output, protocol)
    cost = journal.inspect(root).events[-1].cost
    calls = controlled_worker(monkeypatch, root)
    bundle, receipt = plan(root, output, generated)
    assert isinstance(receipt, ResolvedCanonicalDevelopmentPlanningReceipt)
    runner.validate_completed_native_planning(root, receipt)
    assert parse_native_planning_receipt(receipt) == receipt
    assert receipt.preparation.sources[0] == protocol.sources[0]
    assert receipt.preparation.generations[0] == protocol.generation
    assert receipt.generation_cost == cost
    assert [s.variant for s in receipt.preparation.sources] == ["ordinary", "demand", "physical"]
    assert all(s.products == 100 for s in receipt.preparation.sources)
    assert not receipt.model_fit_authorized and not receipt.preparation.stage_ready
    assert len(calls) == 1 and bundle.is_file()
    assert journal.summary(root)["charged_new_attempts"] == 2
    assert journal.summary(root)["charged_new_fit_attempts"] == 0
    with pytest.raises(ValidationError):
        ResolvedNativeDevelopmentPlanningReceipt.model_validate_json(receipt.model_dump_json())
    original = root / "receipts" / (generated.reservation_id + ".json")
    raw = original.read_bytes()
    before = journal.inspect(root)
    original.unlink()
    try:
        with pytest.raises((OSError, SnapshotError)):
            runner.validate_completed_native_planning(root, receipt)
    finally:
        original.write_bytes(raw)
        original.chmod(0o600)
    assert journal.inspect(root) == before
    with pytest.raises(ValidationError, match="budget_exhausted"):
        plan(root, output, generated)


def test_planning_cannot_open_source_before_original_generation(tmp_path, monkeypatch):
    root, output, _ = case(tmp_path)
    monkeypatch.setattr(runner, "_producer_pin", lambda *args: pytest.fail("source opened"))
    with pytest.raises(ValidationError, match="prerequisite"):
        plan(root, output, None)
    assert not journal.inspect(root).events


@pytest.mark.parametrize(
    "parameter,value",
    [("forecast_plan_days", 28), ("warmup_days", 7), ("output_format", "parquet")],
)
def test_resealed_native_defaults_cannot_differ_from_frozen_canonical_request(
    tmp_path, parameter, value
):
    _, _, original = case(tmp_path)
    parameters = original.generation.resolved_parameters | {parameter: value}
    source = original.sources[0].model_copy(
        update={"generation_config_sha256": canonical_sha256(parameters)}
    )
    generation = original.generation.model_copy(
        update={"resolved_parameters": parameters, "source_recipe_sha256": source.content_sha256()}
    )
    planning = original.planning.model_copy(
        update={"source_recipe_sha256": source.content_sha256()}
    )
    resealed = original.model_copy(
        update={
            "sources": (source,),
            "generation": generation,
            "planning": planning,
            "operations": planning_operations(source, generation, planning),
        }
    )
    with pytest.raises(ValidationError, match="canonical_development"):
        CanonicalDevelopmentPlanningProtocol.model_validate_json(resealed.model_dump_json())


def test_failed_native_plan_stays_charged_and_cannot_be_retried(tmp_path, monkeypatch):
    root, output, protocol = case(tmp_path)
    generated = generate(monkeypatch, root, output, protocol)
    calls = controlled_worker(monkeypatch, root, failure=True)
    with pytest.raises(SnapshotError, match="worker_failed"):
        plan(root, output, generated)
    event = journal.inspect(root).events[-1]
    assert event.result == "failed" and event.cost.wall_seconds > 0
    assert event.cost.peak_process_tree_rss_bytes is None
    assert not (root / "receipts" / (event.reservation_id + ".json")).exists()
    with pytest.raises(ValidationError, match="budget_exhausted"):
        plan(root, output, generated)
    assert len(calls) == 1
