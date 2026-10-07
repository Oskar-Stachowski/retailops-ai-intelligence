"""Actual typed raw/curated replay rejects self-consistently resealed source lies."""

import hashlib
import json
import shutil
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import jsonschema
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pydantic import ValidationError
from test_forecast_features import tables as tables
from test_forecast_manifests import timeline as timeline
from test_independent_forecast_partitions import policy
from test_outcome_access_journal import journal as journal
from test_tensorflow_challenger import development as development

from retailops_ai.curated.builder import build_curated, iter_rows, verify_curated
from retailops_ai.curated.contract import Digest, descriptor_id, schema_for
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import outcome_journal, partitions, source_replay
from retailops_ai.evaluation_campaign import source_replay_cli as cli
from retailops_ai.evaluation_campaign.labels import _parent_seal
from retailops_ai.evaluation_campaign.outcome_contract import OutcomeAccessPlan, OutcomePopulation
from retailops_ai.evaluation_campaign.source_replay_contract import (
    ForecastSourceReplayPolicy,
    ForecastSourceReplayProtocol,
    ForecastSourceReplayReceipt,
)
from retailops_ai.forecasting.contract import Parent
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, file_hash
from retailops_ai.source_snapshot.importer import import_snapshot

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def physical_parents(tmp_path_factory):
    """Public, previously exercised smoke facts; no new project target dataset.

    Fixture construction is not a claim that the whole test process is audited.
    The API must reserve before every actual parent read in each verification.
    """
    base = tmp_path_factory.mktemp("ai09-source-parent").resolve()
    imported = import_snapshot(
        ROOT / "data/fixtures/ai-smoke-v1/snapshot", base / "input/data/generated"
    )
    curated = build_curated(imported.directory, base / "output/data/generated")
    return imported.directory / "snapshot", curated.directory


def protocol_for(snapshot, curated, policy_value=None):
    metadata = json.loads((snapshot / "snapshot_manifest.json").read_bytes())
    document = json.loads((curated / "curated_manifest.json").read_bytes())
    parameters = metadata["source"]["descriptor"]["resolved_parameters"]
    parent = Parent(
        source_dataset_id=metadata["source_dataset_id"],
        snapshot_id=metadata["snapshot_id"],
        curated_dataset_id=document["curated_dataset_id"],
        curated_descriptor_sha256=canonical_sha256(document["descriptor"]),
        business_timezone="UTC",
        forecast_source_status="passed",
    )
    curated_sha = hashlib.sha256((curated / "curated_manifest.json").read_bytes()).hexdigest()
    populations = tuple(
        OutcomePopulation(
            data_seed=parameters["seed"],
            source_dataset_id=parent.source_dataset_id,
            snapshot_id=parent.snapshot_id,
            curated_dataset_id=parent.curated_dataset_id,
            feature_set_id="features-sha256-" + "3" * 64,
            partition_id="ai09-partitions-sha256-" + "4" * 64,
            role=r.role,
            origins=r.origins,
            label_knowledge_cutoff=r.label_knowledge_cutoff,
            membership_keys_sha256=canonical_sha256({"declared_role": r.role}),
            outcome_artifact_sha256=curated_sha,
        )
        for r in policy().roles
    )
    # These are metadata declarations, not physically qualified five-role keys.
    return ForecastSourceReplayProtocol(
        parent=parent,
        source_parameters=parameters,
        snapshot_manifest_sha256=hashlib.sha256(
            (snapshot / "snapshot_manifest.json").read_bytes()
        ).hexdigest(),
        curated_manifest_sha256=curated_sha,
        populations=populations,
        training_initialization_seed=137,
        replay_recipe_sha256=canonical_sha256({"recipe": "complete_source_replay_v1"}),
        policy=policy_value or ForecastSourceReplayPolicy(),
    )


def register(root, protocol, bindings=None):
    plan = OutcomeAccessPlan(
        protocol_sha256=canonical_sha256(protocol.model_dump(mode="json")),
        runtime=partitions.runtime_pin(),
        bindings=bindings if bindings is not None else source_replay.replay_bindings(protocol),
    )
    return outcome_journal.register_plan(root, plan)


