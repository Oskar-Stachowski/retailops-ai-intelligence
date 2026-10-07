"""Controlled metadata fixtures prove ordering/durability, not portfolio quality."""

import copy
import json
import os
import selectors
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign.campaign_contract import (
    CampaignCost,
    CampaignJournal,
    CampaignProtocol,
    CampaignSourceRecipe,
    SelectionFreeze,
)
from retailops_ai.evaluation_campaign.legacy_carryover import load_legacy_carryover
from retailops_ai.evaluation_campaign.partitions import runtime_pin
from retailops_ai.source_snapshot.files import SnapshotError

EVIDENCE = Path(__file__).resolve().parents[1] / "docs/evidence"
CASES = ("forecast", "anomaly", "stockout")
FULL_COST = CampaignCost(wall_seconds=0.1, peak_process_tree_rss_bytes=1024, artifact_bytes=128)


def digest(value):
    return canonical_sha256(value)


def protocol_document(root):
    legacy = load_legacy_carryover(EVIDENCE)
    sources = []
    for phase, seed in (("development", 42), ("final", 42), ("final", 137), ("final", 2026)):
        end = date(2026, 7, 31) if phase == "development" else date(2026, 9, 30)
        days = 365 if phase == "development" else 730
        source = {
            "phase": phase,
            "seed": seed,
            "producer_commit": "a" * 40,
            "producer_lock_sha256": digest("test-producer-lock"),
            "generation_config_sha256": digest(["test-fixture-config", phase, seed]),
            "profile": "ai-dev" if phase == "development" else "ai-training",
            "history": {
                "start": (end - timedelta(days=days - 1)).isoformat(),
                "end": end.isoformat(),
            },
            "products": 100 if phase == "development" else 200,
            "selling_pairs": 5 if phase == "development" else 10,
            "stock_locations": 3 if phase == "development" else 4,
        }
        if phase == "final":
            source["evaluation_origins"] = {"start": "2026-08-16", "end": "2026-09-15"}
        sources.append(CampaignSourceRecipe.model_validate_json(json.dumps(source)))
    operations = []
    for source in sources:
        name = f"{source.phase}-{source.seed}"
        common = {
            "phase": source.phase,
            "source_recipe_sha256": source.content_sha256(),
            "execution_recipe_sha256": digest(["test-execution", name]),
        }
        operations.extend(
            [
                common
                | {
                    "operation_id": name + "-generate",
                    "action": "source_generate",
                    "use_case": "source",
                    "role": "all_parent_data",
                },
                common
                | {
                    "operation_id": name + "-read",
                    "action": "source_read",
                    "use_case": "source",
                    "role": "all_parent_data",
                    "prerequisites": [name + "-generate"],
                },
            ]
        )
        if source.phase == "development":
            operations.extend(
                common
                | {
                    "operation_id": "development-fit"
                    if family == "rf"
                    else "development-fit-" + family,
                    "action": "model_fit",
                    "use_case": "forecast",
                    "forecast_family": family,
                    "role": "train",
                    "initialization_seed": 42,
                    "maximum_attempts": 3,
                    "prerequisites": [name + "-read"],
                }
                for family in ("rf", "hgb", "tensorflow")
            )
        else:
            operations.extend(
                common
                | {
                    "operation_id": name + "-score-" + case,
                    "action": "model_score",
                    "use_case": case,
                    "role": "final_evaluation",
                    "prerequisites": [name + "-read"],
                }
                for case in CASES
            )
    return {
        "journal_path": str(root),
        "legacy": legacy.model_dump(mode="json"),
        "legacy_sha256": legacy.content_sha256(),
        "runtime": runtime_pin().model_dump(mode="json"),
        "sources": [s.model_dump(mode="json") for s in sources],
        "operations": operations,
        "selection_policy_sha256": digest("test-selection-policy"),
        "use_case_quality_policy_sha256": {
            case: digest(["test-quality-policy", case]) for case in CASES
        },
        "segment_policy_sha256": digest("test-segments"),
        "uncertainty_policy_sha256": digest("test-block-bootstrap"),
        "seed_weights": {str(seed): 1 for seed in (42, 137, 2026)},
        "scenario_weights": {
            name: 1 for name in ("normal", "promotion", "demand_shock", "inventory_constraint")
        },
        "maximum_new_attempts": sum(o.get("maximum_attempts", 1) for o in operations),
        "maximum_new_fit_attempts": 9,
    }


