"""Actual pre-read persistence, concurrency, crash accounting and partial-history limits."""

import hashlib
import json
import multiprocessing
import os
import shutil
import signal
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError
from test_development_comparison import protocol_for
from test_forecast_features import tables as tables
from test_forecast_manifests import timeline as timeline
from test_independent_forecast_partitions import policy as partition_policy
from test_independent_forecast_partitions import population as population
from test_tensorflow_challenger import development as development

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import outcome_cli, outcome_journal, partitions
from retailops_ai.evaluation_campaign.development_contract import ComparisonFile
from retailops_ai.evaluation_campaign.outcome_contract import (
    HistoricalOutcomeAttempt,
    HistoricalOutcomeInventory,
    HistoricalOutcomeProtocol,
    OutcomeAccessBinding,
    OutcomeAccessPlan,
    OutcomeJournalPolicy,
    OutcomePopulation,
)
from retailops_ai.evaluation_campaign.partitions import runtime_pin
from retailops_ai.evaluation_campaign.trial_contract import AttemptSnapshot, TrialPlan
from retailops_ai.evaluation_campaign.trial_registry import audit_code
from retailops_ai.evaluation_campaign.trial_registry import initialize as trial_initialize
from retailops_ai.source_snapshot.files import SnapshotError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def journal(development, tmp_path):
    fold, _, _ = development
    protocol = protocol_for(fold)
    old = tmp_path / "historical-attempt"
    old.mkdir()
    raw = canonical_bytes(protocol.model_dump(mode="json")) + b"\n"
    (old / "protocol.json").write_bytes(raw)
    protocol_hash = canonical_sha256(protocol.model_dump(mode="json"))
    file_hash = hashlib.sha256(raw).hexdigest()
    old_registry = tmp_path / "historical-registry"
    old_plan = TrialPlan(
        registry_path=str(old_registry),
        protocols=(protocol,),
        maximum_new_attempts=1,
        audit_code_sha256=audit_code(),
        historical_attempts=(
            AttemptSnapshot(
                output=str(old),
                protocol_sha256=protocol_hash,
                status="interrupted",
                files={"protocol.json": ComparisonFile(size_bytes=len(raw), sha256=file_hash)},
                model_starts=0,
                model_completions=0,
                cost_files=(),
            ),
        ),
    )
    trial_initialize(old_registry, old_plan)
    preparation = tmp_path / "historical-preparation.json"
    preparation.write_bytes(
        canonical_bytes(
            {
                "development_split_verification_reads_existing_development_labels": True,
                "portfolio_final_test_accessed": False,
                "split": "/not/a/real/dataset/path/must/not/be/read",
            }
        )
        + b"\n"
    )
    history = HistoricalOutcomeInventory(
        scope="historical_outcome_access_declaration_from_existing_metadata_only",
        source_ledger=str(old_registry / "ledger.json"),
        source_ledger_sha256=hashlib.sha256(
            (old_registry / "ledger.json").read_bytes()
        ).hexdigest(),
        source_preparation_receipt=str(preparation),
        source_preparation_sha256=hashlib.sha256(preparation.read_bytes()).hexdigest(),
        observed_existing_attempts=(
            HistoricalOutcomeAttempt(
                output=str(old),
                protocol_file_sha256=file_hash,
                protocol_sha256=protocol_hash,
                status="interrupted",
            ),
        ),
        observed_protocols={
            protocol_hash: HistoricalOutcomeProtocol(
                parent=protocol.parent,
                feature_set_id=protocol.feature_set_id,
                split_id=protocol.split_id,
                fold=fold,
                source_parameters=protocol.source_parameters,
                existing_development_holdout_freshness="opened_during_parent_verification_not_untouched",
                provenance="retrospective_metadata_and_preparation_receipt_not_pre_read_journal",
            )
        },
        freshness_of_unlisted_data="unknown_not_automatically_unseen",
        prior_global_access_audit_completed=False,
        historical_files_mutated=False,
        new_project_labels_read=False,
        project_data_fits=0,
        portfolio_final_test_authorized=False,
    )
    history_path = tmp_path / "history.json"
    history_path.write_bytes(canonical_bytes(history.model_dump(mode="json")) + b"\n")
    root = tmp_path / "outcome-journal"
    policy = OutcomeJournalPolicy(
        journal_path=str(root),
        historical_inventory_path=str(history_path),
        history=history,
        historical_inventory_file_sha256=hashlib.sha256(history_path.read_bytes()).hexdigest(),
        runtime=runtime_pin(),
        audit_code_sha256=outcome_journal.audit_code(),
        maximum_new_reads=2,
        maximum_reads_per_binding=2,
    )
    population = OutcomePopulation(
        data_seed=42,
        source_dataset_id=protocol.parent.source_dataset_id,
        snapshot_id=protocol.parent.snapshot_id,
        curated_dataset_id=protocol.parent.curated_dataset_id,
        feature_set_id=protocol.feature_set_id,
        partition_id="ai09-partitions-sha256-" + "1" * 64,
        role="train",
        origins=fold.train,
        label_knowledge_cutoff=fold.training_cutoff,
        membership_keys_sha256="2" * 64,
        outcome_artifact_sha256="3" * 64,
    )
    binding = OutcomeAccessBinding(
        population=population,
        purpose="model_fit",
        training_initialization_seed=42,
        recipe_sha256="4" * 64,
    )
    plan = OutcomeAccessPlan(
        protocol_sha256=protocol_hash, runtime=runtime_pin(), bindings=(binding,)
    )
    return root, policy, plan, binding