@pytest.fixture
def replay_fixture(physical_parents, journal):
    root, journal_policy, _, _ = journal
    outcome_journal.initialize(
        root,
        journal_policy.model_copy(update={"maximum_new_reads": 64, "maximum_reads_per_binding": 4}),
    )
    snapshot, curated = physical_parents
    protocol = protocol_for(snapshot, curated)
    return snapshot, curated, protocol, root


def run(fixture, plan=None):
    snapshot, curated, protocol, root = fixture
    return source_replay.verify_forecast_source_parent(
        snapshot, curated, protocol, journal=root, plan_sha256=plan or register(root, protocol)
    )


def state(root):
    return outcome_journal.summary(outcome_journal.inspect(root))


def test_replay_producer_binding_comes_from_the_same_verified_private_snapshot(
    replay_fixture, monkeypatch
):
    """Previously exposed public fixture proves metadata origin, not fresh holdout."""
    snapshot, _, _, _ = replay_fixture
    metadata = json.loads((snapshot / "snapshot_manifest.json").read_bytes())
    provenance, exporter = metadata["source"]["provenance"], metadata["exporter"]
    original = source_replay._open_verified_source_parent
    observed = []
    from contextlib import contextmanager

    @contextmanager
    def capture(*args, **kwargs):
        with original(*args, **kwargs) as replay:
            observed.append(replay.producer_commit)
            assert replay.producer_commit == provenance.get("git_commit")
            assert replay.producer_code_state == provenance.get("code_state")
            assert replay.producer_lock_sha256 == provenance.get("dependency_sha256")
            assert replay.exporter_commit == exporter.get("git_commit")
            assert replay.exporter_lock_sha256 == exporter.get("dependency_sha256")
            assert replay.declared_exporter_lock_sha256 == provenance.get(
                "dependency_files", {}
            ).get("data/requirements-parquet.txt")
            yield replay

    monkeypatch.setattr(source_replay, "_open_verified_source_parent", capture)
    run(replay_fixture)
    assert len(observed) == 1
    assert state(replay_fixture[3])["completed_reads"] == 5


def seals(snapshot, curated):
    return _parent_seal(snapshot), _parent_seal(curated)


def test_real_full_source_and_curated_rows_replay_after_all_five_reservations(
    replay_fixture, monkeypatch
):
    snapshot, curated, protocol, root = replay_fixture
    before = seals(snapshot, curated)
    plan = register(root, protocol)
    calls = []
    for name in (
        "checked_directory",
        "_inspect_parents",
        "_seal_parent",
        "_copy_parent",
        "verify_snapshot",
        "verify_curated",
        "derive",
    ):
        original = getattr(source_replay, name)

        def observe(*args, _original=original, _name=name, **kwargs):
            assert state(root)["reserved_reads"] == 5
            assert state(root)["completed_reads"] == 0
            calls.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(source_replay, name, observe)
    receipt = run(replay_fixture, plan)
    assert receipt.source_rows == receipt.curated_rows == 31171
    assert receipt.source_tables == receipt.curated_tables
    assert len(receipt.access_ids) == 5
    assert (
        receipt.source_qualification
        == "typed_snapshot_and_complete_curated_transform_replay_passed"
    )
    assert receipt.scoped_outcome_evidence_verified is False
    assert receipt.feature_and_partition_rows_verified is False
    assert receipt.evaluation_status == "not_ready"
    assert (
        calls.count("derive")
        == calls.count("verify_snapshot")
        == calls.count("verify_curated")
        == 1
    )
    assert state(root)["completed_reads"] == 5
    monkeypatch.undo()
    assert seals(snapshot, curated) == before
    for name, model in (
        ("forecast_source_replay_protocol", protocol),
        ("forecast_source_replay_receipt", receipt),
    ):
        schema = json.loads(
            (ROOT / "contracts/evaluation/v7" / (name + ".schema.json")).read_bytes()
        )
        jsonschema.Draft202012Validator(schema).validate(model.model_dump(mode="json"))


