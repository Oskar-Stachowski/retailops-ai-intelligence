"""Quality knowledge never leaks from a final observation into earlier versions."""

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError
from test_forecast_features import tables as tables
from test_forecast_manifests import timeline as timeline
from test_forecast_source_replay import (
    physical_parents as physical_parents,
)
from test_forecast_source_replay import (
    register,
    state,
)
from test_forecast_source_replay import (
    replay_fixture as replay_fixture,
)
from test_outcome_access_journal import journal as journal
from test_tensorflow_challenger import development as development

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import outcome_journal, source_replay, source_versions
from retailops_ai.evaluation_campaign import source_versions_cli as cli
from retailops_ai.evaluation_campaign.source_version_contract import ForecastSourceVersion
from retailops_ai.source_snapshot.files import SnapshotError

ROOT = Path(__file__).resolve().parents[1]
DAY = datetime(2026, 7, 3, tzinfo=UTC)


def history():
    row = dict(
        id="version-1",
        observation_id="observation-1",
        business_date=DAY.date() - timedelta(days=1),
        product_id="product-1",
        selling_location_id="selling-1",
        channel="store",
        version=1,
        observed_units=3,
        observation_status="observed_positive",
        available_at=DAY,
        curated_available_at=DAY,
        source_record_sha256="1" * 64,
        history_policy_version="observed-quantity-history-1.0.0",
    )
    latest = row | dict(
        id="version-2",
        version=2,
        observed_units=7,
        available_at=DAY + timedelta(days=2),
        curated_available_at=DAY + timedelta(days=2),
        source_record_sha256="2" * 64,
    )
    observation = latest | dict(
        id="observation-1",
        curated_available_at=DAY + timedelta(days=4),
        source_data_complete=True,
        quality_status="valid",
        source_record_sha256="3" * 64,
    )
    return observation, [row, latest]


def test_quality_is_unknown_for_prior_versions_and_only_known_at_its_own_time():
    observation, versions = history()
    older, latest = list(source_versions._records(observation, versions))
    assert older.quality_basis == "historical_quality_not_established"
    assert older.quality_available_at is older.source_data_complete is older.quality_status is None
    assert older.at_cutoff(DAY + timedelta(days=100)).source_data_complete is False
    assert latest.candidate.curated_available_at == DAY + timedelta(days=2)
    assert latest.quality_available_at == DAY + timedelta(days=4)
    before = latest.at_cutoff(latest.quality_available_at - timedelta(microseconds=1))
    at = latest.at_cutoff(latest.quality_available_at)
    assert before.version == at.version == 2
    assert before.observed_units == at.observed_units == 7
    assert (
        before.curated_available_at
        == at.curated_available_at
        == versions[-1]["curated_available_at"]
    )
    assert before.source_data_complete is False and before.quality_status == "incomplete"
    assert at.source_data_complete is True and at.quality_status == "valid"


@pytest.mark.parametrize(
    "complete,quality",
    [(False, "valid"), (True, "incomplete"), (True, "quarantined"), (False, "incomplete")],
)
def test_explicit_incomplete_or_invalid_latest_never_becomes_complete(complete, quality):
    observation, versions = history()
    observation.update(source_data_complete=complete, quality_status=quality)
    latest = list(source_versions._records(observation, versions))[-1]
    candidate = latest.at_cutoff(DAY + timedelta(days=100))
    assert candidate.source_data_complete is complete
    assert candidate.quality_status == quality


