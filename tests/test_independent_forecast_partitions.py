"""Chronology, exact populations, resealed corruption and no implicit outcome access."""

import hashlib
import json
import shutil
import stat
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest
from pydantic import ValidationError
from test_forecast_features import DAY, SERIES
from test_forecast_features import tables as tables
from test_forecast_manifests import artifacts as artifacts
from test_forecast_manifests import timeline as timeline

from retailops_ai.data_contracts.common import DateWindow, end_of_day
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import partition_cli, partitions
from retailops_ai.evaluation_campaign.partition_contract import (
    ALL_ROLES,
    ROLES,
    ForecastPartitionPolicy,
    PartitionMembership,
)
from retailops_ai.forecasting.contract import Parent, make_origin
from retailops_ai.forecasting.features import OriginFeatures
from retailops_ai.forecasting.features_contract import FEATURE_TYPES
from retailops_ai.forecasting.manifest_contract import (
    FeatureDescriptor,
    FeatureManifest,
    FeaturePolicy,
)
from retailops_ai.forecasting.manifest_io import code_pin
from retailops_ai.source_snapshot.files import SnapshotError

ROOT = Path(__file__).resolve().parents[1]


def policy():
    return partitions.chronological_policy(
        DateWindow(start=DAY, end=DAY + timedelta(days=64)),
        train_days=1,
        other_role_days=1,
    )


def test_five_role_boundaries_cover_horizon_delay_and_keep_purged_days():
    plan = policy()
    assert tuple(r.role for r in plan.roles) == ROLES
    for index, role in enumerate(plan.roles):
        day = DAY + timedelta(days=16 * index)
        assert role.origins == DateWindow(start=day, end=day)
        assert role.label_knowledge_cutoff == end_of_day(day + timedelta(days=15))
        assert plan.role_for(day) == role.role
        assert plan.role_for(day - timedelta(days=1)) == "purged"
        assert plan.role_for(day + timedelta(days=1)) == "purged"
    assert plan.cutoff_for("purged") is None
    assert plan.holdout_freshness == "not_asserted_requires_outcome_access_audit"