@pytest.mark.parametrize(
    "mutation",
    [
        "drop_role",
        "reorder",
        "seed",
        "source",
        "snapshot",
        "curated",
        "feature",
        "partition",
        "artifact",
        "final",
        "independent",
        "promotion",
        "schema",
        "boolean_seed",
    ],
)
def test_protocol_cannot_hide_broad_exposure_or_grant_evaluation(replay_fixture, mutation):
    protocol = replay_fixture[2]
    value = protocol.model_dump(mode="json")
    if mutation == "drop_role":
        value["populations"].pop()
    elif mutation == "reorder":
        value["populations"] = list(reversed(value["populations"]))
    elif mutation == "boolean_seed":
        value["source_parameters"]["seed"] = True
    elif mutation in ("final", "independent", "promotion", "schema"):
        field, changed = {
            "final": ("final_test_access_authorized", True),
            "independent": ("independent_evaluation_access_authorized", True),
            "promotion": ("promotion_allowed", True),
            "schema": ("schema_version", "1.1.0"),
        }[mutation]
        value[field] = changed
    else:
        field, changed = {
            "seed": ("data_seed", 2026),
            "source": ("source_dataset_id", "source-sha256-" + "a" * 64),
            "snapshot": ("snapshot_id", "snapshot-sha256-" + "a" * 64),
            "curated": ("curated_dataset_id", "curated-sha256-" + "a" * 64),
            "feature": ("feature_set_id", "features-sha256-" + "a" * 64),
            "partition": ("partition_id", "ai09-partitions-sha256-" + "a" * 64),
            "artifact": ("outcome_artifact_sha256", "a" * 64),
        }[mutation]
        value["populations"][2][field] = changed
    with pytest.raises(ValidationError):
        ForecastSourceReplayProtocol.model_validate_json(json.dumps(value))


@pytest.mark.parametrize(
    "mutation", ["role_only", "fit_purpose", "recipe", "wrong_protocol", "reordered_plan"]
)
def test_unbound_plan_denied_before_any_parent_io(replay_fixture, monkeypatch, mutation):
    protocol, root = replay_fixture[2:]
    bindings = source_replay.replay_bindings(protocol)
    if mutation == "role_only":
        bindings = bindings[:1]
    elif mutation == "fit_purpose":
        bindings = (bindings[0].model_copy(update={"purpose": "model_fit"}), *bindings[1:])
    elif mutation == "recipe":
        bindings = (bindings[0].model_copy(update={"recipe_sha256": "a" * 64}), *bindings[1:])
    elif mutation == "reordered_plan":
        bindings = tuple(reversed(bindings))
    plan = register(root, protocol, bindings)
    if mutation == "wrong_protocol":
        value = protocol.model_dump(mode="json")
        value["replay_recipe_sha256"] = "b" * 64
        replay_fixture = (
            *replay_fixture[:2],
            ForecastSourceReplayProtocol.model_validate_json(json.dumps(value)),
            root,
        )
    monkeypatch.setattr(
        source_replay, "checked_directory", lambda *args: pytest.fail("unaudited parent I/O")
    )
    with pytest.raises(SnapshotError, match="unbound_protocol_or_exposure"):
        run(replay_fixture, plan)
    assert state(root)["reserved_reads"] == 0


def test_partial_reservation_exhaustion_is_charged_before_any_input_io(
    physical_parents, journal, monkeypatch
):
    snapshot, curated = physical_parents
    protocol = protocol_for(snapshot, curated)
    root, journal_policy, _, _ = journal
    # A two-read global budget cannot be stretched by a full-parent five-role operation.
    outcome_journal.initialize(root, journal_policy)
    plan = register(root, protocol)
    monkeypatch.setattr(
        source_replay,
        "checked_directory",
        lambda *args: pytest.fail("read after partial reservation"),
    )
    with pytest.raises(SnapshotError, match="read_budget_exhausted"):
        run((snapshot, curated, protocol, root), plan)
    assert state(root)["reserved_reads"] == state(root)["failed_reads"] == 2
    assert state(root)["completed_reads"] == 0


@pytest.mark.parametrize(
    "mutation", ["snapshot_bytes", "curated_bytes", "symlink", "extra_inventory"]
)
def test_physical_corruption_charges_all_roles_and_never_publishes_receipt(
    replay_fixture, tmp_path, mutation
):
    snapshot, curated, protocol, root = replay_fixture
    source, target = tmp_path / "snapshot", tmp_path / "curated"
    shutil.copytree(snapshot, source)
    shutil.copytree(curated, target)
    plan = register(root, protocol)
    if mutation == "snapshot_bytes":
        (source / "snapshot_manifest.json").write_bytes(b"{}\n")
    elif mutation == "curated_bytes":
        (target / "curated_manifest.json").write_bytes(b"{}\n")
    elif mutation == "symlink":
        ref = next(target.rglob("*.parquet"))
        ref.unlink()
        ref.symlink_to(next(curated.rglob("*.parquet")))
    else:
        (target / "not-in-manifest").write_bytes(b"extra")
    with pytest.raises(SnapshotError):
        run((source, target, protocol, root), plan)
    assert state(root)["failed_reads"] == state(root)["reserved_reads"] == 5