@pytest.mark.parametrize("status,units", [("observed_zero", 0), ("closed", 0), ("missing", None)])
def test_zero_closed_and_missing_remain_distinct(status, units):
    observation, versions = history()
    versions[-1].update(observation_status=status, observed_units=units)
    observation.update(observation_status=status, observed_units=units)
    candidate = list(source_versions._records(observation, versions))[-1].at_cutoff(
        DAY + timedelta(days=100)
    )
    assert candidate.observation_status == status and candidate.observed_units == units


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_complete",
        "missing_quality",
        "integer_complete",
        "unknown_quality",
        "quality_time_missing",
        "observation_id",
        "product_id",
        "selling_location_id",
        "channel",
        "business_date",
        "history_policy",
        "gap",
        "duplicate_version",
        "boolean_version",
        "equal_time",
        "regression",
        "before_close",
        "curated_before_raw",
        "missing_curated_time",
        "latest_units",
        "latest_status",
        "latest_raw_time",
        "empty",
        "too_many",
    ],
)
def test_bad_history_is_rejected_without_defaults_or_partial_inventory(mutation):
    observation, versions = history()
    if mutation == "missing_complete":
        observation.pop("source_data_complete")
    elif mutation == "missing_quality":
        observation.pop("quality_status")
    elif mutation == "integer_complete":
        observation["source_data_complete"] = 1
    elif mutation == "unknown_quality":
        observation["quality_status"] = "unknown"
    elif mutation == "quality_time_missing":
        observation["curated_available_at"] = None
    elif mutation in {"observation_id", "product_id", "selling_location_id", "channel"}:
        versions[0][mutation] = "other"
    elif mutation == "business_date":
        versions[0]["business_date"] += timedelta(days=1)
    elif mutation == "history_policy":
        versions[0]["history_policy_version"] = "invented-policy"
    elif mutation in {"gap", "duplicate_version", "boolean_version"}:
        versions[-1]["version"] = {"gap": 3, "duplicate_version": 1, "boolean_version": True}[
            mutation
        ]
    elif mutation in {"equal_time", "regression"}:
        versions[-1]["available_at"] = DAY - timedelta(seconds=int(mutation == "regression"))
    elif mutation == "before_close":
        versions[0]["available_at"] = DAY - timedelta(seconds=1)
    elif mutation == "curated_before_raw":
        versions[0]["curated_available_at"] = DAY - timedelta(seconds=1)
    elif mutation == "missing_curated_time":
        versions[0]["curated_available_at"] = None
    elif mutation in {"latest_units", "latest_status", "latest_raw_time"}:
        field, value = {
            "latest_units": ("observed_units", 8),
            "latest_status": ("observation_status", "observed_zero"),
            "latest_raw_time": ("available_at", DAY + timedelta(days=3)),
        }[mutation]
        observation[field] = value
    elif mutation == "empty":
        versions.clear()
    else:
        versions *= 5
    with pytest.raises(SnapshotError, match="forecast_source_version_"):
        list(source_versions._records(observation, versions))


@pytest.mark.parametrize(
    "mutation",
    ["historical_proof", "missing_proof", "quality_before_quantity", "base_complete", "base_valid"],
)
def test_declared_contract_cannot_hide_unknown_quality_or_its_time(mutation):
    observation, versions = history()
    records = list(source_versions._records(observation, versions))
    value = records[0 if mutation == "historical_proof" else 1].model_dump(mode="json")
    if mutation == "historical_proof":
        value["source_data_complete"] = True
    elif mutation == "missing_proof":
        value["quality_available_at"] = None
    elif mutation == "quality_before_quantity":
        value["quality_available_at"] = DAY.isoformat()
    elif mutation == "base_complete":
        value["candidate"]["source_data_complete"] = True
    else:
        value["candidate"]["quality_status"] = "valid"
    with pytest.raises(ValidationError):
        ForecastSourceVersion.model_validate_json(json.dumps(value))


@pytest.mark.parametrize(
    "cutoff",
    [
        datetime(2026, 7, 5),
        datetime(2026, 7, 5, tzinfo=timezone(timedelta(hours=2))),
        DAY.date(),
        "2026-07-05T00:00:00Z",
    ],
)
def test_cutoff_must_be_explicit_utc_datetime(cutoff):
    observation, versions = history()
    record = list(source_versions._records(observation, versions))[-1]
    with pytest.raises(ValueError):
        record.at_cutoff(cutoff)