def prepared(journal):
    root, policy, plan, binding = journal
    outcome_journal.initialize(root, policy)
    digest = outcome_journal.register_plan(root, plan)
    return root, policy, plan, binding, digest


def test_context_persists_before_real_read_and_logs_every_replay(journal, tmp_path):
    root, policy, plan, binding, digest = prepared(journal)
    outcomes = tmp_path / "controlled-outcomes.json"
    outcomes.write_bytes(b'{"controlled_label":7}\n')
    ids = []
    for _ in range(2):
        with outcome_journal.audited_access(root, digest, binding) as event:
            receipt = outcome_journal.summary(outcome_journal.inspect(root))
            assert receipt["unresolved_reads"] == 1
            assert receipt["reserved_reads"] == len(ids) + 1
            assert outcomes.read_bytes() == b'{"controlled_label":7}\n'
            ids.append(event.access_id)
    assert len(set(ids)) == 2
    assert outcome_journal.summary(outcome_journal.inspect(root))["completed_reads"] == 2
    assert outcome_journal.initialize(root, policy) == outcome_journal.inspect(root)
    assert outcome_journal.register_plan(root, plan) == digest
    with pytest.raises(SnapshotError, match="budget_exhausted"):
        with outcome_journal.audited_access(root, digest, binding):
            pytest.fail("reader entered despite exhausted budget")


@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt])
def test_failed_or_interrupted_reader_is_charged_and_error_contents_are_not_logged(journal, error):
    root, _, _, binding, digest = prepared(journal)
    with pytest.raises(error):
        with outcome_journal.audited_access(root, digest, binding):
            raise error("sensitive_controlled_row_contents")
    receipt = outcome_journal.summary(outcome_journal.inspect(root))
    assert receipt["failed_reads"] == 1 and receipt["remaining_read_budget"] == 1
    assert b"sensitive_controlled_row_contents" not in (root / "journal.json").read_bytes()


def _crash(root, digest, binding):
    with outcome_journal.audited_access(Path(root), digest, binding):
        os.kill(os.getpid(), signal.SIGKILL)


def test_sigkill_after_pre_read_reservation_survives_restart(journal):
    root, policy, _, binding, digest = prepared(journal)
    worker = multiprocessing.get_context("fork").Process(
        target=_crash, args=(str(root), digest, binding)
    )
    worker.start()
    worker.join(timeout=10)
    assert worker.exitcode == -signal.SIGKILL
    receipt = outcome_journal.summary(outcome_journal.initialize(root, policy))
    assert receipt["reserved_reads"] == receipt["unresolved_reads"] == 1
    assert receipt["remaining_read_budget"] == 1


def _race(root, digest, binding, ready, results):
    ready.wait()
    try:
        outcome_journal.reserve(Path(root), digest, binding)
        results.put("reserved")
    except SnapshotError as exc:
        results.put(str(exc))