def reseal_curated(root, mutation):
    """All physical/logical checksums are valid; only raw transform replay can reject."""
    document = json.loads((root / "curated_manifest.json").read_bytes())
    name = "daily_demand_versions" if mutation != "non_target" else "products"
    table = next(t for t in document["tables"] if t["table"] == name)
    rows = list(iter_rows(root, table["files"], 256))
    row = next((r for r in rows if r.get("observed_units", 1)), rows[0])
    if mutation == "target":
        row["observed_units"] += 1
    elif mutation == "availability":
        row["curated_available_at"] += timedelta(days=1)
    elif mutation == "lineage":
        row["source_record_sha256"] = "a" * 64
    else:
        row["source_record_sha256"] = "b" * 64
    digest = Digest(root / "reseal.sqlite", table["schema"], table["grain"])
    try:
        for r in rows:
            digest.add(r)
        table.update(digest.summary())
    finally:
        digest.close()
        (root / "reseal.sqlite").unlink()
    for ref in table["files"]:
        (root / ref["path"]).unlink()
    path = table["files"][0]["path"]
    pq.write_table(pa.Table.from_pylist(rows, schema=schema_for(table["schema"])), root / path)
    size, sha = file_hash(root, path)
    table["files"] = [{"path": path, "bytes": size, "sha256": sha, "row_count": len(rows)}]
    document["descriptor"]["tables"] = [
        {k: v for k, v in t.items() if k != "files"} for t in document["tables"]
    ]
    document["curated_dataset_id"] = descriptor_id(document["descriptor"])
    raw = canonical_json(document) + b"\n"
    (root / "curated_manifest.json").write_bytes(raw)
    (root / "manifest.sha256").write_bytes((hashlib.sha256(raw).hexdigest() + "\n").encode())
    return verify_curated(root)


@pytest.mark.parametrize("mutation", ["target", "lineage", "availability", "non_target"])
def test_resealed_typed_curated_forgery_is_rejected_by_full_source_replay(
    replay_fixture, tmp_path, mutation
):
    snapshot, curated, _, root = replay_fixture
    target = tmp_path / "curated"
    shutil.copytree(curated, target)
    forged = reseal_curated(target, mutation)
    assert forged["readiness"]["forecast_source"] == "passed"
    protocol = protocol_for(snapshot, target)
    with pytest.raises(SnapshotError, match="complete_logical_mismatch"):
        run((snapshot, target, protocol, root))
    assert state(root)["failed_reads"] == 5


@pytest.mark.parametrize("mutation", ["original", "private", "runtime", "interrupt"])
def test_mutation_or_interruption_during_replay_is_failed_and_private_files_removed(
    replay_fixture, tmp_path, monkeypatch, mutation
):
    snapshot, curated, protocol, root = replay_fixture
    target = tmp_path / "curated"
    shutil.copytree(curated, target)
    original = source_replay.derive
    private_paths = []

    def disturb(source_root, source, payload, scratch, *args):
        private_paths.append(source_root.parent)
        if mutation == "interrupt":
            raise KeyboardInterrupt
        result = original(source_root, source, payload, scratch, *args)
        if mutation == "original":
            (target / "new-file").write_bytes(b"changed")
        elif mutation == "private":
            (source_root / "new-file").write_bytes(b"changed")
        else:
            monkeypatch.setattr(source_replay, "runtime_pin", lambda: None)
        return result

    monkeypatch.setattr(source_replay, "derive", disturb)
    with pytest.raises(KeyboardInterrupt if mutation == "interrupt" else SnapshotError):
        run((snapshot, target, protocol, root))
    assert private_paths and all(not p.exists() for p in private_paths)
    assert state(root)["failed_reads"] == 5


