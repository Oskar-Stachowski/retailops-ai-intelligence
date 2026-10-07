"""Physical scope, complete coverage, PIT maturity and pre-read durable reservations."""

import hashlib
import json
import multiprocessing
import os
import shutil
import signal
import sqlite3
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError
from test_forecast_features import DAY, SERIES
from test_forecast_features import tables as tables
from test_forecast_manifests import artifacts as artifacts
from test_forecast_manifests import timeline as timeline
from test_independent_forecast_partitions import policy
from test_independent_forecast_partitions import population as population
from test_outcome_access_journal import journal as journal
from test_tensorflow_challenger import development as development

from retailops_ai.data_contracts.common import DateWindow, ForecastKey, end_of_day
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import label_cli, labels, outcome_journal, partitions
from retailops_ai.evaluation_campaign.label_contract import (
    DemandVersion,
    ForecastOutcomeReadPolicy,
    ForecastOutcomeReadProtocol,
    OutcomeEvidence,
    OutcomeEvidenceManifest,
)
from retailops_ai.evaluation_campaign.outcome_contract import (
    OutcomeAccessBinding,
    OutcomeAccessPlan,
)
from retailops_ai.evaluation_campaign.partition_contract import ROLES, PartitionMembership
from retailops_ai.forecasting.contract import make_origin
from retailops_ai.forecasting.features import OriginFeatures
from retailops_ai.forecasting.manifest_contract import FeaturePolicy
from retailops_ai.forecasting.splits import label_point, qualify
from retailops_ai.source_snapshot.files import SnapshotError

ROOT = Path(__file__).resolve().parents[1]


def candidate(key, **changes):
    return DemandVersion.model_validate(
        {
            "product_id": key.product_id,
            "selling_location_id": key.selling_location_id,
            "channel": key.channel,
            "business_date": key.target_date,
            "record_id": "demand-" + key.target_date.isoformat(),
            "source_record_sha256": "9" * 64,
            "version": 1,
            "curated_available_at": end_of_day(key.target_date + timedelta(days=1)),
            "observed_units": 7,
            "observation_status": "observed_positive",
            "source_data_complete": True,
            "quality_status": "valid",
            **changes,
        }
    )


def freeze_evidence(partition_manifest, root, role, members, records=None, raw=None):
    if records is None:
        records = [
            OutcomeEvidence(
                key=ForecastKey.model_validate(m.model_dump(include=set(partitions.KEY_FIELDS))),
                candidates=(candidate(m),),
            ).model_dump(mode="json")
            for m in members
        ]
    if raw is None:
        raw = b"".join(canonical_bytes(r) + b"\n" for r in records)
    manifest = OutcomeEvidenceManifest(
        population=outcome_journal.forecast_population(
            partition_manifest, role, outcome_artifact_sha256=hashlib.sha256(raw).hexdigest()
        ),
        row_count=len(members),
        size_bytes=len(raw),
    )
    root.mkdir(mode=0o700, exist_ok=True)
    (root / "outcomes.jsonl").write_bytes(raw)
    (root / "manifest.json").write_bytes(canonical_bytes(manifest.model_dump(mode="json")) + b"\n")
    return manifest


@pytest.fixture
def reader_fixture(population, timeline, journal, tmp_path, monkeypatch):
    """Typed unit parent; physical role and evidence files and the journal are real."""
    features, feature_rows, _ = population
    root = partitions.prepare_partitions(features, policy(), tmp_path / "partitions")
    manifest = partitions.verify_partitions(features, root)
    histories = {
        r.history_context_sha256: OriginFeatures(
            timeline, make_origin(r.forecast_origin.date())
        ).history(SERIES)
        for r in feature_rows
    }
    monkeypatch.setattr(
        labels,
        "input_models",
        lambda path, name: iter(feature_rows if name == "features" else histories.values()),
    )
    monkeypatch.setattr(
        "retailops_ai.forecasting.splits.input_models",
        lambda path, name: iter(histories.values()),
    )
    journal_root, journal_policy, _, _ = journal
    outcome_journal.initialize(journal_root, journal_policy)
    return features, root, manifest, journal_root, feature_rows, histories