def test_concurrent_readers_cannot_spend_the_last_global_slot_twice(journal):
    root, policy, plan, binding = journal
    policy = policy.model_copy(update={"maximum_new_reads": 1})
    outcome_journal.initialize(root, policy)
    digest = outcome_journal.register_plan(root, plan)
    ctx = multiprocessing.get_context("fork")
    ready, results = ctx.Event(), ctx.Queue()
    workers = [
        ctx.Process(target=_race, args=(str(root), digest, binding, ready, results))
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    ready.set()
    observed = sorted(results.get(timeout=10) for _ in workers)
    for worker in workers:
        worker.join(timeout=10)
        assert worker.exitcode == 0
    assert observed == ["outcome_journal_read_budget_exhausted", "reserved"]
    assert outcome_journal.summary(outcome_journal.inspect(root))["unresolved_reads"] == 1


@pytest.mark.parametrize(
    "field",
    [
        "recipe_sha256",
        "fitted_candidate_sha256",
        "calibrator_sha256",
        "thresholds_sha256",
        "training_initialization_seed",
        "population",
    ],
)
def test_changed_frozen_binding_never_enters_reader(journal, field):
    root, _, _, binding, digest = prepared(journal)
    change = 137 if field == "training_initialization_seed" else "f" * 64
    if field == "population":
        change = binding.population.model_copy(update={"outcome_artifact_sha256": "f" * 64})
    changed = binding.model_copy(update={field: change})
    before = (root / "journal.json").read_bytes()
    with pytest.raises(SnapshotError, match="unplanned_access"):
        with outcome_journal.audited_access(root, digest, changed):
            pytest.fail("unplanned reader entered")
    assert (root / "journal.json").read_bytes() == before


def test_same_binding_under_new_protocol_cannot_reset_its_read_budget(journal):
    root, _, plan, binding, digest = prepared(journal)
    other = outcome_journal.register_plan(
        root, plan.model_copy(update={"protocol_sha256": "f" * 64})
    )
    outcome_journal.reserve(root, digest, binding)
    outcome_journal.reserve(root, other, binding)
    with pytest.raises(SnapshotError, match="budget_exhausted"):
        outcome_journal.reserve(root, other, binding)


def test_terminal_receipt_is_immutable_but_same_finish_is_idempotent(journal):
    root, _, _, binding, digest = prepared(journal)
    event = outcome_journal.reserve(root, digest, binding)
    terminal = outcome_journal.finish(
        root, event.access_id, result="failed", error_code="controlled_failure"
    )
    assert (
        outcome_journal.finish(
            root, event.access_id, result="failed", error_code="controlled_failure"
        )
        == terminal
    )
    with pytest.raises(SnapshotError, match="already_frozen"):
        outcome_journal.finish(root, event.access_id, result="completed")
    with pytest.raises(SnapshotError, match="unknown_reservation"):
        outcome_journal.finish(root, "outcome-access-" + "0" * 32, result="completed")


@pytest.mark.parametrize(
    "mutation", ["chain", "delete", "noncanonical", "duplicate_key", "resealed_duplicate_finish"]
)
def test_corruption_is_rejected_before_new_access(journal, mutation):
    root, _, _, binding, digest = prepared(journal)
    event = outcome_journal.reserve(root, digest, binding)
    outcome_journal.finish(root, event.access_id, result="completed")
    data = json.loads((root / "journal.json").read_bytes())
    if mutation == "chain":
        data["events"][1]["binding_sha256"] = "f" * 64
    elif mutation == "delete":
        data["events"] = []
    elif mutation == "resealed_duplicate_finish":
        data["events"].append(dict(data["events"][-1]))
        head = canonical_sha256(data["policy"])
        for index, item in enumerate(data["events"], 1):
            item.update(sequence=index, previous_sha256=head)
            head = canonical_sha256(item)
        data["head_sha256"] = head
    raw = canonical_bytes(data) + b"\n"
    if mutation == "noncanonical":
        raw = json.dumps(data, indent=2).encode()
    elif mutation == "duplicate_key":
        raw = raw.replace(b'"events":', b'"events":[],"events":', 1)
    (root / "journal.json").write_bytes(raw)
    with pytest.raises(ValueError):
        outcome_journal.inspect(root)
    with pytest.raises(ValueError):
        outcome_journal.reserve(root, digest, binding)
    assert (root / "journal.json").read_bytes() == raw


@pytest.mark.parametrize(
    "source", ["source_ledger", "source_preparation_receipt", "protocol", "history"]
)
def test_changed_original_metadata_blocks_initialization_without_opening_dataset(journal, source):
    root, policy, _, _ = journal
    path = Path(
        {
            "source_ledger": policy.history.source_ledger,
            "source_preparation_receipt": policy.history.source_preparation_receipt,
            "protocol": str(
                Path(policy.history.observed_existing_attempts[0].output) / "protocol.json"
            ),
            "history": policy.historical_inventory_path,
        }[source]
    )
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(SnapshotError, match="changed|freeze_mismatch"):
        outcome_journal.initialize(root, policy)
    assert not root.exists()


def test_partial_inventory_and_relabelled_data_never_become_unseen(journal):
    root, _, plan, binding, digest = prepared(journal)
    outcome_journal.reserve(root, digest, binding)
    population = binding.population.model_copy(
        update={
            "role": "development_evaluation",
            "partition_id": "ai09-partitions-sha256-" + "f" * 64,
        }
    )
    status = outcome_journal.exposure_status(outcome_journal.inspect(root), population)
    assert status["related_historical_protocols"] == status["related_reserved_reads"] == 1
    assert status["freshness"] == "not_established_partial_audit"
    unknown = population.model_copy(
        update={
            "data_seed": 2026,
            "source_dataset_id": "source-sha256-" + "f" * 64,
            "snapshot_id": "snapshot-sha256-" + "f" * 64,
            "outcome_artifact_sha256": "f" * 64,
        }
    )
    status = outcome_journal.exposure_status(outcome_journal.inspect(root), unknown)
    assert status["related_historical_protocols"] == status["related_reserved_reads"] == 0
    assert status["freshness"] == "not_established_partial_audit"
    assert not status["independent_evaluation_access_authorized"]


def test_independent_evaluation_is_blocked_before_journal_or_reader_access(journal, tmp_path):
    _, _, plan, binding = journal
    binding = binding.model_copy(
        update={
            "population": binding.population.model_copy(update={"role": "development_evaluation"}),
            "purpose": "independent_evaluation",
        }
    )
    with pytest.raises(SnapshotError, match="complete_access_audit"):
        with outcome_journal.audited_access(tmp_path / "does-not-exist", "f" * 64, binding):
            pytest.fail("independent reader entered")
    with pytest.raises(ValidationError, match="complete_access_audit"):
        outcome_journal.register_plan(
            tmp_path / "does-not-exist", plan.model_copy(update={"bindings": (binding,)})
        )


@pytest.mark.parametrize(
    "flags",
    [
        {"independent_evaluation_access_authorized": True},
        {"final_test_access_authorized": True},
        {"promotion_allowed": True},
        {"maximum_new_reads": 129},
    ],
)
def test_policy_cannot_grant_evaluation_or_unbounded_access(journal, flags):
    _, policy, _, _ = journal
    with pytest.raises(ValidationError):
        OutcomeJournalPolicy.model_validate_json(
            canonical_bytes(policy.model_dump(mode="json") | flags)
        )


@pytest.mark.parametrize("mutation", ["purged", "final_test", "wrong_role", "missing_candidate"])
def test_binding_rejects_unsupported_or_mixed_role(journal, mutation):
    _, _, _, binding = journal
    data = binding.model_dump(mode="json")
    if mutation in ("purged", "final_test"):
        data["population"]["role"] = mutation
    elif mutation == "wrong_role":
        data["population"]["role"] = "tune"
    else:
        data["population"]["role"] = "calibration"
        data["purpose"] = "calibrator_fit"
    with pytest.raises(ValidationError):
        OutcomeAccessBinding.model_validate_json(canonical_bytes(data))


def test_safe_permissions_location_and_deleted_history_cannot_reset(journal, tmp_path):
    root, policy, _, binding, digest = prepared(journal)
    outcome_journal.reserve(root, digest, binding)
    assert root.stat().st_mode & 0o777 == 0o700
    assert (root / "journal.json").stat().st_mode & 0o777 == 0o600
    clone = tmp_path / "copied-journal"
    shutil.copytree(root, clone)
    with pytest.raises(SnapshotError, match="location_mismatch"):
        outcome_journal.inspect(clone)
    link = tmp_path / "linked-journal"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(SnapshotError, match="symlink"):
        outcome_journal.inspect(link)
    (root / "journal.json").unlink()
    with pytest.raises(SnapshotError, match="cannot_reset"):
        outcome_journal.initialize(root, policy)


def test_failed_atomic_write_keeps_previous_history_and_reader_is_not_entered(journal, monkeypatch):
    root, _, _, binding, digest = prepared(journal)
    before = (root / "journal.json").read_bytes()

    def fail(*args, **kwargs):
        raise OSError("controlled_publish_failure")

    monkeypatch.setattr(outcome_journal.os, "replace", fail)
    with pytest.raises(OSError, match="controlled_publish_failure"):
        with outcome_journal.audited_access(root, digest, binding):
            pytest.fail("reader entered before durable reservation")
    assert (root / "journal.json").read_bytes() == before
    assert not list(root.glob(".outcome-journal-*"))


def test_runtime_change_blocks_read_but_keeps_old_reservation_visible(journal, monkeypatch):
    root, _, _, binding, digest = prepared(journal)
    outcome_journal.reserve(root, digest, binding)
    runtime = runtime_pin().model_copy(update={"dependency_lock_sha256": "f" * 64})
    monkeypatch.setattr(outcome_journal, "runtime_pin", lambda: runtime)
    with pytest.raises(SnapshotError, match="runtime_changed"):
        outcome_journal.reserve(root, digest, binding)
    assert outcome_journal.summary(outcome_journal.inspect(root))["unresolved_reads"] == 1


def test_cli_freeze_inspect_preflight_and_schemas(journal, monkeypatch, capsys):
    root, policy, plan, binding = journal
    monkeypatch.setattr(
        "sys.argv",
        [
            "outcome-cli",
            "freeze",
            "--journal",
            str(root),
            "--history",
            policy.historical_inventory_path,
            "--expected-history-sha256",
            policy.historical_inventory_file_sha256,
        ],
    )
    assert outcome_cli.main() == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["historical_attempts"] == 1 and receipt["reserved_reads"] == 0
    for command, expected in (("verify-history", 0), ("preflight", 3)):
        monkeypatch.setattr("sys.argv", ["outcome-cli", command, "--journal", str(root)])
        assert outcome_cli.main() == expected
        assert json.loads(capsys.readouterr().out)["evaluation_status"] == "not_ready"
    ledger = outcome_journal.inspect(root)
    for name, value in (
        ("outcome_access_binding", binding),
        ("outcome_access_plan", plan),
        ("outcome_journal_policy", ledger.policy),
        ("outcome_journal", ledger),
    ):
        schema = json.loads(
            (ROOT / "contracts/evaluation/v5" / (name + ".schema.json")).read_bytes()
        )
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(value.model_dump(mode="json"), schema)
    monkeypatch.setattr(
        "sys.argv",
        [
            "outcome-cli",
            "freeze",
            "--journal",
            str(root),
            "--history",
            policy.historical_inventory_path,
            "--expected-history-sha256",
            "f" * 64,
        ],
    )
    assert outcome_cli.main() == 2
    assert (
        json.loads(capsys.readouterr().out)["error_code"]
        == "outcome_journal_history_freeze_mismatch"
    )


def test_population_is_bound_to_complete_role_and_source_metadata_without_labels(
    population, tmp_path
):
    source, _, calls = population
    root = partitions.prepare_partitions(source, partition_policy(), tmp_path / "partitions")
    manifest = partitions.verify_partitions(source, root)
    for role in ("train", "early_stopping", "tune", "calibration", "development_evaluation"):
        result = outcome_journal.forecast_population(
            manifest, role, outcome_artifact_sha256="f" * 64
        )
        assert result.partition_id == manifest.partition_id
        assert (
            result.source_dataset_id
            == manifest.descriptor.feature_descriptor.parent.source_dataset_id
        )
        assert result.membership_keys_sha256 == manifest.descriptor.populations[role].keys_sha256
        assert result.origins == next(
            r.origins for r in manifest.descriptor.policy.roles if r.role == role
        )
    assert set(calls) == {"features"}
    with pytest.raises(SnapshotError, match="unsupported_role"):
        outcome_journal.forecast_population(manifest, "purged", outcome_artifact_sha256="f" * 64)


@pytest.mark.parametrize("mutation", ["root_mode", "file_mode", "lock_symlink", "file_symlink"])
def test_unsafe_journal_paths_never_allow_a_reader(journal, mutation, tmp_path):
    root, _, _, binding, digest = prepared(journal)
    if mutation == "root_mode":
        root.chmod(0o755)
    elif mutation == "file_mode":
        (root / "journal.json").chmod(0o644)
    else:
        path = root / ("journal.lock" if mutation == "lock_symlink" else "journal.json")
        target = tmp_path / "protected-metadata"
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target)
    with pytest.raises((OSError, SnapshotError)):
        with outcome_journal.audited_access(root, digest, binding):
            pytest.fail("unsafe reader entered")