def test_whole_parent_row_limit_rejects_without_truncation(replay_fixture):
    snapshot, curated, _, root = replay_fixture
    protocol = protocol_for(
        snapshot, curated, ForecastSourceReplayPolicy(max_rows_per_parent=31170)
    )
    with pytest.raises(SnapshotError):
        run((snapshot, curated, protocol, root))
    assert state(root)["failed_reads"] == 5


@pytest.mark.parametrize(
    "mutation",
    [
        "snapshot_truth",
        "curated_truth",
        "extra_snapshot",
        "extra_curated",
        "extra_after_inspection",
    ],
)
def test_truth_and_unlisted_files_rejected_before_any_fact_hash_or_copy(
    replay_fixture, tmp_path, monkeypatch, mutation
):
    snapshot, curated, protocol, root = replay_fixture
    source, target = tmp_path / "snapshot", tmp_path / "curated"
    shutil.copytree(snapshot, source)
    shutil.copytree(curated, target)
    if mutation in ("snapshot_truth", "curated_truth"):
        path = (
            source / "snapshot_manifest.json"
            if mutation == "snapshot_truth"
            else target / "curated_manifest.json"
        )
        value = json.loads(path.read_bytes())
        if mutation == "snapshot_truth":
            value["descriptor"]["include_evaluation_truth"] = True
            value["snapshot_id"] = "snapshot-sha256-" + canonical_sha256(value["descriptor"])
        else:
            value["evaluation_truth"]["included"] = True
        raw = canonical_json(value) + b"\n"
        path.write_bytes(raw)
        (path.parent / "manifest.sha256").write_bytes(
            (hashlib.sha256(raw).hexdigest() + "\n").encode()
        )
        protocol = protocol_for(source, target)
    elif mutation == "extra_after_inspection":
        original = source_replay._inspect_parents

        def add_extra(*args):
            result = original(*args)
            (target / "forbidden-extra").write_bytes(b"must never be hashed")
            return result

        monkeypatch.setattr(source_replay, "_inspect_parents", add_extra)
    else:
        (source if mutation == "extra_snapshot" else target).joinpath(
            "forbidden-extra"
        ).write_bytes(b"must never be hashed")
    plan = register(root, protocol)
    if mutation == "extra_after_inspection":
        original_hash = source_replay._bounded_hash

        def bound_hash(root, name, maximum):
            assert name != "forbidden-extra"
            return original_hash(root, name, maximum)

        monkeypatch.setattr(source_replay, "_bounded_hash", bound_hash)
    else:
        monkeypatch.setattr(
            source_replay,
            "_bounded_hash",
            lambda *args: pytest.fail("fact hashed before truth/inventory rejection"),
        )
    monkeypatch.setattr(
        source_replay, "_copy_parent", lambda *args: pytest.fail("copy before metadata rejection")
    )
    with pytest.raises(SnapshotError):
        run((source, target, protocol, root), plan)
    assert state(root)["failed_reads"] == 5


@pytest.mark.parametrize("mutation", ["descriptor", "parameters", "transform"])
def test_declared_parent_and_transform_must_match_actual_current_identity(
    replay_fixture, tmp_path, mutation
):
    snapshot, curated, protocol, root = replay_fixture
    value = protocol.model_dump(mode="json")
    if mutation == "descriptor":
        value["parent"]["curated_descriptor_sha256"] = "a" * 64
    elif mutation == "parameters":
        value["source_parameters"]["undeclared_parameter"] = "not_in_actual_source"
    else:
        target = tmp_path / "curated"
        shutil.copytree(curated, target)
        document = json.loads((target / "curated_manifest.json").read_bytes())
        document["descriptor"]["transform"]["code_files"]["curated/builder.py"] = "a" * 64
        document["descriptor"]["transform"]["code_sha256"] = canonical_sha256(
            document["descriptor"]["transform"]["code_files"]
        )
        document["curated_dataset_id"] = descriptor_id(document["descriptor"])
        raw = canonical_json(document) + b"\n"
        (target / "curated_manifest.json").write_bytes(raw)
        (target / "manifest.sha256").write_bytes((hashlib.sha256(raw).hexdigest() + "\n").encode())
        verify_curated(target)
        curated = target
        value = protocol_for(snapshot, target).model_dump(mode="json")
    protocol = ForecastSourceReplayProtocol.model_validate_json(json.dumps(value))
    with pytest.raises(SnapshotError, match="parent_identity_mismatch|transform_runtime_mismatch"):
        run((snapshot, curated, protocol, root))
    assert state(root)["failed_reads"] == 5