def register(
    reader_fixture,
    tmp_path,
    role="train",
    purpose="verification",
    records=None,
    raw=None,
    reader_policy=None,
):
    features, root, manifest, journal_root, _, _ = reader_fixture
    members = [
        PartitionMembership.model_validate_json(line)
        for line in (root / ("memberships/" + role + ".jsonl")).read_bytes().splitlines()
    ]
    evidence_root = tmp_path / ("evidence-" + role)
    evidence = freeze_evidence(manifest, evidence_root, role, members, records, raw)
    protocol = ForecastOutcomeReadProtocol(
        partitions=manifest,
        evidence=(evidence,),
        policy=reader_policy or ForecastOutcomeReadPolicy(),
    )
    binding = OutcomeAccessBinding(
        population=evidence.population,
        purpose=purpose,
        training_initialization_seed=137,
        recipe_sha256="4" * 64,
        fitted_candidate_sha256="5" * 64 if purpose == "calibrator_fit" else None,
    )
    plan = OutcomeAccessPlan(
        protocol_sha256=canonical_sha256(protocol.model_dump(mode="json")),
        runtime=partitions.runtime_pin(),
        bindings=(binding,),
    )
    digest = outcome_journal.register_plan(journal_root, plan)
    return (features, root, evidence_root, protocol), {
        "journal": journal_root,
        "plan_sha256": digest,
        "binding": binding,
    }


def state(root):
    return outcome_journal.summary(outcome_journal.inspect(root))


@pytest.mark.parametrize(
    ("role", "purpose"),
    [(r, "verification") for r in ROLES]
    + [
        ("train", "model_fit"),
        ("train", "preprocessing_fit"),
        ("early_stopping", "early_stopping"),
        ("tune", "recipe_selection"),
        ("calibration", "calibrator_fit"),
    ],
)
def test_all_five_roles_preserve_every_key_and_only_bound_outcome_artifact(
    reader_fixture, tmp_path, role, purpose, monkeypatch
):
    args, kwargs = register(reader_fixture, tmp_path, role, purpose)
    root = kwargs["journal"]
    original = labels._copy_evidence
    calls = []

    def observe(path, manifest, destination):
        assert state(root)["reserved_reads"] == 1
        assert state(root)["completed_reads"] == 0
        calls.append(path)
        return original(path, manifest, destination)

    monkeypatch.setattr(labels, "_copy_evidence", observe)
    with labels.open_forecast_outcomes(*args, **kwargs) as reader:
        rows = list(reader.rows())
        assert len(rows) == args[3].evidence[0].row_count
        assert {r.label.role for r in rows} == {role}
        assert all(r.label.observed_sales_units == 7 for r in rows)
        result = reader.summary()
        assert result["counts"]["rows"] == args[3].evidence[0].row_count
        assert result["source_qualification"] == "not_established"
        assert result["evaluation_status"] == "not_ready"
        assert result["freshness"] == "not_asserted_partial_access_audit"
    assert calls == [args[2]]
    assert state(root)["completed_reads"] == 1