def test_fsync_failure_after_atomic_replace_keeps_reservation_charged(journal, monkeypatch):
    root, _, _, binding, digest = prepared(journal)
    original = outcome_journal.os.fsync
    calls = []

    def fail_directory(descriptor):
        calls.append(descriptor)
        if len(calls) == 2:
            raise OSError("controlled_directory_fsync_failure")
        original(descriptor)

    monkeypatch.setattr(outcome_journal.os, "fsync", fail_directory)
    with pytest.raises(OSError, match="controlled_directory_fsync_failure"):
        with outcome_journal.audited_access(root, digest, binding):
            pytest.fail("reader entered before directory fsync")
    assert outcome_journal.summary(outcome_journal.inspect(root))["unresolved_reads"] == 1


def test_global_quota_cannot_reset_with_new_binding_and_frozen_policy_cannot_change(journal):
    root, policy, plan, binding = journal
    policy = policy.model_copy(update={"maximum_new_reads": 1, "maximum_plans": 2})
    outcome_journal.initialize(root, policy)
    digest = outcome_journal.register_plan(root, plan)
    outcome_journal.reserve(root, digest, binding)
    changed = binding.model_copy(update={"recipe_sha256": "f" * 64})
    new_plan = plan.model_copy(update={"protocol_sha256": "f" * 64, "bindings": (changed,)})
    new_digest = outcome_journal.register_plan(root, new_plan)
    with pytest.raises(SnapshotError, match="read_budget_exhausted"):
        outcome_journal.reserve(root, new_digest, changed)
    with pytest.raises(SnapshotError, match="plan_budget_exhausted"):
        outcome_journal.register_plan(root, plan.model_copy(update={"protocol_sha256": "e" * 64}))
    with pytest.raises(SnapshotError, match="already_frozen"):
        outcome_journal.initialize(root, policy.model_copy(update={"maximum_new_reads": 2}))