def test_complete_runtime_manifest_keeps_a_bounded_read_and_publication_limit(
    population, tmp_path, monkeypatch
):
    source, _, _ = population
    root = partitions.prepare_partitions(source, policy(), tmp_path / "valid")
    manifest = partitions.verify_partitions(source, root)
    assert manifest.descriptor.runtime == partitions.runtime_pin()
    raw = (root / "manifest.json").read_bytes()
    assert len(raw) <= partitions.MAX_MANIFEST_BYTES
    (root / "manifest.json").write_bytes(
        raw + b" " * (partitions.MAX_MANIFEST_BYTES - len(raw) + 1)
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("oversized metadata must fail before feature I/O")

    original = partitions.verify_feature_set
    monkeypatch.setattr(partitions, "verify_feature_set", forbidden)
    with pytest.raises(SnapshotError, match="metadata_size_limit"):
        partitions.verify_partitions(source, root)
    monkeypatch.setattr(partitions, "verify_feature_set", original)
    monkeypatch.setattr(partitions, "MAX_MANIFEST_BYTES", len(raw) - 1)
    rejected = tmp_path / "too-small"
    with pytest.raises(SnapshotError, match="forecast_partition_manifest_budget"):
        partitions.prepare_partitions(source, policy(), rejected)
    assert list(rejected.iterdir()) == []


@pytest.mark.parametrize(
    "mutation",
    [
        "overlap",
        "short_purge",
        "short_delay_purge",
        "early_cutoff",
        "future_cutoff",
        "reorder",
        "duplicate",
        "missing",
        "final_test",
        "evaluation_access",
        "promotion",
        "fit_all",
        "calibrate_on_tune",
        "assert_untouched",
        "raise_rows",
        "boolean_horizon",
    ],
)
def test_policy_rejects_leakage_and_unsupported_claims(mutation):
    value = policy().model_dump(mode="json")
    if mutation == "overlap":
        value["roles"][1]["origins"]["start"] = value["roles"][0]["origins"]["end"]
    elif mutation == "short_purge":
        value["purge_days"] = 14
    elif mutation == "short_delay_purge":
        value["label_delay_days"] = 2
    elif mutation == "early_cutoff":
        value["roles"][0]["label_knowledge_cutoff"] = end_of_day(
            DAY + timedelta(days=14)
        ).isoformat()
    elif mutation == "future_cutoff":
        value["roles"][0]["label_knowledge_cutoff"] = end_of_day(
            DAY + timedelta(days=16)
        ).isoformat()
    elif mutation == "reorder":
        value["roles"][1:3] = reversed(value["roles"][1:3])
    elif mutation == "duplicate":
        value["roles"][2]["role"] = "early_stopping"
    elif mutation == "missing":
        value["roles"].pop()
    else:
        field, changed = {
            "final_test": ("final_test_access_authorized", True),
            "evaluation_access": ("development_evaluation_access_authorized", True),
            "promotion": ("promotion_allowed", True),
            "fit_all": ("preprocessing", "fit_on_all_roles"),
            "calibrate_on_tune": ("calibration", "fit_on_tune"),
            "assert_untouched": ("holdout_freshness", "untouched"),
            "raise_rows": ("max_population_rows", 100001),
            "boolean_horizon": ("max_horizon_days", True),
        }[mutation]
        value[field] = changed
    with pytest.raises(ValidationError):
        ForecastPartitionPolicy.model_validate_json(json.dumps(value))


def test_old_sixty_day_smoke_cannot_be_relabelled_as_five_independent_roles():
    with pytest.raises(SnapshotError, match="origin_window_too_short"):
        partitions.chronological_policy(
            DateWindow(start=DAY, end=DAY + timedelta(days=59)),
            train_days=1,
            other_role_days=1,
        )
    # Recommended template has explicit 30/10-day roles, not five one-day samples.
    normal = partitions.chronological_policy(DateWindow(start=DAY, end=DAY + timedelta(days=129)))
    assert (normal.roles[0].origins.end - DAY).days + 1 == 30
    assert normal.roles[-1].origins.end == DAY + timedelta(days=129)
    with pytest.raises(SnapshotError, match="positive_role_lengths"):
        partitions.chronological_policy(
            DateWindow(start=DAY, end=DAY + timedelta(days=129)), train_days=True
        )


@pytest.mark.parametrize(
    ("role", "purpose"),
    [
        ("train", "preprocessing_fit"),
        ("train", "model_fit"),
        ("early_stopping", "early_stopping"),
        ("tune", "recipe_selection"),
        ("calibration", "calibrator_fit"),
    ],
)
def test_each_fit_operation_has_exactly_one_role(role, purpose):
    partitions.require_role_purpose(role, purpose)


@pytest.mark.parametrize(
    ("role", "purpose"),
    [
        ("tune", "preprocessing_fit"),
        ("calibration", "model_fit"),
        ("tune", "early_stopping"),
        ("early_stopping", "recipe_selection"),
        ("development_evaluation", "calibrator_fit"),
        ("purged", "model_fit"),
        ("development_evaluation", "independent_evaluation"),
    ],
)
def test_wrong_role_or_unapproved_evaluation_fails_before_any_input_read(role, purpose, tmp_path):
    with pytest.raises(SnapshotError):
        list(
            partitions.role_memberships(
                tmp_path / "absent", tmp_path / "absent", role=role, purpose=purpose
            )
        )


@pytest.fixture
def population(timeline, tmp_path, monkeypatch):
    """Typed sparse unit population; verification is mocked only in this unit fixture."""
    rows = tuple(
        row
        for offset in (0, 16, 32, 48, 64, 10)
        for row in OriginFeatures(timeline, make_origin(DAY + timedelta(days=offset))).targets(
            OriginFeatures(timeline, make_origin(DAY + timedelta(days=offset))).history(SERIES)
        )
    )
    feature_policy = FeaturePolicy()
    parent = Parent(
        source_dataset_id="source-sha256-" + "a" * 64,
        curated_dataset_id="curated-sha256-" + "b" * 64,
        snapshot_id="snapshot-sha256-" + "c" * 64,
        curated_descriptor_sha256="d" * 64,
        business_timezone="UTC",
        forecast_source_status="passed",
    )
    desc = FeatureDescriptor(
        parent=parent,
        calendar_id="forecast-calendar-sha256-" + "e" * 64,
        inputs_id="forecast-inputs-sha256-" + "f" * 64,
        source_parameters={"profile": "controlled-unit-population", "seed": 42},
        requested_policy=feature_policy,
        resolved_policy=feature_policy,
        feature_types={c: FEATURE_TYPES[c] for c in feature_policy.columns},
        code=code_pin(),
        input_code_sha256="1" * 64,
        content_sha256=canonical_sha256([r.model_dump(mode="json") for r in rows]),
        history_content_sha256="2" * 64,
        row_count=len(rows),
    )
    feature = FeatureManifest(
        feature_set_id="features-sha256-" + canonical_sha256(desc.model_dump(mode="json")),
        descriptor=desc,
        generated_at=end_of_day(DAY),
    )
    monkeypatch.setattr(
        partitions,
        "load_calendar",
        lambda *args: SimpleNamespace(
            descriptor=SimpleNamespace(
                origin_window=DateWindow(start=DAY, end=DAY + timedelta(days=64))
            )
        ),
    )
    monkeypatch.setattr(partitions, "verify_feature_set", lambda *args: feature)
    calls = []

    def inputs(root, name):
        calls.append(name)
        assert name == "features", (
            "partition preparation must not read a label/history table itself"
        )
        yield from rows

    monkeypatch.setattr(partitions, "input_models", inputs)
    source = tmp_path / "features"
    source.mkdir()
    return source, rows, calls


def test_full_keys_and_all_role_files_replay_without_labels_and_are_private(population, tmp_path):
    source, rows, calls = population
    output = tmp_path / "partitions"
    root = partitions.prepare_partitions(source, policy(), output)
    manifest = partitions.verify_partitions(source, root)
    assert manifest.descriptor.feature_descriptor.row_count == len(rows) == 75
    # The fixture's assortment ends before the final role's longest targets.
    # Preserve that actual partial population instead of inventing nine rows.
    assert {r: f.row_count for r, f in manifest.descriptor.populations.items()} == {
        "train": 14,
        "early_stopping": 14,
        "tune": 14,
        "calibration": 14,
        "development_evaluation": 5,
        "purged": 14,
    }
    assert set(calls) == {"features"}
    observed = []
    for role in ALL_ROLES:
        body = (root / "memberships" / (role + ".jsonl")).read_bytes()
        observed.extend(PartitionMembership.model_validate_json(line) for line in body.splitlines())
    assert {partitions.membership_key(r) for r in observed} == {
        partitions.membership_key(r) for r in rows
    }
    assert partitions.prepare_partitions(source, policy(), output) == root
    members = list(
        partitions.role_memberships(source, root, role="calibration", purpose="calibrator_fit")
    )
    assert len(members) == 14 and all(m.role == "calibration" for m in members)
    state = partitions.readiness(manifest)
    assert state["evaluation_status"] == "not_ready" and state["labels_accessed"] is False
    assert not manifest.development_evaluation_access_authorized
    for path in (root, root / "memberships", *root.rglob("*.json*")):
        assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0
    for name, model in (("policy", policy()), ("manifest", manifest), ("membership", observed[0])):
        schema = json.loads(
            (
                ROOT / "contracts/evaluation/v4" / ("forecast_partition_" + name + ".schema.json")
            ).read_text()
        )
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(model.model_dump(mode="json"), schema)


def reseal(root, role):
    """Recompute public file/manifest hashes: semantic verification must still reject corruption."""
    path = root / "memberships" / (role + ".jsonl")
    raw = path.read_bytes()
    value = json.loads((root / "manifest.json").read_text())
    value["descriptor"]["populations"][role].update(
        size_bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        keys_sha256=hashlib.sha256(
            b"".join(
                partitions.membership_key(PartitionMembership.model_validate_json(line)) + b"\n"
                for line in raw.splitlines()
            )
        ).hexdigest(),
    )
    value["partition_id"] = "ai09-partitions-sha256-" + canonical_sha256(value["descriptor"])
    (root / "manifest.json").write_bytes(canonical_bytes(value) + b"\n")


@pytest.mark.parametrize(
    "mutation", ["cutoff", "feature_hash", "unknown_key", "duplicate", "swap_role", "order"]
)
def test_resealed_corruption_cannot_change_membership(population, tmp_path, mutation):
    source, _, _ = population
    root = partitions.prepare_partitions(source, policy(), tmp_path / "partitions")
    file = root / "memberships/train.jsonl"
    rows = [json.loads(line) for line in file.read_bytes().splitlines()]
    if mutation == "cutoff":
        rows[0]["label_knowledge_cutoff"] = end_of_day(DAY + timedelta(days=16)).isoformat()
    elif mutation == "feature_hash":
        rows[0]["feature_row_sha256"] = "0" * 64
    elif mutation == "unknown_key":
        rows[0]["selling_location_id"] = "another-location"
    elif mutation == "duplicate":
        rows[1] = rows[0]
    elif mutation == "swap_role":
        rows[0]["role"] = "tune"
    else:
        rows.reverse()
    file.write_bytes(b"".join(canonical_bytes(row) + b"\n" for row in rows))
    reseal(root, "train")
    with pytest.raises(SnapshotError, match="membership_mismatch"):
        partitions.verify_partitions(source, root)


@pytest.mark.parametrize(
    "mutation", ["extra_labels", "symlink", "runtime", "noncanonical", "truncated"]
)
def test_inventory_runtime_and_physical_integrity_fail_closed(population, tmp_path, mutation):
    source, _, _ = population
    root = partitions.prepare_partitions(source, policy(), tmp_path / "partitions")
    if mutation == "extra_labels":
        (root / "labels.json").write_text("must never be parsed")
    elif mutation == "symlink":
        file = root / "memberships/train.jsonl"
        original = tmp_path / "original.jsonl"
        shutil.move(file, original)
        file.symlink_to(original)
    elif mutation == "runtime":
        value = json.loads((root / "manifest.json").read_text())
        value["descriptor"]["runtime"]["python_version"] = "3.11.99"
        value["partition_id"] = "ai09-partitions-sha256-" + canonical_sha256(value["descriptor"])
        (root / "manifest.json").write_bytes(canonical_bytes(value) + b"\n")
    elif mutation == "noncanonical":
        value = json.loads((root / "manifest.json").read_text())
        (root / "manifest.json").write_text(json.dumps(value, indent=2))
    else:
        file = root / "memberships/train.jsonl"
        file.write_bytes(file.read_bytes()[:-10])
    with pytest.raises((SnapshotError, ValidationError)):
        partitions.verify_partitions(source, root)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("max_population_rows", 74, "population_budget"),
        ("max_artifact_bytes", 1024, "population_budget"),
        ("max_index_bytes", 4096, "disk_index_budget"),
    ],
)
def test_budgets_reject_complete_population_instead_of_truncating(
    population, tmp_path, field, value, error
):
    source, _, _ = population
    plan = ForecastPartitionPolicy.model_validate_json(
        json.dumps({**policy().model_dump(mode="json"), field: value})
    )
    output = tmp_path / "unpublished"
    with pytest.raises(SnapshotError, match=error):
        partitions.prepare_partitions(source, plan, output)
    assert not output.exists()