@pytest.fixture
def campaign(tmp_path):
    root = tmp_path.resolve() / "prospective"
    protocol = CampaignProtocol.model_validate_json(json.dumps(protocol_document(root)))
    journal.initialize(root, protocol)
    return root, protocol


def complete(root, operation_id, cost=FULL_COST):
    with journal.audited_operation(root, operation_id) as operation:
        operation.evidence_sha256 = digest(["controlled-fixture-output", operation_id])
        operation.cost = cost


def development(root):
    complete(root, "development-42-generate")
    complete(root, "development-42-read")
    complete(root, "development-fit")
    complete(root, "development-fit-hgb")
    complete(root, "development-fit-tensorflow")


def selection(root):
    fields = (
        "model_artifact_sha256",
        "preprocessing_sha256",
        "calibration_sha256",
        "threshold_policy_sha256",
        "feature_schema_sha256",
        "selection_evidence_sha256",
    )
    return SelectionFreeze.model_validate_json(
        json.dumps(
            {
                "bundles": [
                    {
                        "use_case": case,
                        **{field: digest(["test-bundle", case, field]) for field in fields},
                    }
                    for case in CASES
                ],
                "development_journal_head_sha256": journal.inspect(root).head_sha256,
            }
        )
    )


def test_initialize_is_idempotent_and_carries_unknown_legacy_cost(campaign):
    root, protocol = campaign
    original = (root / "journal.json").read_bytes()
    assert journal.initialize(root, protocol).protocol_sha256 == protocol.content_sha256()
    assert (root / "journal.json").read_bytes() == original
    report = journal.summary(root)
    assert report["legacy_fit_starts"] == 44 and report["legacy_fit_completions"] == 40
    assert report["legacy_budget_available"] == 0 and report["unknown_historical_cost"]
    assert report["charged_new_attempts"] == 0
    assert not journal.inspect(root).final_holdout_freshness_qualified
    assert not report["stage_ready"]
    assert root.stat().st_mode & 0o777 == 0o700
    assert (root / "journal.json").stat().st_mode & 0o777 == 0o600


def test_reservation_is_persisted_before_the_context_body_can_read(campaign):
    root, _ = campaign
    with journal.audited_operation(root, "development-42-generate") as operation:
        observed = journal.inspect(root)
        assert observed.events[-1] == operation.reservation
        assert journal.summary(root)["unresolved_new_attempts"] == 1
        operation.evidence_sha256 = digest("test-output")
        operation.cost = FULL_COST
    assert journal.summary(root)["completed_new_attempts"] == 1


@pytest.mark.parametrize("failed_fsync", [1, 2])
def test_publication_failure_never_enters_the_operation_body(campaign, monkeypatch, failed_fsync):
    root, _ = campaign
    real_fsync = os.fsync
    calls = 0

    def interrupted_sync(descriptor):
        nonlocal calls
        calls += 1
        if calls == failed_fsync:
            raise OSError("controlled persistence failure")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", interrupted_sync)
    entered = False
    with pytest.raises(OSError, match="controlled persistence failure"):
        with journal.audited_operation(root, "development-42-generate"):
            entered = True
    assert not entered
    # Before replace there is no published reservation. After replace, an
    # uncertain directory sync conservatively leaves the reservation charged.
    assert journal.summary(root)["charged_new_attempts"] == failed_fsync - 1
    assert journal.summary(root)["unresolved_new_attempts"] == failed_fsync - 1


