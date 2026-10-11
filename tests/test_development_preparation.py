"""Real durable runner boundaries with controlled phase outputs, not Source results."""

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_campaign_generation import fake_phases
from test_development_profiles import plans
from test_development_search import design

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign import campaign_generation as generation
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign.campaign_contract import CampaignCost, CampaignProtocol
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import (
    CampaignPortfolioProtocol,
    parse_campaign_journal,
    parse_campaign_protocol,
)
from retailops_ai.evaluation_campaign.development_preparation import (
    DevelopmentPreparationJournal,
    DevelopmentPreparationProtocol,
    compile_development_preparation,
    run_development_preparation_variant,
)
from retailops_ai.evaluation_campaign.development_profiles import prepare_development_profile
from retailops_ai.source_snapshot.files import SnapshotError


def preparation_case(tmp_path, products=25):
    search, small, medium, canonical = design(tmp_path)
    source = canonical.sources[0]
    profiles = [
        prepare_development_profile(
            original.profile,
            producer_commit=source.producer_commit,
            producer_lock_sha256=source.producer_lock_sha256,
            exporter_lock_sha256=source.exporter_lock_sha256,
            scenario_plans=plans(),
            resources=original.generations[0].resources,
        )
        for original in (small, medium)
    ]
    search = search.model_copy(
        update={
            "preparation_25_sha256": profiles[0].content_sha256(),
            "preparation_50_sha256": profiles[1].content_sha256(),
        }
    )
    root = tmp_path / "preparation-journal"
    protocol = compile_development_preparation(
        root,
        preparation=profiles[0 if products == 25 else 1],
        search=search,
        canonical_protocol=canonical,
    )
    journal.initialize(root, protocol)
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    return root, output, protocol, canonical


def run(root, output, variant="ordinary"):
    return run_development_preparation_variant(
        journal=root,
        variant=variant,
        producer=Path("/never-read-controlled-producer"),
        producer_python=Path(sys.executable),
        output_root=output,
    )


@pytest.mark.parametrize("products", [25, 50])
def test_additive_protocol_durably_preserves_full_scope_and_only_three_generations(
    tmp_path, products
):
    root, _, protocol, _ = preparation_case(tmp_path, products)
    assert isinstance(journal.inspect(root), DevelopmentPreparationJournal)
    assert parse_campaign_protocol(protocol.model_dump_json().encode()) == protocol
    assert parse_campaign_journal((root / "journal.json").read_bytes()).protocol == protocol
    assert len(protocol.sources) == len(protocol.operations) == 3
    assert protocol.maximum_new_attempts == 3 and protocol.maximum_new_fit_attempts == 0
    assert all(
        s.products == products and s.history == protocol.preparation.profile.history
        for s in protocol.sources
    )
    assert journal.initialize(root, protocol) == journal.inspect(root)
    for old in (CampaignProtocol, CampaignPortfolioProtocol):
        with pytest.raises(ValidationError):
            old.model_validate_json(protocol.model_dump_json())


@pytest.mark.parametrize("variant", ["ordinary", "demand", "physical"])
def test_original_six_phases_and_verified_stored_receipt_precede_completion(
    tmp_path, monkeypatch, variant
):
    root, output, protocol, _ = preparation_case(tmp_path)
    position = next(i for i, source in enumerate(protocol.sources) if source.variant == variant)
    operation = protocol.operations[position]
    case = root, output, operation.operation_id, protocol.preparation.generations[position]
    phases = fake_phases(monkeypatch, case)
    snapshot, curated, receipt = run(root, output, variant)
    assert snapshot.is_dir() and curated.is_dir()
    assert phases == list(generation.PHASES)
    generation.validate_completed_generation(root, receipt)
    assert receipt.source.source_parameters == protocol.preparation.profile.resolved_parameters()
    assert receipt.source_recipe_sha256 == protocol.sources[position].content_sha256()
    completed = journal.inspect(root).events[-1]
    assert completed.result == "completed" and completed.cost.peak_process_tree_rss_bytes == 8192
    assert completed.cost.artifact_bytes > 0
    before = list(phases)
    with pytest.raises(ValidationError, match="budget_exhausted"):
        run(root, output, variant)
    assert phases == before


@pytest.mark.parametrize("phase", generation.PHASES)
def test_every_phase_failure_is_charged_and_cannot_be_relaunched(tmp_path, monkeypatch, phase):
    root, output, protocol, _ = preparation_case(tmp_path)
    case = root, output, protocol.operations[0].operation_id, protocol.preparation.generations[0]
    phases = fake_phases(monkeypatch, case, failure=phase)
    with pytest.raises(SnapshotError, match="phase_failed_" + phase):
        run(root, output)
    finished = journal.inspect(root).events[-1]
    assert finished.result == "failed" and finished.cost.wall_seconds > 0
    assert finished.cost.peak_process_tree_rss_bytes == 8192
    before = list(phases)
    with pytest.raises(ValidationError, match="budget_exhausted"):
        run(root, output)
    assert phases == before
    assert journal.summary(root)["failed_new_attempts"] == 1