def test_every_input_read_and_public_verification_occurs_after_reservation(
    reader_fixture, tmp_path, monkeypatch
):
    args, kwargs = register(reader_fixture, tmp_path)
    for name in ("_parent_seal", "_copy_evidence", "verify_partitions", "input_models"):
        original = getattr(labels, name)

        def observe(*a, _original=original, **k):
            assert state(kwargs["journal"])["reserved_reads"] == 1
            assert state(kwargs["journal"])["completed_reads"] == 0
            return _original(*a, **k)

        monkeypatch.setattr(labels, name, observe)
    with labels.open_forecast_outcomes(*args, **kwargs) as reader:
        assert len(list(reader.rows())) == 14


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate",
        "reorder",
        "outside_role",
        "wrong_horizon",
        "extra_last",
        "noncanonical",
        "candidate_scope",
        "ambiguous_version",
        "null_complete",
    ],
)
def test_resealed_evidence_cannot_hide_semantic_or_complete_population_errors(
    reader_fixture, tmp_path, mutation
):
    _, root, _, journal_root, _, _ = reader_fixture
    members = [
        PartitionMembership.model_validate_json(line)
        for line in (root / "memberships/train.jsonl").read_bytes().splitlines()
    ]
    records = [
        OutcomeEvidence(
            key=ForecastKey.model_validate(m.model_dump(include=set(partitions.KEY_FIELDS))),
            candidates=(candidate(m),),
        ).model_dump(mode="json")
        for m in members
    ]
    if mutation == "missing":
        records.pop()
    elif mutation == "duplicate":
        records[-1] = records[0]
    elif mutation == "reorder":
        records.reverse()
    elif mutation == "outside_role":
        records[-1]["key"]["selling_location_id"] = "outside-population"
        records[-1]["candidates"] = []
    elif mutation == "wrong_horizon":
        records[-1]["key"]["horizon_days"] = 3
    elif mutation == "extra_last":
        records.append(records[-1])
    elif mutation == "candidate_scope":
        records[-1]["candidates"][0]["product_id"] = "outside-population"
    elif mutation == "ambiguous_version":
        records[-1]["candidates"].append(records[-1]["candidates"][0])
    elif mutation == "null_complete":
        records[-1]["candidates"][0]["source_data_complete"] = None
    raw = (json.dumps(records[0]) + "\n").encode() if mutation == "noncanonical" else None
    args, kwargs = register(reader_fixture, tmp_path, records=records, raw=raw)
    yielded = False
    with pytest.raises((SnapshotError, ValidationError)):
        with labels.open_forecast_outcomes(*args, **kwargs):
            yielded = True
    assert not yielded
    assert state(journal_root)["failed_reads"] == 1
    assert state(journal_root)["remaining_read_budget"] == 1