def test_failures_and_unresolved_reservations_never_refund_fit_budget(campaign):
    root, protocol = campaign
    complete(root, "development-42-generate")
    complete(root, "development-42-read")
    with pytest.raises(RuntimeError, match="controlled failure"):
        with journal.audited_operation(root, "development-fit"):
            raise RuntimeError("controlled failure")
    unresolved = journal.reserve(root, "development-fit")
    with pytest.raises(ValidationError, match="unresolved_attempt"):
        journal.reserve(root, "development-fit")
    assert journal.summary(root)["remaining_new_fit_attempts"] == 7
    journal.initialize(root, protocol)
    journal.finish(
        root, str(unresolved.reservation_id), result="failed", error_code="worker_interrupted"
    )
    complete(root, "development-fit")
    with pytest.raises(ValidationError, match="budget_exhausted"):
        journal.reserve(root, "development-fit")
    assert journal.summary(root)["charged_new_fit_attempts"] == 3
    assert journal.summary(root)["failed_new_attempts"] == 2
    ordinary_failure = journal.inspect(root).events[5].cost
    assert ordinary_failure is not None and ordinary_failure.wall_seconds > 0
    assert ordinary_failure.peak_process_tree_rss_bytes is None
    assert journal.inspect(root).events[7].cost is None  # externally interrupted, unknown


def test_missing_output_or_full_fit_cost_is_a_charged_failure(campaign):
    root, _ = campaign
    complete(root, "development-42-generate")
    complete(root, "development-42-read")
    with pytest.raises(SnapshotError, match="missing_completion_evidence"):
        with journal.audited_operation(root, "development-fit"):
            pass
    with pytest.raises(ValidationError, match="measured_resource_cost"):
        complete(root, "development-fit", CampaignCost(wall_seconds=0.1))
    assert journal.summary(root)["charged_new_fit_attempts"] == 2
    assert journal.summary(root)["failed_new_attempts"] == 2


@pytest.mark.parametrize(
    "operation_id", ["unplanned", "development-fit", "final-42-generate", "final-42-read"]
)
def test_unplanned_or_out_of_order_body_cannot_run(campaign, operation_id):
    root, _ = campaign
    reached = False
    with pytest.raises(ValidationError):
        with journal.audited_operation(root, operation_id):
            reached = True
    assert not reached and journal.summary(root)["charged_new_attempts"] == 0


def test_freeze_blocks_open_attempts_and_binds_exact_development_history(campaign):
    root, _ = campaign
    development(root)
    bound = selection(root)
    event = journal.reserve(root, "development-fit")
    with pytest.raises(ValidationError, match="resolved_development"):
        journal.freeze_selection(root, bound)
    journal.finish(
        root, str(event.reservation_id), result="failed", error_code="controlled_failure"
    )
    with pytest.raises(ValidationError, match="development_head_mismatch"):
        journal.freeze_selection(root, bound)
    bound = selection(root)
    original = journal.freeze_selection(root, bound)
    assert journal.freeze_selection(root, bound) == original
    with pytest.raises(ValidationError, match="phase_mismatch"):
        journal.reserve(root, "development-fit")
    changed = bound.model_dump(mode="json")
    changed["bundles"][0]["model_artifact_sha256"] = "f" * 64
    with pytest.raises(SnapshotError, match="already_frozen"):
        journal.freeze_selection(root, SelectionFreeze.model_validate_json(json.dumps(changed)))


def test_all_final_seeds_and_use_cases_are_required_before_execution_close(campaign):
    root, protocol = campaign
    development(root)
    journal.freeze_selection(root, selection(root))
    complete(root, "final-42-generate")
    with pytest.raises(ValidationError, match="resolved_final_execution"):
        journal.close(root, digest("test-final-report"))
    for operation in protocol.operations:
        if operation.phase == "final" and operation.operation_id != "final-42-generate":
            complete(root, operation.operation_id)
    closed = journal.close(root, digest("test-final-report"))
    assert journal.close(root, digest("test-final-report")) == closed
    with pytest.raises(SnapshotError, match="already_frozen"):
        journal.close(root, digest("different-report"))
    with pytest.raises(ValidationError, match="after_close"):
        journal.reserve(root, "development-fit")
    assert journal.summary(root)["execution_closed"]
    assert not journal.summary(root)["stage_ready"]
    assert not journal.inspect(root).quality_qualified