def open_reader(fixture, plan=None):
    snapshot, curated, protocol, root = fixture
    return source_versions.open_forecast_source_versions(
        snapshot, curated, protocol, journal=root, plan_sha256=plan or register(root, protocol)
    )


def test_actual_source_is_replayed_once_before_all_joined_rows_and_receipt(
    replay_fixture, monkeypatch
):
    root = replay_fixture[-1]
    calls = []
    for module, name in (
        (source_replay, "derive"),
        (source_replay, "verify_snapshot"),
        (source_replay, "verify_curated"),
        (source_versions, "_populate"),
    ):
        original = getattr(module, name)

        def observe(*args, _original=original, _name=name, **kwargs):
            assert state(root)["reserved_reads"] == 5 and state(root)["completed_reads"] == 0
            calls.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(module, name, observe)
    with open_reader(replay_fixture) as reader:
        private = reader._path
        assert private.stat().st_mode & 0o777 == 0o600
        with pytest.raises(SnapshotError, match="successful_audit_exit"):
            reader.receipt()
        rows = list(reader.rows())
        assert len(rows) == 1612
        assert all(r.quality_basis == "exact_latest_observation" for r in rows)
        assert all(r.at_cutoff(datetime(2100, 1, 1, tzinfo=UTC)).source_data_complete for r in rows)
        assert all(not r.at_cutoff(DAY - timedelta(days=100)).source_data_complete for r in rows)
        digest = hashlib.sha256(
            b"".join(canonical_bytes(r.model_dump(mode="json")) + b"\n" for r in rows)
        ).hexdigest()
    receipt = reader.receipt()
    assert not private.exists()
    assert state(root)["completed_reads"] == 5
    assert receipt.observation_rows == receipt.version_rows == 1612
    assert receipt.historical_versions_without_quality_proof == 0
    assert receipt.version_inventory_sha256 == digest
    assert receipt.feature_and_partition_rows_verified is False
    assert receipt.scoped_outcome_evidence_verified is False
    assert receipt.evaluation_status == "not_ready"
    assert calls == ["verify_snapshot", "verify_curated", "derive", "_populate"]
    for name, model in (
        ("forecast_source_version", rows[0]),
        ("forecast_source_version_receipt", receipt),
    ):
        schema = json.loads(
            (ROOT / "contracts/evaluation/v8" / (name + ".schema.json")).read_bytes()
        )
        jsonschema.Draft202012Validator(schema).validate(model.model_dump(mode="json"))


@pytest.mark.parametrize("started", [False, True])
def test_escaped_iterator_never_fetches_after_context_exit(replay_fixture, started):
    with open_reader(replay_fixture) as reader:
        iterator = reader.rows()
        if started:
            next(iterator)
    with pytest.raises(SnapshotError, match="outside_audit_context"):
        next(iterator)
    assert reader.receipt().version_rows == 1612


def test_caller_interrupt_charges_all_roles_and_never_publishes_receipt(replay_fixture):
    with pytest.raises(KeyboardInterrupt):
        with open_reader(replay_fixture) as reader:
            private = reader._path
            next(reader.rows())
            raise KeyboardInterrupt
    assert not private.exists()
    assert state(replay_fixture[-1])["failed_reads"] == 5
    with pytest.raises(SnapshotError, match="successful_audit_exit"):
        reader.receipt()


def test_private_index_mutation_is_detected_and_five_reads_remain_charged(replay_fixture):
    with pytest.raises(SnapshotError, match="private_index_changed"):
        with open_reader(replay_fixture) as reader:
            iterator = reader.rows()
            next(iterator)
            with reader._path.open("ab") as foreign:
                foreign.write(b"changed-private-index")
            next(iterator)
    assert state(replay_fixture[-1])["failed_reads"] == 5
    with pytest.raises(SnapshotError):
        reader.receipt()