def test_feature_mutation_before_publication_is_detected(population, tmp_path, monkeypatch):
    source, _, _ = population
    original = partitions.verify_feature_set(source)
    count = 0

    def verify(*args):
        nonlocal count
        count += 1
        return (
            original
            if count == 1
            else original.model_copy(update={"generated_at": end_of_day(DAY + timedelta(days=1))})
        )

    monkeypatch.setattr(partitions, "verify_feature_set", verify)
    output = tmp_path / "unpublished"
    with pytest.raises(SnapshotError, match="changed_during_prepare"):
        partitions.prepare_partitions(source, policy(), output)
    assert list(output.iterdir()) == []


def test_changed_role_file_is_detected_before_first_yield(population, tmp_path, monkeypatch):
    source, _, _ = population
    root = partitions.prepare_partitions(source, policy(), tmp_path / "partitions")
    verified = partitions.verify_partitions

    def change(*args):
        result = verified(*args)
        path = root / "memberships/train.jsonl"
        path.write_bytes(path.read_bytes() + b"extra")
        return result

    monkeypatch.setattr(partitions, "verify_partitions", change)
    with pytest.raises(SnapshotError, match="changed_during_role_read"):
        next(partitions.role_memberships(source, root, role="train", purpose="model_fit"))


def test_duplicate_source_key_fails_before_publication(population, tmp_path, monkeypatch):
    source, rows, _ = population
    monkeypatch.setattr(partitions, "input_models", lambda *args: iter((*rows, rows[0])))
    output = tmp_path / "unpublished"
    with pytest.raises(SnapshotError, match="duplicate_feature_key"):
        partitions.prepare_partitions(source, policy(), output)
    assert not output.exists()