@pytest.mark.parametrize("mutation", ["checksum", "manifest", "symlink", "extra_file"])
def test_physical_corruption_is_charged_before_any_consumer_row(reader_fixture, tmp_path, mutation):
    args, kwargs = register(reader_fixture, tmp_path)
    evidence = args[2]
    if mutation == "checksum":
        raw = (evidence / "outcomes.jsonl").read_bytes()
        (evidence / "outcomes.jsonl").write_bytes(
            raw.replace(b'"observed_units":7', b'"observed_units":8')
        )
    elif mutation == "manifest":
        (evidence / "manifest.json").write_text("{}")
    elif mutation == "extra_file":
        (evidence / "another-role-targets.json").write_text("must never be parsed")
    else:
        target = tmp_path / "target.jsonl"
        shutil.move(evidence / "outcomes.jsonl", target)
        (evidence / "outcomes.jsonl").symlink_to(target)
    with pytest.raises((OSError, SnapshotError)):
        with labels.open_forecast_outcomes(*args, **kwargs):
            pytest.fail("consumer received unverified evidence")
    assert state(kwargs["journal"])["failed_reads"] == 1


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "late",
        "unavailable",
        "premature",
        "early_complete",
        "cutoff_not_elapsed",
        "latest_incomplete",
        "quarantined",
        "null_units",
        "source_incomplete",
        "zero",
        "closed",
        "late_correction",
        "known_correction",
    ],
)
def test_actual_availability_latest_revision_and_censoring(reader_fixture, case):
    _, _, manifest, _, feature_rows, histories = reader_fixture
    row = next(r for r in feature_rows if r.forecast_origin.date() == DAY)
    member = partitions._membership(row, manifest.descriptor.policy)
    first = candidate(row)
    if case == "missing":
        versions = ()
    elif case == "late":
        versions = (
            candidate(
                row, curated_available_at=member.label_knowledge_cutoff + timedelta(seconds=1)
            ),
        )
    elif case == "unavailable":
        versions = (candidate(row, curated_available_at=None),)
    elif case == "premature":
        versions = (
            candidate(row, curated_available_at=end_of_day(row.target_date) - timedelta(seconds=1)),
        )
    elif case in ("early_complete", "cutoff_not_elapsed"):
        versions = (
            candidate(row, curated_available_at=end_of_day(row.target_date) + timedelta(seconds=1)),
        )
        if case == "cutoff_not_elapsed":
            member = member.model_copy(
                update={
                    "label_knowledge_cutoff": end_of_day(row.target_date + timedelta(days=1))
                    - timedelta(seconds=1)
                }
            )
    elif case == "latest_incomplete":
        versions = (first, candidate(row, version=2, source_data_complete=False))
    elif case == "quarantined":
        versions = (candidate(row, quality_status="quarantined"),)
    elif case == "null_units":
        versions = (candidate(row, observed_units=None),)
    elif case == "source_incomplete":
        versions = (candidate(row, source_data_complete=False),)
    elif case in ("zero", "closed"):
        versions = (
            candidate(
                row,
                observed_units=0,
                observation_status="observed_zero" if case == "zero" else "closed",
            ),
        )
    else:
        versions = (
            first,
            candidate(
                row,
                version=2,
                observed_units=99,
                curated_available_at=member.label_knowledge_cutoff + timedelta(seconds=1)
                if case == "late_correction"
                else member.label_knowledge_cutoff,
            ),
        )
    evidence = OutcomeEvidence(
        key=ForecastKey.model_validate(row.model_dump(include=set(partitions.KEY_FIELDS))),
        candidates=versions,
    )
    result = labels.qualify_evidence(
        evidence, member, row, histories[row.history_context_sha256], FeaturePolicy(), 1
    )
    if case in ("zero", "closed", "late_correction", "known_correction", "early_complete"):
        assert result.label.status == "eligible"
        assert result.label.observed_sales_units == (
            0 if case in ("zero", "closed") else 99 if case == "known_correction" else 7
        )
    else:
        assert result.label.status == "censored"
        assert result.label.observed_sales_units is None
        assert "censored_label" in result.reasons
    if case == "latest_incomplete":
        assert result.label.selected_version.version == 2
    if case == "late_correction":
        assert result.label.selected_version.version == 1


def test_eligibility_matches_existing_forecast_policy_and_retains_closed_targets(reader_fixture):
    _, _, manifest, _, rows, histories = reader_fixture
    row = next(r for r in rows if r.forecast_origin.date() == DAY)
    member = partitions._membership(row, manifest.descriptor.policy)
    evidence = OutcomeEvidence(
        key=ForecastKey.model_validate(row.model_dump(include=set(partitions.KEY_FIELDS))),
        candidates=(candidate(row),),
    )
    result = labels.qualify_evidence(
        evidence, member, row, histories[row.history_context_sha256], FeaturePolicy(), 1
    )
    assert result.eligible
    value = row.model_dump(mode="json")
    opening = next(v for v in value["values"] if v["name"] == "target_location_open")
    opening["value"] = False
    value["target_calendar_eligible"] = False
    closed = type(row).model_validate_json(json.dumps(value))
    closed_member = member.model_copy(
        update={"feature_row_sha256": canonical_sha256(closed.model_dump(mode="json"))}
    )
    result = labels.qualify_evidence(
        evidence, closed_member, closed, histories[row.history_context_sha256], FeaturePolicy(), 1
    )
    assert result.label.observed_sales_units == 7
    assert result.reasons == ("closed_target",)
    assert not result.eligible