def test_journal_cannot_be_created_inside_historical_inputs(journal):
    _, policy, _, _ = journal
    root = Path(policy.history.observed_existing_attempts[0].output) / "journal"
    policy = policy.model_copy(update={"journal_path": str(root)})
    with pytest.raises(SnapshotError, match="overlaps_historical_input"):
        outcome_journal.initialize(root, policy)
    assert not root.exists()


def test_new_reader_code_requires_new_plan_and_preserves_shared_history(journal, monkeypatch):
    root, policy, plan, binding, digest = prepared(journal)
    outcome_journal.reserve(root, digest, binding)
    current = runtime_pin()
    hashes = current.code_files | {"evaluation_campaign/new_label_reader.py": "f" * 64}
    changed = current.model_copy(
        update={"code_files": hashes, "code_sha256": canonical_sha256(hashes)}
    )
    monkeypatch.setattr(outcome_journal, "runtime_pin", lambda: changed)
    with pytest.raises(SnapshotError, match="execution_runtime_changed"):
        outcome_journal.reserve(root, digest, binding)
    with pytest.raises(SnapshotError, match="execution_runtime_changed"):
        outcome_journal.register_plan(root, plan)
    assert (
        outcome_journal.summary(outcome_journal.initialize(root, policy))["unresolved_reads"] == 1
    )
    new_digest = outcome_journal.register_plan(root, plan.model_copy(update={"runtime": changed}))
    outcome_journal.reserve(root, new_digest, binding)
    assert outcome_journal.summary(outcome_journal.inspect(root))["reserved_reads"] == 2
    with pytest.raises(SnapshotError, match="read_budget_exhausted"):
        outcome_journal.reserve(root, new_digest, binding)


def test_changed_auditor_cannot_grant_new_access_or_register_plan(journal, monkeypatch):
    root, _, plan, binding, digest = prepared(journal)
    before = (root / "journal.json").read_bytes()
    monkeypatch.setattr(outcome_journal, "audit_code", lambda: "f" * 64)
    with pytest.raises(SnapshotError, match="runtime_changed"):
        outcome_journal.reserve(root, digest, binding)
    with pytest.raises(SnapshotError, match="runtime_changed"):
        outcome_journal.register_plan(root, plan)
    assert (root / "journal.json").read_bytes() == before