def test_completion_is_idempotent_and_cannot_rewrite_failure_as_success(campaign):
    root, _ = campaign
    event = journal.reserve(root, "development-42-generate")
    finished = journal.finish(
        root, str(event.reservation_id), result="failed", error_code="controlled_failure"
    )
    assert (
        journal.finish(
            root, str(event.reservation_id), result="failed", error_code="controlled_failure"
        )
        == finished
    )
    with pytest.raises(SnapshotError, match="result_already_frozen"):
        journal.finish(
            root,
            str(event.reservation_id),
            result="completed",
            evidence_sha256="a" * 64,
            cost=FULL_COST,
        )


def test_runtime_change_denies_io_but_still_records_a_failure(campaign, monkeypatch):
    root, _ = campaign
    event = journal.reserve(root, "development-42-generate")
    current = runtime_pin()
    monkeypatch.setattr(
        journal, "runtime_pin", lambda: current.model_copy(update={"code_sha256": "f" * 64})
    )
    with pytest.raises(SnapshotError, match="runtime_changed"):
        journal.reserve(root, "development-42-read")
    with pytest.raises(SnapshotError, match="runtime_changed"):
        journal.finish(
            root,
            str(event.reservation_id),
            result="completed",
            evidence_sha256="a" * 64,
            cost=FULL_COST,
        )
    journal.finish(root, str(event.reservation_id), result="failed", error_code="runtime_changed")
    assert journal.summary(root)["failed_new_attempts"] == 1


def test_deleted_journal_existing_directory_cannot_reset_budget(campaign):
    root, protocol = campaign
    (root / "journal.json").unlink()
    with pytest.raises(SnapshotError, match="cannot_reset"):
        journal.initialize(root, protocol)
    assert not (root / "journal.json").exists()


@pytest.mark.parametrize("target", ["journal.json", "journal.lock"])
def test_symlinks_and_public_permissions_are_rejected(campaign, tmp_path, target):
    root, _ = campaign
    destination = tmp_path.resolve() / "outside"
    destination.write_bytes((root / target).read_bytes())
    original = destination.read_bytes()
    (root / target).unlink()
    (root / target).symlink_to(destination)
    with pytest.raises((SnapshotError, OSError)):
        journal.inspect(root)
    assert destination.read_bytes() == original
    (root / target).unlink()
    (root / target).write_bytes(original)
    (root / target).chmod(0o644)
    with pytest.raises(SnapshotError, match="private"):
        journal.inspect(root)


def test_partial_or_resealed_event_chain_is_rejected(campaign):
    root, _ = campaign
    complete(root, "development-42-generate")
    document = journal.inspect(root).model_dump(mode="json")
    document["events"][0]["operation_id"] = "unplanned"
    with pytest.raises(ValidationError, match="phase_mismatch"):
        CampaignJournal.model_validate_json(json.dumps(document))
    (root / "journal.json").write_bytes(b'{"partial":')
    with pytest.raises(SnapshotError, match="invalid_json"):
        journal.inspect(root)


def test_parallel_clients_cannot_overspend_the_same_frozen_operation(campaign):
    root, _ = campaign

    def attempt(_):
        try:
            complete(root, "development-42-generate")
            return True
        except ValidationError:
            return False

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(attempt, range(4)))
    assert sum(results) == 1
    assert journal.summary(root)["charged_new_attempts"] == 1
    assert journal.summary(root)["completed_new_attempts"] == 1


