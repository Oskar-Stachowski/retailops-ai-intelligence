"""Durable preparation controls with fake worker outputs, not Project Source proof."""

import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_ai09_campaign_journal import protocol_document
from test_campaign_generation import fake_phases
from test_development_profiles import plans as scenario_plans

from retailops_ai.data_contracts.common import DateWindow, end_of_day
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign import development_planning as runner
from retailops_ai.evaluation_campaign.campaign_contract import CampaignProtocol
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationResources,
)
from retailops_ai.evaluation_campaign.campaign_generation_worker import write
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import (
    CampaignPortfolioProtocol,
    parse_campaign_protocol,
)
from retailops_ai.evaluation_campaign.development_planning_contract import (
    DevelopmentPlanningContext,
    NativeDevelopmentPlanningJournal,
    NativeDevelopmentPlanningProtocol,
    compile_native_development_planning,
)
from retailops_ai.evaluation_campaign.development_profiles import DevelopmentProfile
from retailops_ai.evaluation_campaign.partition_contract import (
    ROLES,
    ForecastPartitionPolicy,
    RoleWindow,
)
from retailops_ai.source_snapshot.files import SnapshotError


def case(tmp_path, products=25):
    root = tmp_path / "journal"
    declaration = protocol_document(root)
    context = DevelopmentPlanningContext.model_validate_json(
        canonical_bytes({k: declaration[k] for k in DevelopmentPlanningContext.model_fields})
    )
    roles = []
    for i, name in enumerate(ROLES):
        start = date(2025, 9, 1) + timedelta(days=i * 46)
        end = start + timedelta(days=29)
        roles.append(
            RoleWindow(
                role=name,
                origins=DateWindow(start=start, end=end),
                label_knowledge_cutoff=end_of_day(end + timedelta(days=15)),
            )
        )
    resources = CampaignGenerationResources(
        wall_seconds=3600,
        tree_rss_bytes=512 * 1024**2,
        scratch_bytes=8 * 1024**3,
        minimum_available_memory_bytes=1024**3,
        minimum_free_disk_bytes=6 * 1024**3,
    )
    protocol = compile_native_development_planning(
        root,
        context=context,
        profile=DevelopmentProfile(name=f"ai09-development-{products}-v1", products=products),
        producer_commit="a" * 40,
        producer_lock_sha256="b" * 64,
        exporter_lock_sha256="c" * 64,
        producer_planner_sha256="d" * 64,
        partitions=ForecastPartitionPolicy(roles=tuple(roles)),
        generation_resources=resources,
        planning_resources=resources,
        maximum_total_wall_seconds=3600,
    )
    journal.initialize(root, protocol)
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    return root, output, protocol


def generate(monkeypatch, root, output, protocol):
    phases = fake_phases(
        monkeypatch, (root, output, protocol.operations[0].operation_id, protocol.generation)
    )
    _, _, generated = runner.generate_ordinary_for_planning(
        journal=root,
        producer=Path("/controlled-producer"),
        producer_python=Path(sys.executable),
        output_root=output,
    )
    assert phases == ["generation", "qualification", "export", "import", "curation", "verify"]
    return generated


def controlled_worker(monkeypatch, root, *, failure=False, mutation=None):
    calls = []

    def pin(*args):
        assert journal.inspect(root).events[-1].kind == "reserved"

    monkeypatch.setattr(runner, "_producer_pin", pin)

    def execute(command, **kwargs):
        calls.append(kwargs)
        ledger = journal.inspect(root)
        assert ledger.events[-1].operation_id == ledger.protocol.operations[1].operation_id
        assert ledger.events[-1].kind == "reserved"
        assert command[1:3] == ["-I", "-B"]
        if failure:
            return {
                "status": "failed",
                "reason": "preflight_reserve",
                "sampled_tree_peak_rss_bytes": None,
            }
        request = runner.read(kwargs["root"] / "request.json")
        plans = scenario_plans()
        bundle = {
            "version": request["planning"]["producer_planner_version"],
            "data_class": "simulation_truth",
            "recipe": request["selection"],
            "recipe_sha256": canonical_sha256(request["selection"]),
            "source_dataset_id": request["selection"]["source_dataset_id"],
            "plans": plans,
            "plan_sha256": {k: canonical_sha256(v) for k, v in plans.items()},
            "realized_effects_verified": False,
            "critical_coverage_verified": False,
            "model_fit_authorized": False,
            "final_test_access_authorized": False,
            "stage_ready": False,
        }
        if mutation:
            mutation(bundle)
        write(kwargs["root"] / "native-plans.json", bundle)
        write(kwargs["root"] / "result.json", {"worker_peak_rss_bytes": 16384})
        return {"status": "passed", "sampled_tree_peak_rss_bytes": 8192}

    monkeypatch.setattr(runner, "monitor", execute)
    return calls


