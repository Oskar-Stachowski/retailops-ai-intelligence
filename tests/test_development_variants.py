"""Controlled native phases exercise durable reuse; they are not full-profile evidence."""

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_campaign_generation import fake_phases
from test_development_planning import case as bootstrap_case
from test_development_planning import controlled_worker, generate, plan

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign import campaign_generation as generation
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign import development_variants as variants
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import parse_campaign_protocol
from retailops_ai.evaluation_campaign.development_planning_contract import (
    NativeDevelopmentPlanningReceipt,
)
from retailops_ai.evaluation_campaign.development_variants_contract import (
    ResolvedDevelopmentPreparationJournal,
    ResolvedDevelopmentPreparationProtocol,
)
from retailops_ai.source_snapshot.files import SnapshotError


def prepared_case(tmp_path, monkeypatch, products=25):
    bootstrap, output, initial = bootstrap_case(tmp_path, products)
    generated = generate(monkeypatch, bootstrap, output, initial)
    controlled_worker(monkeypatch, bootstrap)
    bundle, planned = plan(bootstrap, output, generated)
    # The native truth bundle is no longer needed to compile the next frozen
    # protocol: the charged planning operation returned the exact native plans.
    bundle.unlink()
    root = tmp_path / "variant-journal"
    protocol = variants.compile_resolved_development_preparation(
        root,
        bootstrap_journal=bootstrap,
        generated=generated,
        planned=planned,
        reuse_resources=initial.planning.resources,
        maximum_total_wall_seconds=3600,
    )
    journal.initialize(root, protocol)
    snapshot = output / generated.reservation_id / "import/snapshot"
    curated = output / generated.reservation_id / "curation"
    return root, output, protocol, snapshot, curated


def replay_worker(monkeypatch, case, *, mutation=None, failed=False):
    root, _, protocol, _, _ = case
    calls = []

    def replay(command, **kwargs):
        calls.append(kwargs)
        assert journal.inspect(root).events[-1].kind == "reserved"
        assert journal.inspect(root).events[-1].operation_id == protocol.operations[0].operation_id
        assert command[1:3] == ["-I", "-B"] and command[-3] == "verify"
        if failed:
            return {
                "status": "failed",
                "sampled_tree_peak_rss_bytes": None,
                "reason": "preflight_reserve",
            }
        request = read(kwargs["root"] / "request.json")
        assert request["source"] == protocol.sources[0].model_dump(mode="json")
        result = {
            "source": protocol.generated.source.model_dump(mode="json"),
            "verified_inventories": {
                k: "e" * 64 for k in ("snapshot", "curated", "logical_curated")
            },
            "worker_peak_rss_bytes": 32768,
        }
        if mutation:
            mutation(result)
        write(kwargs["root"] / "verify.json", result)
        return {"status": "passed", "sampled_tree_peak_rss_bytes": 16384}

    monkeypatch.setattr(variants, "monitor", replay)
    return calls


def reuse(case):
    root, output, _, snapshot, curated = case
    return variants.reuse_ordinary_development_parent(
        journal=root, snapshot=snapshot, curated=curated, output_root=output
    )


def run_variant(case, name):
    return variants.generate_resolved_development_variant(
        journal=case[0],
        variant=name,
        producer=Path("/controlled-producer"),
        producer_python=Path(sys.executable),
        output_root=case[1],
    )


@pytest.mark.parametrize("products", [25, 50])
def test_complete_preparation_reuses_ordinary_then_runs_both_original_six_phase_generations(
    tmp_path,
    monkeypatch,
    products,
):
    case = prepared_case(tmp_path, monkeypatch, products)
    root, output, protocol, _, _ = case
    assert isinstance(journal.inspect(root), ResolvedDevelopmentPreparationJournal)
    assert parse_campaign_protocol(protocol.model_dump_json().encode()) == protocol
    assert protocol.sources == protocol.planned.preparation.sources
    assert protocol.planned.preparation.generations[0] == protocol.bootstrap.generation
    with pytest.raises(ValidationError):
        NativeDevelopmentPlanningReceipt.model_validate_json(protocol.planned.model_dump_json())
    calls = replay_worker(monkeypatch, case)
    reused = reuse(case)
    variants.validate_reused_development_parent(root, reused)
    assert reused.source == protocol.generated.source
    assert reused.generation_cost == protocol.planned.generation_cost
    assert reused.planning_cost == protocol.planning_cost and len(calls) == 1
    assert not reused.new_source_generation_performed
    for position, name in ((1, "demand"), (2, "physical")):
        phases = fake_phases(
            monkeypatch,
            (
                root,
                output,
                protocol.operations[position].operation_id,
                protocol.planned.preparation.generations[position],
            ),
        )
        _, _, generated = run_variant(case, name)
        assert phases == list(generation.PHASES)
        generation.validate_completed_generation(root, generated)
        assert generated.source_recipe_sha256 == protocol.sources[position].content_sha256()
    events = journal.inspect(root).events
    assert len(events) == 6 and all(e.result == "completed" for e in events if e.kind == "finished")
    assert [o.action for o in protocol.operations] == [
        "source_read",
        "source_generate",
        "source_generate",
    ]
    assert journal.summary(root)["charged_new_fit_attempts"] == 0