def test_input_order_does_not_change_partition_identity(population, tmp_path, monkeypatch):
    source, rows, _ = population
    root = partitions.prepare_partitions(source, policy(), tmp_path / "first")
    monkeypatch.setattr(partitions, "input_models", lambda *args: iter(reversed(rows)))
    replay = partitions.prepare_partitions(source, policy(), tmp_path / "second")
    assert root.name == replay.name
    assert (root / "manifest.json").read_bytes() == (replay / "manifest.json").read_bytes()


def test_runtime_changes_during_preparation_block_publication(population, tmp_path, monkeypatch):
    source, _, _ = population
    original = partitions.runtime_pin()
    calls = 0

    def changed():
        nonlocal calls
        calls += 1
        return original if calls == 1 else original.model_copy(update={"python_version": "3.11.99"})

    monkeypatch.setattr(partitions, "runtime_pin", changed)
    output = tmp_path / "unpublished"
    with pytest.raises(SnapshotError, match="runtime_changed_during_prepare"):
        partitions.prepare_partitions(source, policy(), output)
    assert list(output.iterdir()) == []


def test_output_overlap_and_public_root_are_rejected(population, tmp_path):
    source, _, _ = population
    with pytest.raises(SnapshotError, match="overlaps_features"):
        partitions.prepare_partitions(source, policy(), source / "output")
    output = tmp_path / "public"
    output.mkdir(mode=0o755)
    with pytest.raises(SnapshotError, match="private_output_root"):
        partitions.prepare_partitions(source, policy(), output)
    assert list(output.iterdir()) == []
    alias = tmp_path / "alias"
    alias.symlink_to(output, target_is_directory=True)
    with pytest.raises(SnapshotError, match="unsafe_output_root"):
        partitions.prepare_partitions(source, policy(), alias / "new")
    assert not (output / "new").exists()