@pytest.mark.parametrize("case", ["insufficient", "stale", "unknown_calendar"])
def test_history_and_calendar_reasons_match_existing_forecast_qualification(timeline, case):
    from test_forecast_manifests import plan

    source = deepcopy(timeline)
    if case == "insufficient":
        source["daily_demand_versions"] = [
            v
            for v in source["daily_demand_versions"]
            if v["business_date"] >= DAY - timedelta(days=5)
        ]
    elif case == "stale":
        source["daily_demand_versions"] = [
            v
            for v in source["daily_demand_versions"]
            if v["business_date"] < DAY - timedelta(days=4)
        ]
    else:
        source["business_calendar"] = [
            v for v in source["business_calendar"] if v["business_date"] != DAY + timedelta(days=1)
        ]
    view = OriginFeatures(source, make_origin(DAY))
    history = view.history(SERIES)
    row = view.targets(history)[0]
    member = partitions._membership(row, policy())
    evidence = OutcomeEvidence(
        key=ForecastKey.model_validate(row.model_dump(include=set(partitions.KEY_FIELDS))),
        candidates=(candidate(row),),
    )
    result = labels.qualify_evidence(evidence, member, row, history, FeaturePolicy(), 1)
    old = label_point(row, plan(), [{**candidate(row).model_dump(), "id": "controlled"}])
    expected = qualify(row, history, plan(), FeaturePolicy(), old)
    assert result.reasons == expected.reasons
    assert result.eligible == expected.eligible
    assert not result.eligible


def test_iterator_cannot_escape_the_audited_context(reader_fixture, tmp_path):
    args, kwargs = register(reader_fixture, tmp_path)
    with labels.open_forecast_outcomes(*args, **kwargs) as reader:
        iterator = reader.rows()
        next(iterator)
    with pytest.raises(SnapshotError, match="outside_audit_context"):
        next(iterator)
    with pytest.raises(SnapshotError, match="outside_audit_context"):
        reader.summary()
    assert state(kwargs["journal"])["completed_reads"] == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        reader._db.execute("SELECT body FROM qualified")


def test_failures_and_replays_exhaust_shared_budget_before_any_new_input_read(
    reader_fixture, tmp_path, monkeypatch
):
    args, kwargs = register(reader_fixture, tmp_path)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="consumer failure"):
            with labels.open_forecast_outcomes(*args, **kwargs) as reader:
                next(reader.rows())
                raise RuntimeError("consumer failure")
    original = (kwargs["journal"] / "journal.json").read_bytes()
    monkeypatch.setattr(
        labels, "_parent_seal", lambda *a: pytest.fail("input read before budget rejection")
    )
    with pytest.raises(SnapshotError, match="budget_exhausted"):
        with labels.open_forecast_outcomes(*args, **kwargs):
            pytest.fail("third access allowed")
    assert (kwargs["journal"] / "journal.json").read_bytes() == original
    assert state(kwargs["journal"])["failed_reads"] == 2


def test_independent_eval_is_denied_before_journal_or_dataset_io(
    reader_fixture, tmp_path, monkeypatch
):
    args, kwargs = register(reader_fixture, tmp_path, "development_evaluation")
    kwargs["binding"] = kwargs["binding"].model_copy(update={"purpose": "independent_evaluation"})
    monkeypatch.setattr(labels, "inspect", lambda *a: pytest.fail("journal opened"))
    monkeypatch.setattr(labels, "_parent_seal", lambda *a: pytest.fail("parent opened"))
    with pytest.raises(SnapshotError, match="requires_complete_access_audit"):
        with labels.open_forecast_outcomes(*args, **kwargs):
            pytest.fail("independent access granted")