def plan(root, output, generated):
    return runner.plan_native_development_scenarios(
        journal=root,
        generated=generated,
        raw_source=output,
        producer=Path("/controlled-producer"),
        producer_python=Path(sys.executable),
        output_root=output,
    )


@pytest.mark.parametrize("products", [25, 50])
def test_exact_full_scope_without_fake_scenarios_or_final_source(products, tmp_path):
    root, _, protocol = case(tmp_path, products)
    assert isinstance(journal.inspect(root), NativeDevelopmentPlanningJournal)
    assert parse_campaign_protocol(protocol.model_dump_json().encode()) == protocol
    assert len(protocol.sources) == 1 and protocol.sources[0].scenario_plan_sha256 is None
    assert protocol.sources[0].products == products
    assert protocol.generation.resolved_parameters["days"] == 365
    assert protocol.generation.resolved_parameters["stores"] == 5
    assert protocol.generation.resolved_parameters["warehouses"] == 3
    assert protocol.maximum_new_attempts == 2 and protocol.maximum_new_fit_attempts == 0
    for old in (CampaignProtocol, CampaignPortfolioProtocol):
        with pytest.raises(ValidationError):
            old.model_validate_json(protocol.model_dump_json())


def test_planning_requires_completed_generation_before_any_source_io(tmp_path, monkeypatch):
    root, output, _ = case(tmp_path)
    monkeypatch.setattr(runner, "_producer_pin", lambda *args: pytest.fail("producer opened"))
    with pytest.raises(ValidationError, match="prerequisite"):
        plan(root, output, None)
    assert not journal.inspect(root).events and not list(output.iterdir())


def test_six_phase_generation_and_charged_read_preserve_costs_and_native_identity(
    tmp_path, monkeypatch
):
    root, output, protocol = case(tmp_path)
    generated = generate(monkeypatch, root, output, protocol)
    cold = journal.inspect(root).events[-1].cost
    calls = controlled_worker(monkeypatch, root)
    bundle, receipt = plan(root, output, generated)
    runner.validate_completed_native_planning(root, receipt)
    assert receipt.source_dataset_id == generated.source.parent.source_dataset_id
    assert (
        receipt.generation_cost == cold
        and receipt.generation_receipt_sha256 == generated.content_sha256()
    )
    assert receipt.planning_sha256 == protocol.planning.content_sha256()
    assert bundle.stat().st_mode & 0o777 == 0o600
    assert receipt.bundle_bytes == bundle.stat().st_size
    ledger = journal.inspect(root)
    assert [e.kind for e in ledger.events] == ["reserved", "finished", "reserved", "finished"]
    assert ledger.events[-1].cost.peak_process_tree_rss_bytes == 16384
    assert ledger.events[-1].cost.artifact_bytes > 0 and len(calls) == 1
    assert journal.summary(root)["charged_new_fit_attempts"] == 0
    with pytest.raises(ValidationError, match="budget_exhausted"):
        plan(root, output, generated)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "mutation",
    [
        lambda b: b.update(source_dataset_id="source-sha256-" + "f" * 64),
        lambda b: b.update(model_fit_authorized=True),
        lambda b: b["plan_sha256"].update(demand="0" * 64),
        lambda b: b["plans"].pop("physical"),
        lambda b: b["recipe"].update(generation_sha256="0" * 64),
    ],
)
def test_changed_native_output_consumes_read_attempt_without_receipt(
    tmp_path, monkeypatch, mutation
):
    root, output, protocol = case(tmp_path)
    generated = generate(monkeypatch, root, output, protocol)
    calls = controlled_worker(monkeypatch, root, mutation=mutation)
    with pytest.raises(SnapshotError, match="bundle_binding_mismatch"):
        plan(root, output, generated)
    finish = journal.inspect(root).events[-1]
    assert finish.result == "failed" and finish.cost.wall_seconds > 0
    assert not (root / "receipts" / (finish.reservation_id + ".json")).exists()
    with pytest.raises(ValidationError, match="budget_exhausted"):
        plan(root, output, generated)
    assert len(calls) == 1