def test_both_variants_require_verified_ordinary_and_fixed_sequential_order(tmp_path, monkeypatch):
    case = prepared_case(tmp_path, monkeypatch)
    root, output, protocol, _, _ = case
    for position, name in ((1, "demand"), (2, "physical")):
        phases = fake_phases(
            monkeypatch,
            (
                root,
                output,
                protocol.operations[position].operation_id,
                protocol.planned.preparation.generations[position],
            ),
        )
        with pytest.raises(ValidationError, match="prerequisite"):
            run_variant(case, name)
        assert phases == [] and not journal.inspect(root).events
    replay_worker(monkeypatch, case)
    reuse(case)
    with pytest.raises(ValidationError, match="prerequisite"):
        run_variant(case, "physical")
    assert len(journal.inspect(root).events) == 2


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r["source"].update(snapshot_manifest_sha256="0" * 64),
        lambda r: r["source"]["parent"].update(source_dataset_id="source-sha256-" + "0" * 64),
        lambda r: r["source"]["source_parameters"].update(days=364),
        lambda r: r["verified_inventories"].pop("logical_curated"),
        lambda r: r.update(worker_peak_rss_bytes=13 * 1024**3),
    ],
)
def test_changed_parent_or_incomplete_replay_consumes_the_single_read(
    tmp_path, monkeypatch, mutation
):
    case = prepared_case(tmp_path, monkeypatch)
    calls = replay_worker(monkeypatch, case, mutation=mutation)
    with pytest.raises((SnapshotError, ValidationError)):
        reuse(case)
    ended = journal.inspect(case[0]).events[-1]
    assert ended.result == "failed" and ended.cost.wall_seconds > 0
    assert not (case[0] / "receipts" / (ended.reservation_id + ".json")).exists()
    with pytest.raises(ValidationError, match="budget_exhausted"):
        reuse(case)
    assert len(calls) == 1
    with pytest.raises(ValidationError, match="prerequisite"):
        run_variant(case, "demand")


def test_bootstrap_metadata_rechecked_after_reserve_before_parent_worker(tmp_path, monkeypatch):
    case = prepared_case(tmp_path, monkeypatch)
    protocol = case[2]
    original = (
        Path(protocol.bootstrap.journal_path)
        / "receipts"
        / (protocol.generated.reservation_id + ".json")
    )
    original.unlink()
    calls = replay_worker(monkeypatch, case)
    with pytest.raises(FileNotFoundError):
        reuse(case)
    assert not calls and journal.inspect(case[0]).events[-1].result == "failed"


def test_refused_worker_keeps_unknown_rss_and_does_not_reset_cold_costs(tmp_path, monkeypatch):
    case = prepared_case(tmp_path, monkeypatch)
    replay_worker(monkeypatch, case, failed=True)
    with pytest.raises(SnapshotError, match="reuse_worker_failed"):
        reuse(case)
    ledger = journal.inspect(case[0])
    assert ledger.events[-1].cost.peak_process_tree_rss_bytes is None
    assert ledger.protocol.planned.generation_cost.wall_seconds > 0
    assert ledger.protocol.planning_cost.wall_seconds > 0


def test_reuse_and_generation_deadlines_share_the_original_cold_budget(tmp_path, monkeypatch):
    case = prepared_case(tmp_path, monkeypatch)
    root, output, protocol, _, _ = case
    calls = replay_worker(monkeypatch, case)
    monkeypatch.setattr(variants, "perf_counter", lambda: 100.0)
    reuse(case)
    assert calls[0]["deadline"] == 100.0 + 3600 - protocol.cold_wall_seconds()
    fake_phases(
        monkeypatch,
        (
            root,
            output,
            protocol.operations[1].operation_id,
            protocol.planned.preparation.generations[1],
        ),
    )
    old = generation.monitor
    deadlines = []

    def capture(*args, **kwargs):
        deadlines.append(kwargs["deadline"])
        return old(*args, **kwargs)

    monkeypatch.setattr(generation, "monitor", capture)
    monkeypatch.setattr(generation, "perf_counter", lambda: 200.0)
    expected = 200.0 + variants.remaining_wall_seconds(variants.variants_journal(root))
    run_variant(case, "demand")
    assert deadlines == [expected] * 6


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p["operations"][0].update(action="source_generate"),
        lambda p: p["operations"][1].update(prerequisites=[]),
        lambda p: p["operations"][2].update(prerequisites=[p["operations"][0]["operation_id"]]),
        lambda p: p["reuse"].update(generated_receipt_sha256="f" * 64),
        lambda p: p.update(runtime=p["runtime"] | {"python_version": "different"}),
        lambda p: p.update(uncertainty_policy_sha256="f" * 64),
        lambda p: p.update(maximum_new_fit_attempts=1),
        lambda p: p["reuse"]["resources"].update(minimum_free_disk_bytes=1024),
        lambda p: p["planned"]["preparation"]["generations"][1]["scenario_plan"].update(seed=137),
    ],
)
def test_resealed_protocol_cannot_invent_generation_change_native_plans_or_erase_context(
    tmp_path, monkeypatch, mutation
):
    case = prepared_case(tmp_path, monkeypatch)
    raw = case[2].model_dump(mode="json")
    mutation(raw)
    with pytest.raises(ValidationError):
        ResolvedDevelopmentPreparationProtocol.model_validate_json(canonical_bytes(raw))