@pytest.mark.parametrize(
    "mutation",
    ["evidence", "public_parent", "private_database", "private_replace", "evidence_inventory"],
)
def test_mutation_during_consumption_fails_and_keeps_reservation(
    reader_fixture, tmp_path, mutation
):
    args, kwargs = register(reader_fixture, tmp_path)
    with pytest.raises(SnapshotError):
        with labels.open_forecast_outcomes(*args, **kwargs) as reader:
            next(reader.rows())
            if mutation == "evidence":
                (args[2] / "outcomes.jsonl").write_bytes(b"{}\n")
            elif mutation == "public_parent":
                (args[0] / "new-file").write_bytes(b"changed")
            elif mutation == "evidence_inventory":
                (args[2] / "another-role.json").write_bytes(b"must not be read")
            elif mutation == "private_database":
                with sqlite3.connect(reader._path) as connection:
                    connection.execute("DELETE FROM qualified")
                reader.summary()
            else:
                before = reader._path.stat()
                raw = reader._path.read_bytes()
                reader._path.unlink()
                reader._path.write_bytes(raw)
                os.utime(reader._path, ns=(before.st_atime_ns, before.st_mtime_ns))
                reader.summary()
    assert state(kwargs["journal"])["failed_reads"] == 1


def test_sigkill_after_physical_read_remains_unresolved(reader_fixture, tmp_path):
    args, kwargs = register(reader_fixture, tmp_path)
    context = multiprocessing.get_context("fork")
    ready = context.Event()
    release = context.Event()

    def consume():
        with labels.open_forecast_outcomes(*args, **kwargs) as reader:
            next(reader.rows())
            ready.set()
            release.wait(20)

    process = context.Process(target=consume)
    process.start()
    try:
        assert ready.wait(15)
        os.kill(process.pid, signal.SIGKILL)
        process.join(10)
        assert process.exitcode == -signal.SIGKILL
    finally:
        if process.is_alive():
            process.terminate()
            process.join(10)
    assert state(kwargs["journal"])["unresolved_reads"] == 1
    assert state(kwargs["journal"])["remaining_read_budget"] == 1


def test_frozen_protocol_mismatch_is_rejected_without_outcome_read(
    reader_fixture, tmp_path, monkeypatch
):
    args, kwargs = register(reader_fixture, tmp_path)
    args = (
        *args[:3],
        args[3].model_copy(update={"policy": ForecastOutcomeReadPolicy(max_rows=50)}),
    )
    monkeypatch.setattr(labels, "_parent_seal", lambda *a: pytest.fail("opened input"))
    with pytest.raises(SnapshotError, match="unbound_read_protocol"):
        with labels.open_forecast_outcomes(*args, **kwargs):
            pytest.fail("protocol change accepted")
    assert state(kwargs["journal"])["reserved_reads"] == 0


def test_index_limit_fails_without_silently_truncating(reader_fixture, tmp_path):
    args, kwargs = register(
        reader_fixture, tmp_path, reader_policy=ForecastOutcomeReadPolicy(max_index_bytes=4096)
    )
    with pytest.raises((SnapshotError, sqlite3.Error)):
        with labels.open_forecast_outcomes(*args, **kwargs):
            pytest.fail("oversized index accepted")
    assert state(kwargs["journal"])["failed_reads"] == 1


def test_cli_and_four_v6_schemas_emit_diagnostics_without_targets(
    reader_fixture, tmp_path, monkeypatch, capsys
):
    args, kwargs = register(reader_fixture, tmp_path)
    protocol_file, binding_file = tmp_path / "protocol.json", tmp_path / "binding.json"
    protocol_file.write_bytes(canonical_bytes(args[3].model_dump(mode="json")))
    binding_file.write_bytes(canonical_bytes(kwargs["binding"].model_dump(mode="json")))
    command = [
        "labels",
        "--features",
        str(args[0]),
        "--partitions",
        str(args[1]),
        "--evidence",
        str(args[2]),
        "--protocol",
        str(protocol_file),
        "--expected-protocol-sha256",
        canonical_sha256(args[3].model_dump(mode="json")),
        "--binding",
        str(binding_file),
        "--journal",
        str(kwargs["journal"]),
        "--access-plan-sha256",
        kwargs["plan_sha256"],
    ]
    monkeypatch.setattr("sys.argv", command)
    assert label_cli.main() == 0
    output = capsys.readouterr().out
    assert json.loads(output)["counts"]["rows"] == 14
    assert "observed_sales_units" not in output
    with labels.open_forecast_outcomes(*args, **kwargs) as reader:
        qualified = next(reader.rows())
    values = {
        "forecast_outcome_read_protocol": args[3].model_dump(mode="json"),
        "outcome_evidence_manifest": args[3].evidence[0].model_dump(mode="json"),
        "outcome_evidence": json.loads((args[2] / "outcomes.jsonl").read_bytes().splitlines()[0]),
        "qualified_forecast_outcome": qualified.model_dump(mode="json"),
    }
    for name, value in values.items():
        schema = json.loads(
            (ROOT / ("contracts/evaluation/v6/" + name + ".schema.json")).read_text()
        )
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(value, schema)