def test_worker_admission_failure_preserves_unknown_rss_and_cold_generation(tmp_path, monkeypatch):
    root, output, protocol = case(tmp_path)
    generated = generate(monkeypatch, root, output, protocol)
    cold = journal.inspect(root).events[-1]
    controlled_worker(monkeypatch, root, failure=True)
    with pytest.raises(SnapshotError, match="worker_failed"):
        plan(root, output, generated)
    ledger = journal.inspect(root)
    assert ledger.events[1] == cold and ledger.events[-1].cost.peak_process_tree_rss_bytes is None
    assert ledger.events[-1].result == "failed"


def test_forged_stored_generation_fails_after_reservation_before_worker(tmp_path, monkeypatch):
    root, output, protocol = case(tmp_path)
    generated = generate(monkeypatch, root, output, protocol)
    (root / "receipts" / (generated.reservation_id + ".json")).write_text("{}\n")
    calls = controlled_worker(monkeypatch, root)
    with pytest.raises(SnapshotError, match="stored_receipt_mismatch"):
        plan(root, output, generated)
    assert not calls and journal.inspect(root).events[-1].result == "failed"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d["operations"][1].update(maximum_attempts=2),
        lambda d: d["operations"][1].update(prerequisites=[]),
        lambda d: d["operations"][1].update(execution_recipe_sha256="0" * 64),
        lambda d: d.update(maximum_new_fit_attempts=1),
        lambda d: d.update(maximum_total_wall_seconds=10801),
        lambda d: d["generation"]["resources"].update(tree_rss_bytes=12 * 1024**3 + 1),
        lambda d: d["planning"]["resources"].update(minimum_available_memory_bytes=1024**3 - 1),
        lambda d: d["planning"]["partitions"]["roles"][0]["origins"].update(start="2025-08-02"),
        lambda d: d["planning"]["partitions"]["roles"][2]["origins"].update(end="2025-12-20"),
    ],
)
def test_resealed_scope_cannot_add_retries_fits_unbound_plans_or_unsafe_limits(tmp_path, mutation):
    _, _, protocol = case(tmp_path)
    document = protocol.model_dump(mode="json")
    mutation(document)
    with pytest.raises(ValidationError):
        NativeDevelopmentPlanningProtocol.model_validate_json(canonical_bytes(document))


def test_concurrent_read_reservations_charge_at_most_once(tmp_path, monkeypatch):
    root, output, protocol = case(tmp_path)
    generate(monkeypatch, root, output, protocol)

    def reserve(_):
        try:
            return journal.reserve(root, protocol.operations[1].operation_id)
        except ValidationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = list(pool.map(reserve, range(2)))
    assert sum(a is not None for a in attempts) == 1
    assert journal.summary(root)["unresolved_new_attempts"] == 1


def test_planning_deadline_subtracts_actual_completed_cold_generation_cost(tmp_path, monkeypatch):
    root, output, protocol = case(tmp_path)
    generated = generate(monkeypatch, root, output, protocol)
    cold = journal.inspect(root).events[-1].cost
    calls = controlled_worker(monkeypatch, root)
    monkeypatch.setattr(runner, "perf_counter", lambda: 100.0)
    plan(root, output, generated)
    assert calls[0]["deadline"] == 100.0 + protocol.maximum_total_wall_seconds - cold.wall_seconds


def test_unresolved_planning_charge_cannot_resume_or_generate_again(tmp_path, monkeypatch):
    root, output, protocol = case(tmp_path)
    generated = generate(monkeypatch, root, output, protocol)
    journal.reserve(root, protocol.operations[1].operation_id)
    calls = controlled_worker(monkeypatch, root)
    with pytest.raises(ValidationError, match="unresolved_attempt"):
        plan(root, output, generated)
    assert not calls
    assert journal.summary(root)["unknown_new_wall_costs"] == 1


def test_planning_receipt_revalidates_its_stored_generation_parent(tmp_path, monkeypatch):
    root, output, protocol = case(tmp_path)
    generated = generate(monkeypatch, root, output, protocol)
    controlled_worker(monkeypatch, root)
    _, receipt = plan(root, output, generated)
    stored = root / "receipts" / (generated.reservation_id + ".json")
    stored.unlink()
    with pytest.raises(FileNotFoundError):
        runner.validate_completed_native_planning(root, receipt)