@pytest.mark.parametrize("artifacts", [65], indirect=True)
def test_real_feature_parquet_integration_and_cli_no_label_access(
    artifacts, tmp_path, monkeypatch, capsys
):
    """Public typed fixture, actual Parquet verifier and wheel-compatible artifact format."""
    source, split, _, _ = artifacts
    # Existing split is a controlled test fixture. Poison it to prove the new
    # preparation path does not verify or read its outcome files.
    shutil.rmtree(split)
    split.mkdir()
    (split / "labels.parquet").write_bytes(b"deliberately unreadable outcomes")
    root = partitions.prepare_partitions(source, policy(), tmp_path / "independent")
    manifest = partitions.verify_partitions(source, root)
    assert manifest.descriptor.feature_descriptor.row_count == 865
    assert all(manifest.descriptor.populations[r].row_count == 14 for r in ROLES[:-1])
    assert manifest.descriptor.populations["development_evaluation"].row_count == 5
    assert manifest.descriptor.populations["purged"].row_count == 804
    monkeypatch.setattr(
        "sys.argv",
        ["partitions", "preflight", "--features", str(source), "--partitions", str(root)],
    )
    assert partition_cli.main() == 3
    assert json.loads(capsys.readouterr().out)["evaluation_status"] == "not_ready"
    monkeypatch.setattr(
        "sys.argv",
        [
            "partitions",
            "template",
            "--start",
            DAY.isoformat(),
            "--end",
            (DAY + timedelta(days=59)).isoformat(),
            "--train-days",
            "1",
            "--other-role-days",
            "1",
        ],
    )
    assert partition_cli.main() == 2
    assert json.loads(capsys.readouterr().err)["error"] == "forecast_partition_rejected"