@pytest.mark.parametrize("artifacts", [65], indirect=True)
def test_real_feature_parquet_and_history_with_physical_role_evidence(artifacts, journal, tmp_path):
    features, split, _, _ = artifacts
    shutil.rmtree(split)
    split.mkdir()
    (split / "labels.parquet").write_bytes(b"old split must never be opened")
    root = partitions.prepare_partitions(features, policy(), tmp_path / "partitions")
    manifest = partitions.verify_partitions(features, root)
    journal_root, journal_policy, _, _ = journal
    outcome_journal.initialize(journal_root, journal_policy)
    fixture = features, root, manifest, journal_root, (), {}
    args, kwargs = register(fixture, tmp_path, "calibration", "calibrator_fit")
    with labels.open_forecast_outcomes(*args, **kwargs) as reader:
        result = reader.summary()
        assert result["counts"]["rows"] == 14
        assert result["counts"]["label_eligible"] == 14
        assert result["evaluation_status"] == "not_ready"
    assert (split / "labels.parquet").read_bytes() == b"old split must never be opened"


@pytest.mark.parametrize("artifacts", [66], indirect=True)
@pytest.mark.parametrize("mutation", ["version_payload", "candidate_inventory"])
def test_shared_target_versions_cannot_differ_across_origins(
    artifacts, journal, tmp_path, mutation
):
    features, _, _, _ = artifacts
    plan = partitions.chronological_policy(
        DateWindow(start=DAY, end=DAY + timedelta(days=65)), train_days=2, other_role_days=1
    )
    root = partitions.prepare_partitions(features, plan, tmp_path / "partitions")
    manifest = partitions.verify_partitions(features, root)
    journal_root, journal_policy, _, _ = journal
    outcome_journal.initialize(journal_root, journal_policy)
    members = [
        PartitionMembership.model_validate_json(line)
        for line in (root / "memberships/train.jsonl").read_bytes().splitlines()
    ]
    records = [
        OutcomeEvidence(
            key=ForecastKey.model_validate(m.model_dump(include=set(partitions.KEY_FIELDS))),
            candidates=(candidate(m),),
        ).model_dump(mode="json")
        for m in members
    ]
    target = DAY + timedelta(days=3)
    same = [r for r in records if r["key"]["target_date"] == target.isoformat()]
    assert len(same) == 2
    if mutation == "version_payload":
        same[-1]["candidates"][0]["observed_units"] = 99
    else:
        replacement = dict(same[-1]["candidates"][0], version=2, observed_units=99)
        same[-1]["candidates"].append(replacement)
    fixture = features, root, manifest, journal_root, (), {}
    args, kwargs = register(fixture, tmp_path, records=records)
    with pytest.raises(SnapshotError, match="inconsistent_"):
        with labels.open_forecast_outcomes(*args, **kwargs):
            pytest.fail("conflicting shared target accepted")
    assert state(journal_root)["failed_reads"] == 1


@pytest.mark.parametrize("field", ["version", "observed_units"])
def test_evidence_rejects_quantities_and_versions_outside_physical_int64(reader_fixture, field):
    row = reader_fixture[4][0]
    with pytest.raises(ValidationError):
        candidate(row, **{field: 2**63})