def test_real_wrong_producer_is_rejected_only_after_durable_reservation(tmp_path, monkeypatch):
    root, output, protocol, _ = preparation_case(tmp_path)
    producer = tmp_path / "missing-producer"
    # This is the actual producer guard, with no mocked phase or Source I/O.
    with pytest.raises(SnapshotError):
        run_development_preparation_variant(
            journal=root,
            variant="ordinary",
            producer=producer,
            producer_python=Path(sys.executable),
            output_root=output,
        )
    events = journal.inspect(root).events
    assert [e.kind for e in events] == ["reserved", "finished"]
    assert events[-1].result == "failed" and events[-1].cost.wall_seconds > 0
    assert events[-1].cost.peak_process_tree_rss_bytes is None
    assert not list(output.iterdir()) and not producer.exists()


@pytest.mark.parametrize("mutation", ["new_fit", "retry", "changed_plan", "wrong_source", "search"])
def test_resealed_protocol_cannot_add_fit_retry_or_unbound_source(tmp_path, mutation):
    _, _, protocol, _ = preparation_case(tmp_path)
    raw = protocol.model_dump(mode="json")
    if mutation == "new_fit":
        raw["operations"][0].update(
            action="model_fit",
            use_case="forecast",
            role="train",
            initialization_seed=42,
            forecast_family="rf",
        )
    elif mutation == "retry":
        raw["operations"][0]["maximum_attempts"] = 2
    elif mutation == "changed_plan":
        raw["operations"][0]["execution_recipe_sha256"] = "f" * 64
    elif mutation == "wrong_source":
        raw["sources"].reverse()
    else:
        raw["search"]["preparation_25_sha256"] = "f" * 64
    with pytest.raises(ValidationError):
        DevelopmentPreparationProtocol.model_validate_json(canonical_bytes(raw))


def test_actual_canonical_argument_and_shared_producer_are_required(tmp_path):
    _, _, protocol, canonical = preparation_case(tmp_path)
    changed = canonical.model_copy(update={"journal_path": str(tmp_path / "different")})
    with pytest.raises(ValueError, match="canonical_producer_or_scope"):
        compile_development_preparation(
            tmp_path / "second",
            preparation=protocol.preparation,
            search=protocol.search,
            canonical_protocol=changed,
        )
    _, small, _, _ = design(tmp_path / "other")
    different_search = protocol.search.model_copy(
        update={"preparation_25_sha256": small.content_sha256()}
    )
    with pytest.raises(ValueError, match="canonical_producer_or_scope"):
        compile_development_preparation(
            tmp_path / "second",
            preparation=small,
            search=different_search,
            canonical_protocol=canonical,
        )


def test_preparation_cannot_close_or_complete_without_measured_resources(tmp_path):
    root, _, protocol, _ = preparation_case(tmp_path)
    before = (root / "journal.json").read_bytes()
    with pytest.raises(ValidationError, match="only_generation_events"):
        journal.close(root, "f" * 64)
    assert (root / "journal.json").read_bytes() == before
    reservation = journal.reserve(root, protocol.operations[0].operation_id)
    with pytest.raises(ValidationError, match="requires_resource_cost"):
        journal.finish(
            root,
            reservation.reservation_id,
            result="completed",
            evidence_sha256="f" * 64,
            cost=CampaignCost(wall_seconds=1),
        )
    assert journal.summary(root)["unresolved_new_attempts"] == 1
    assert journal.summary(root)["unknown_new_wall_costs"] == 1


def test_concurrent_duplicate_and_interrupted_attempt_remain_charged(tmp_path):
    root, _, protocol, _ = preparation_case(tmp_path)
    operation = protocol.operations[0].operation_id

    def reserve():
        try:
            return journal.reserve(root, operation).reservation_id
        except ValidationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: reserve(), range(2)))
    assert sum(value is not None for value in outcomes) == 1
    with pytest.raises(ValidationError, match="unresolved_attempt"):
        journal.reserve(root, operation)
    assert journal.summary(root)["unresolved_new_attempts"] == 1
    assert journal.summary(root)["charged_new_attempts"] == 1


def test_wrong_journal_or_variant_never_opens_source(tmp_path):
    root, output, _, canonical = preparation_case(tmp_path)
    before = (root / "journal.json").read_bytes()
    with pytest.raises(SnapshotError, match="unknown_variant"):
        run(root, output, "other")
    assert (root / "journal.json").read_bytes() == before
    normal = Path(canonical.journal_path)
    journal.initialize(normal, canonical)
    with pytest.raises(SnapshotError, match="separate_journal"):
        run(normal, output)
    assert journal.summary(normal)["charged_new_attempts"] == 0
