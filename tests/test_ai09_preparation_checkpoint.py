"""Archive integrity and provenance controls; tiny synthetic witnesses are not native proof."""

import copy
import json
import os
import stat
from pathlib import Path

import pytest

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import preparation_checkpoint as checkpoint
from retailops_ai.evaluation_campaign import preparation_witness as native
from retailops_ai.forecasting.functional_v12_archive import seal_checkpoint


def identity():
    return {
        "scope": checkpoint.SCOPE,
        **{
            name: "a" * (40 if name.endswith("_commit") else 64)
            for name in checkpoint.IDENTITY_FIELDS - {"scope", "validators_sha256"}
        },
        "validators_sha256": {phase: "b" * 64 for phase in checkpoint.PHASES},
    }


def values(tmp_path, phase="generation", previous=None):
    directory = tmp_path / phase
    directory.mkdir()
    (directory / "data.csv").write_bytes(b"key,value\nknown,7\n")
    (directory / "report.json").write_bytes(b'{"controlled_test_only":true}\n')
    pin = identity()
    result = {checkpoint.RESULT_PATHS[phase]: str(directory), "controlled_test_only": True}
    measurement = {
        "phase": phase,
        "status": "passed",
        "reason": None,
        "exit_code": 0,
        "wall_seconds": 1.5,
        "sampled_worker_cpu_seconds": 1.0,
    }
    witness = native.capture(
        phase,
        directory,
        validator_code_sha256=pin["validators_sha256"][phase],
        identity_sha256=canonical_sha256(pin),
        output_id="controlled-" + phase,
        input_ids=(
            []
            if previous is None
            else ["controlled-generation", "controlled-" + previous]
            if phase == "export"
            else ["controlled-" + previous]
        ),
        report_paths=["report.json"],
    )
    return directory, pin, result, measurement, witness


def seal(tmp_path, phase="generation", previous=None, prior_id=None):
    directory, pin, result, measurement, witness = values(tmp_path, phase, previous)
    path, binding, cost = checkpoint.seal_stage(
        phase,
        directory,
        tmp_path / "checkpoints",
        identity=pin,
        result=result,
        measurement=measurement,
        witness=witness,
        previous_checkpoint_id=prior_id,
    )
    return path, binding, cost, directory, witness


def test_complete_five_phase_prefix_restores_bytes_parents_paths_and_cold_cost(tmp_path):
    archive_paths, original = [], []
    previous, prior_id = None, None
    for phase in checkpoint.PHASES:
        path, binding, cost, directory, witness = seal(tmp_path, phase, previous, prior_id)
        # Persist the independently retained expected binding as a controller would.
        saved = tmp_path / (phase + ".binding.json")
        saved.write_bytes(canonical_bytes(binding))
        archive_paths.append((path, json.loads(saved.read_bytes())))
        original.append((directory, witness))
        assert cost["wall_seconds"] > 0 and cost["process_cpu_seconds"] >= 0
        previous, prior_id = phase, path.name
    resumed = checkpoint.restore_chain(archive_paths, tmp_path / "restore", identity=identity())
    assert [r["phase"] for r in resumed] == list(checkpoint.PHASES)
    assert sum(r["cold_preparation_measurement"]["wall_seconds"] for r in resumed) == 7.5
    for (source, witness), receipt in zip(original, resumed, strict=True):
        target = Path(receipt["resumed_result"][checkpoint.RESULT_PATHS[receipt["phase"]]])
        assert native.inventory(target) == native.inventory(source) == witness["output_files"]
        assert receipt["original_result"][checkpoint.RESULT_PATHS[receipt["phase"]]] == str(source)
        assert receipt["source_regenerated"] is receipt["native_validation_repeated"] is False
        assert receipt["project_generation_receipt"] is receipt["final_test_authorized"] is False
        assert receipt["restore_measurement"]["wall_seconds"] > 0
        assert (target.parent / "evidence/witness.json").read_bytes() == canonical_bytes(
            witness
        ) + b"\n"
    with pytest.raises(FileExistsError):
        checkpoint.restore_chain(archive_paths, tmp_path / "restore", identity=identity())


@pytest.mark.parametrize(
    "field",
    [
        "plan_sha256",
        "generation_sha256",
        "consumer_commit",
        "producer_commit",
        "consumer_lock_sha256",
        "producer_lock_sha256",
        "exporter_lock_sha256",
        "consumer_runtime_sha256",
        "producer_runtime_sha256",
        "validators_sha256",
    ],
)
def test_changed_code_configuration_seed_dependencies_or_validator_cannot_reuse(tmp_path, field):
    path, binding, _, _, _ = seal(tmp_path)
    expected = identity()
    if field == "validators_sha256":
        expected[field]["generation"] = "c" * 64
    else:
        expected[field] = "c" * len(expected[field])
    with pytest.raises(ValueError, match="chain_changed_or_incomplete"):
        checkpoint.restore_chain([(path, binding)], tmp_path / "restore", identity=expected)
    assert not (tmp_path / "restore").exists()