def test_query_only_index_and_failed_journal_finish_never_publish_a_receipt(
    replay_fixture, monkeypatch
):
    finish = outcome_journal.finish

    def fail_first_completion(root, access_id, *, result, error_code=None):
        if result == "completed":
            raise OSError("controlled completion persistence failure")
        return finish(root, access_id, result=result, error_code=error_code)

    with pytest.raises(OSError, match="controlled completion"):
        with open_reader(replay_fixture) as reader:
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                reader._db.execute("UPDATE qualified SET sha='changed'")
            monkeypatch.setattr(outcome_journal, "finish", fail_first_completion)
    counts = state(replay_fixture[-1])
    assert counts["reserved_reads"] == 5
    assert counts["unresolved_reads"] == 1 and counts["failed_reads"] == 4
    assert counts["completed_reads"] == 0
    with pytest.raises(SnapshotError, match="successful_audit_exit"):
        reader.receipt()


def test_unbound_plan_never_opens_any_parent_or_creates_a_journal(replay_fixture, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("parent read before exact plan binding")

    monkeypatch.setattr(source_replay, "checked_directory", forbidden)
    before = state(replay_fixture[-1])
    with pytest.raises(SnapshotError, match="unbound_protocol"):
        with open_reader(replay_fixture, "a" * 64):
            pytest.fail("unbound reader yielded")
    assert state(replay_fixture[-1]) == before


def test_private_parent_changed_during_join_is_rejected_before_first_public_row(
    replay_fixture, monkeypatch
):
    populate = source_versions._populate

    def change_after_join(db, curated, manifest):
        result = populate(db, curated, manifest)
        with (curated / "curated_manifest.json").open("ab") as stream:
            stream.write(b" ")
        return result

    monkeypatch.setattr(source_versions, "_populate", change_after_join)
    with pytest.raises(SnapshotError, match="parent_changed"):
        with open_reader(replay_fixture):
            pytest.fail("changed private source must never yield a reader")
    assert state(replay_fixture[-1])["failed_reads"] == 5


def test_original_parent_change_at_exit_withholds_receipt(replay_fixture):
    path = replay_fixture[1] / "curated_manifest.json"
    before = path.read_bytes()
    try:
        with pytest.raises(SnapshotError, match="parent_changed"):
            with open_reader(replay_fixture) as reader:
                path.write_bytes(before + b" ")
        assert state(replay_fixture[-1])["failed_reads"] == 5
        with pytest.raises(SnapshotError, match="successful_audit_exit"):
            reader.receipt()
    finally:
        path.write_bytes(before)


def test_runtime_change_at_exit_withholds_receipt_and_charges_all_roles(
    replay_fixture, monkeypatch
):
    original = source_replay.runtime_pin
    with pytest.raises(SnapshotError, match="runtime_changed"):
        with open_reader(replay_fixture) as reader:
            monkeypatch.setattr(
                source_replay,
                "runtime_pin",
                lambda: original().model_copy(update={"code_sha256": "a" * 64}),
            )
    assert state(replay_fixture[-1])["failed_reads"] == 5
    with pytest.raises(SnapshotError, match="successful_audit_exit"):
        reader.receipt()


def test_cli_returns_completed_inventory_without_target_payload(
    replay_fixture, tmp_path, monkeypatch, capsys
):
    snapshot, curated, protocol, root = replay_fixture
    plan = register(root, protocol)
    path = tmp_path / "protocol.json"
    path.write_bytes(canonical_bytes(protocol.model_dump(mode="json")))
    monkeypatch.setattr(
        "sys.argv",
        [
            "source-versions",
            "--snapshot",
            str(snapshot),
            "--curated",
            str(curated),
            "--protocol",
            str(path),
            "--expected-protocol-sha256",
            canonical_sha256(protocol.model_dump(mode="json")),
            "--journal",
            str(root),
            "--access-plan-sha256",
            plan,
        ],
    )
    assert cli.main() == 0
    raw = capsys.readouterr().out
    receipt = json.loads(raw)
    assert receipt["version_rows"] == 1612
    assert "observed_units" not in raw and "candidate" not in raw
    assert receipt["evaluation_status"] == "not_ready"
    assert state(root)["completed_reads"] == 5