def test_cli_full_replay_emits_only_receipt_and_hashes(
    replay_fixture, tmp_path, monkeypatch, capsys
):
    snapshot, curated, protocol, root = replay_fixture
    plan = register(root, protocol)
    file = tmp_path / "protocol.json"
    file.write_bytes(canonical_bytes(protocol.model_dump(mode="json")) + b"\n")
    monkeypatch.setattr(
        "sys.argv",
        [
            "source-replay",
            "--snapshot",
            str(snapshot),
            "--curated",
            str(curated),
            "--protocol",
            str(file),
            "--expected-protocol-sha256",
            canonical_sha256(protocol.model_dump(mode="json")),
            "--journal",
            str(root),
            "--access-plan-sha256",
            plan,
        ],
    )
    assert cli.main() == 0
    receipt = ForecastSourceReplayReceipt.model_validate_json(capsys.readouterr().out)
    assert receipt.curated_rows == 31171 and receipt.evaluation_status == "not_ready"
    assert "observed_units" not in receipt.model_dump()
    assert state(root)["completed_reads"] == 5


def test_receipt_cannot_promote_scoped_labels_or_claim_independence():
    # Contract itself prevents a detached receipt from silently growing authority.
    schema = ForecastSourceReplayReceipt.model_json_schema()
    for field in (
        "scoped_outcome_evidence_verified",
        "feature_and_partition_rows_verified",
        "independent_evaluation_access_authorized",
        "final_test_access_authorized",
        "promotion_allowed",
    ):
        assert schema["properties"][field]["const"] is False


def test_logical_comparison_keeps_every_nonphysical_field(physical_parents):
    document = json.loads((physical_parents[1] / "curated_manifest.json").read_bytes())
    changed = deepcopy(document)
    changed["tables"][0]["files"] = []
    assert source_replay.logical_document(changed) == source_replay.logical_document(document)
    changed["time_semantics"]["as_of"] = "wrong"
    assert source_replay.logical_document(changed) != source_replay.logical_document(document)


@pytest.mark.parametrize("current_wire", [False, True])
@pytest.mark.parametrize("mismatch", ["bytes", "runtime"])
def test_common_core_rejects_undeclared_limits_or_runtime_before_parent_io(
    replay_fixture, monkeypatch, current_wire, mismatch
):
    from dataclasses import replace

    from retailops_ai.evaluation_campaign.physical_contract import PhysicalSourceSpec

    snapshot, curated, old, _ = replay_fixture
    specification = (
        PhysicalSourceSpec(
            schema_version=old.schema_version,
            parent=old.parent,
            source_parameters=old.source_parameters,
            snapshot_manifest_sha256=old.snapshot_manifest_sha256,
            curated_manifest_sha256=old.curated_manifest_sha256,
        )
        if current_wire
        else old
    )
    limits = source_replay.physical_limits(specification)
    runtime = partitions.runtime_pin()
    if mismatch == "bytes":
        limits = replace(limits, max_bytes=limits.max_bytes + 1)
    else:
        runtime = runtime.model_copy(update={"python_version": "0.0.0"})

    def forbidden(*args, **kwargs):
        pytest.fail("parent I/O happened before declaration guard")

    monkeypatch.setattr(source_replay, "checked_directory", forbidden)
    monkeypatch.setattr(source_replay, "_inspect_parents", forbidden)
    with pytest.raises(Exception, match="undeclared_limits|execution_runtime_changed"):
        with source_replay._open_verified_source_parent(
            snapshot, curated, specification, limits=limits, runtime=runtime
        ):
            pytest.fail("mismatched context entered")


def test_borrowed_curated_metadata_cannot_change_the_verified_parent_inventory(replay_fixture):
    snapshot, curated, protocol, root = replay_fixture
    plan = register(root, protocol)
    with pytest.raises(SnapshotError, match="mutable_metadata_changed"):
        with source_replay._open_replayed_source_parent(
            snapshot, curated, protocol, journal=root, plan_sha256=plan
        ) as (_, manifest, _, _):
            manifest["tables"].pop()
    audit = state(root)
    assert audit["reserved_reads"] == audit["failed_reads"] == 5
    assert audit["completed_reads"] == 0