def test_sigkill_keeps_a_durable_charged_unresolved_fit(campaign):
    root, protocol = campaign
    complete(root, "development-42-generate")
    complete(root, "development-42-read")
    code = """
import signal, sys
from pathlib import Path
from retailops_ai.evaluation_campaign.campaign_journal import audited_operation
with audited_operation(Path(sys.argv[1]), 'development-fit'):
    print('reserved', flush=True)
    signal.pause()
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(root)], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        assert child.stdout is not None
        with selectors.DefaultSelector() as ready:
            ready.register(child.stdout, selectors.EVENT_READ)
            assert ready.select(timeout=20)
        assert child.stdout.readline() == b"reserved\n"
        child.kill()
        child.communicate(timeout=10)
        assert child.returncode == -9
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)
    journal.initialize(root, protocol)
    assert journal.summary(root)["charged_new_fit_attempts"] == 1
    assert journal.summary(root)["unresolved_new_attempts"] == 1
    assert journal.summary(root)["remaining_new_fit_attempts"] == 8
    assert journal.summary(root)["unknown_new_fit_resource_costs"] == 1
    with pytest.raises(ValidationError, match="resolved_development"):
        journal.freeze_selection(root, selection(root))


@pytest.mark.parametrize(
    "mutation",
    [
        "smaller_final_profile",
        "wrong_seed",
        "final_overlaps_development",
        "missing_scenario",
        "dangling_prerequisite",
        "extra_budget",
        "missing_final_use_case",
        "legacy_reset",
        "final_fit",
        "future_calibration",
        "final_regeneration",
    ],
)
def test_protocol_cannot_narrow_scope_or_reopen_training_on_final(tmp_path, mutation):
    document = protocol_document(tmp_path.resolve() / "new")
    if mutation == "smaller_final_profile":
        document["sources"][1]["products"] = 20
    elif mutation == "wrong_seed":
        document["sources"][3]["seed"] = 42
    elif mutation == "final_overlaps_development":
        document["sources"][1]["evaluation_origins"]["start"] = "2026-07-30"
    elif mutation == "missing_scenario":
        del document["scenario_weights"]["demand_shock"]
    elif mutation == "dangling_prerequisite":
        document["operations"][2]["prerequisites"] = ["missing"]
    elif mutation == "extra_budget":
        document["maximum_new_attempts"] += 1
    elif mutation == "missing_final_use_case":
        document["operations"][7]["use_case"] = "stockout"
    elif mutation == "legacy_reset":
        document["legacy"]["legacy_budget_available"] = 4
    elif mutation == "final_fit":
        document["operations"][7].update(action="model_fit", role="train", initialization_seed=42)
    elif mutation == "future_calibration":
        document["operations"][2].update(
            action="calibrator_fit", role="tune", initialization_seed=None
        )
    else:
        document["operations"][5]["maximum_attempts"] = 2
    with pytest.raises(ValidationError):
        CampaignProtocol.model_validate_json(json.dumps(document))


@pytest.mark.parametrize("mutation", ["missing_tensorflow", "more_tensorflow_attempts"])
def test_all_forecast_families_have_the_same_frozen_trial_and_seed_budget(tmp_path, mutation):
    document = protocol_document(tmp_path.resolve() / "new")
    if mutation == "missing_tensorflow":
        document["operations"][4]["forecast_family"] = "rf"
    else:
        document["operations"][4]["maximum_attempts"] = 2
        document["maximum_new_attempts"] -= 1
        document["maximum_new_fit_attempts"] -= 1
    with pytest.raises(ValidationError, match="equal_trial_and_seed_budgets"):
        CampaignProtocol.model_validate_json(json.dumps(document))


def test_nested_mutation_cannot_change_a_frozen_campaign_budget(campaign):
    _, protocol = campaign
    data = copy.deepcopy(protocol.model_dump(mode="json"))
    changed = CampaignProtocol.model_validate_json(json.dumps(data))
    changed.scenario_weights["demand_shock"] = 0
    with pytest.raises(ValidationError):
        changed.content_sha256()


def test_rewriting_location_or_policy_does_not_replace_an_existing_journal(campaign):
    root, protocol = campaign
    document = protocol.model_dump(mode="json")
    document["segment_policy_sha256"] = digest("changed-segments")
    with pytest.raises(SnapshotError, match="already_frozen"):
        journal.initialize(root, CampaignProtocol.model_validate_json(json.dumps(document)))
    relocated = protocol.model_copy(update={"journal_path": str(root.parent / "elsewhere")})
    with pytest.raises(SnapshotError, match="location_mismatch"):
        journal.initialize(root, relocated)