def test_reused_receipt_must_remain_durable_before_scenario_generation(tmp_path, monkeypatch):
    case = prepared_case(tmp_path, monkeypatch)
    replay_worker(monkeypatch, case)
    reused = reuse(case)
    (case[0] / "receipts" / (reused.reservation_id + ".json")).write_text("{}\n")
    with pytest.raises(ValidationError):
        run_variant(case, "demand")
    assert len(journal.inspect(case[0]).events) == 4
    assert journal.inspect(case[0]).events[-1].result == "failed"


def test_ordinary_cannot_be_regenerated_in_resolved_preparation(tmp_path, monkeypatch):
    case = prepared_case(tmp_path, monkeypatch)
    with pytest.raises(SnapshotError, match="ordinary_must_be_reused"):
        run_variant(case, "ordinary")
    assert not journal.inspect(case[0]).events


def test_exhausted_shared_budget_is_charged_before_any_new_source_work(tmp_path, monkeypatch):
    case = prepared_case(tmp_path, monkeypatch)
    replay_worker(monkeypatch, case)
    reuse(case)
    root, output, protocol, _, _ = case
    phases = fake_phases(
        monkeypatch,
        (
            root,
            output,
            protocol.operations[1].operation_id,
            protocol.planned.preparation.generations[1],
        ),
    )
    monkeypatch.setattr(variants, "remaining_wall_seconds", lambda ledger: 0.0)
    with pytest.raises(SnapshotError, match="aggregate_wall_budget_exhausted"):
        run_variant(case, "demand")
    assert not phases
    assert journal.inspect(root).events[-1].result == "failed"
    with pytest.raises(ValidationError, match="budget_exhausted"):
        run_variant(case, "demand")


@pytest.mark.parametrize("allowance", [1.0, 99999.0])
def test_outer_allowance_can_only_reduce_the_frozen_generation_limit(
    tmp_path, monkeypatch, allowance
):
    case = prepared_case(tmp_path, monkeypatch)
    replay_worker(monkeypatch, case)
    reuse(case)
    root, output, protocol, _, _ = case
    plan = protocol.planned.preparation.generations[1]
    fake_phases(monkeypatch, (root, output, protocol.operations[1].operation_id, plan))
    original = generation.monitor
    deadlines = []

    def capture(*args, **kwargs):
        deadlines.append(kwargs["deadline"])
        return original(*args, **kwargs)

    monkeypatch.setattr(generation, "monitor", capture)
    monkeypatch.setattr(generation, "perf_counter", lambda: 100.0)
    effective = min(
        plan.resources.wall_seconds,
        allowance,
        variants.remaining_wall_seconds(variants.variants_journal(root)),
    )
    generation.generate_campaign_parent(
        Path("/controlled-producer"),
        Path(sys.executable),
        output,
        journal=root,
        operation_id=protocol.operations[1].operation_id,
        plan=plan,
        remaining_wall_seconds=allowance,
    )
    assert deadlines == [100.0 + effective] * 6


def test_direct_generator_cannot_bypass_original_costs_or_durable_reuse(tmp_path, monkeypatch):
    case = prepared_case(tmp_path, monkeypatch)
    replay_worker(monkeypatch, case)
    reused = reuse(case)
    root, output, protocol, _, _ = case
    (root / "receipts" / (reused.reservation_id + ".json")).unlink()
    phases = fake_phases(
        monkeypatch,
        (
            root,
            output,
            protocol.operations[1].operation_id,
            protocol.planned.preparation.generations[1],
        ),
    )
    with pytest.raises(FileNotFoundError):
        generation.generate_campaign_parent(
            Path("/controlled-producer"),
            Path(sys.executable),
            output,
            journal=root,
            operation_id=protocol.operations[1].operation_id,
            plan=protocol.planned.preparation.generations[1],
        )
    assert not phases and journal.inspect(root).events[-1].result == "failed"