@pytest.mark.parametrize(
    "change",
    [
        "failed",
        "missing_cost",
        "nan_cost",
        "bad_exit",
        "wrong_measured_phase",
        "false_proof",
        "integer_proof",
        "wrong_validator",
        "missing_native_reports",
        "changed_data",
        "extra_data",
    ],
)
def test_incomplete_or_changed_work_is_not_checkpointed(tmp_path, change):
    directory, pin, result, measurement, witness = values(tmp_path)
    if change == "failed":
        measurement["status"], measurement["reason"] = "failed", "wall_limit"
    elif change == "missing_cost":
        del measurement["wall_seconds"]
    elif change == "nan_cost":
        measurement["sampled_worker_cpu_seconds"] = float("nan")
    elif change == "bad_exit":
        measurement["exit_code"] = 7
    elif change == "wrong_measured_phase":
        measurement["phase"] = "curation"
    elif change == "false_proof":
        witness["native_validation_completed"] = False
    elif change == "integer_proof":
        witness["native_validation_completed"] = 1
    elif change == "wrong_validator":
        witness["native_validator"] = "hashes_only"
    elif change == "missing_native_reports":
        witness["validation_reports"] = {}
    elif change == "changed_data":
        (directory / "data.csv").write_bytes(b"changed after native validation")
    else:
        (directory / "extra.csv").write_bytes(b"unvalidated")
    with pytest.raises(ValueError):
        checkpoint.seal_stage(
            "generation",
            directory,
            tmp_path / "checkpoints",
            identity=pin,
            result=result,
            measurement=measurement,
            witness=witness,
            previous_checkpoint_id=None,
        )
    assert not (tmp_path / "checkpoints").exists()


def test_trusted_external_binding_is_required_even_for_internally_valid_archive(tmp_path):
    path, binding, _, _, _ = seal(tmp_path)
    altered = copy.deepcopy(binding)
    altered["lineage"]["measurement_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="trusted_binding_mismatch"):
        checkpoint.restore_stage(path, tmp_path / "restore", expected=altered)
    assert not (tmp_path / "restore").exists()


def test_archive_hash_alone_cannot_replace_native_evidence_and_invalid_restore_is_not_published(
    tmp_path,
):
    directory, pin, result, measurement, witness = values(tmp_path)
    witness["native_validation_completed"] = False
    evidence = tmp_path / "forged-evidence"
    evidence.mkdir()
    for name, value in (("result", result), ("measurement", measurement), ("witness", witness)):
        (evidence / (name + ".json")).write_bytes(canonical_bytes(value))
    lineage = {
        "version": checkpoint.VERSION,
        "phase": "generation",
        "identity": pin,
        "previous_checkpoint_id": None,
        "result_sha256": canonical_sha256(result),
        "measurement_sha256": canonical_sha256(measurement),
        "witness_sha256": canonical_sha256(witness),
    }
    path = seal_checkpoint(
        {"data": directory, "evidence": evidence}, tmp_path / "archives", lineage=lineage
    )
    with pytest.raises(ValueError, match="native_validation_binding"):
        checkpoint.restore_stage(
            path, tmp_path / "restore", expected={"checkpoint_id": path.name, "lineage": lineage}
        )
    assert not (tmp_path / "restore" / path.name).exists()
    assert list((tmp_path / "restore").iterdir()) == []


def test_missing_or_reordered_predecessor_is_rejected_before_restore(tmp_path):
    first, binding, _, _, _ = seal(tmp_path)
    second, second_binding, _, _, _ = seal(tmp_path, "qualification", "generation", first.name)
    for invalid in ([(second, second_binding)], [(second, second_binding), (first, binding)]):
        with pytest.raises(ValueError, match="chain_changed_or_incomplete"):
            checkpoint.restore_chain(invalid, tmp_path / "restore", identity=identity())
    assert not (tmp_path / "restore").exists()


def test_late_native_parent_mismatch_exposes_no_resumed_prefix(tmp_path):
    first, binding, _, _, _ = seal(tmp_path)
    second, second_binding, _, _, _ = seal(tmp_path, "qualification", "wrong-source", first.name)
    with pytest.raises(ValueError, match="native_parent_chain_mismatch"):
        checkpoint.restore_chain(
            [(first, binding), (second, second_binding)], tmp_path / "restore", identity=identity()
        )
    assert list((tmp_path / "restore").iterdir()) == []


def test_checkpoint_capture_rejects_links_and_does_not_archive_its_own_output(tmp_path):
    directory, pin, result, measurement, witness = values(tmp_path)
    (directory / "link").symlink_to(directory / "data.csv")
    with pytest.raises(ValueError, match="special_file"):
        native.inventory(directory)
    with pytest.raises(ValueError, match="archive_inside_source"):
        checkpoint.seal_stage(
            "generation",
            directory,
            directory / "archive",
            identity=pin,
            result=result,
            measurement=measurement,
            witness=witness,
            previous_checkpoint_id=None,
        )


def test_archive_and_restored_files_are_private_with_permissive_umask(tmp_path):
    previous_umask = os.umask(0)
    try:
        path, binding, _, _, _ = seal(tmp_path)
        restored, _ = checkpoint.restore_stage(path, tmp_path / "restore", expected=binding)
        for root in (path, restored):
            for entry in (root, *root.rglob("*")):
                assert stat.S_IMODE(entry.stat().st_mode) == (0o700 if entry.is_dir() else 0o600)
    finally:
        os.umask(previous_umask)


def test_export_must_bind_both_the_source_and_qualification_parents(tmp_path):
    first, binding, _, _, _ = seal(tmp_path)
    second, second_binding, _, _, _ = seal(tmp_path, "qualification", "generation", first.name)
    directory, pin, result, measurement, witness = values(tmp_path, "export", "qualification")
    witness["input_ids"][0] = "different-native-source"
    third, third_binding, _ = checkpoint.seal_stage(
        "export",
        directory,
        tmp_path / "checkpoints",
        identity=pin,
        result=result,
        measurement=measurement,
        witness=witness,
        previous_checkpoint_id=second.name,
    )
    with pytest.raises(ValueError, match="native_parent_chain_mismatch"):
        checkpoint.restore_chain(
            [(first, binding), (second, second_binding), (third, third_binding)],
            tmp_path / "restore",
            identity=pin,
        )
    assert list((tmp_path / "restore").iterdir()) == []
