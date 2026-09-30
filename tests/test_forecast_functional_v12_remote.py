"""Remote activation is fail-closed and bounded; tests never invoke a source generator."""

import json
import subprocess
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts import prepare_forecast_functional_v12_remote as remote


@pytest.fixture
def plan_files(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_NUMBER", "1")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    root = tmp_path / "control"
    root.mkdir()
    body = {
        "version": "forecast-functional-cohort-plan-1.0.0",
        "holdout_metrics_evaluated_before_freeze": False,
        "seeds": [720001, 720002],
        "previously_used_seeds": [42, 710001],
        "method_policy": {"version": "fixture"},
        "split_policy": {"version": "fixture"},
        "origin_window": {"start": "2026-01-01", "end": "2026-02-01"},
        "source_configuration": {
            "generation": {
                "profile": "ai-intermittent-v1",
                "days": 232,
                "products": 100,
                "stores": 2,
                "warehouses": 2,
                "end_date": "2026-09-30",
            },
            "source_schema_version": "2.7.0",
            "snapshot_schema_version": "1.1.0",
            "resolved_parameters": {"fixture": "resolved"},
            "context": {"fixture": "context"},
            "inventory_configuration_sha256": {
                str(seed): remote.sha(remote.canonical({"fulfillment": {"seed": seed}}))
                for seed in (720001, 720002)
            },
        },
        "remote_preparation": {
            "enabled": True,
            "ai_commit": "a" * 40,
            "source_commit": "b" * 40,
            "max_parallel": 4,
            "github_run_number": 1,
            "max_checkpoint_bytes": remote.MAX_CHECKPOINT_BYTES,
            "min_free_bytes": remote.MIN_FREE_BYTES,
            "ai_code_files": {remote.RUNNER_PATH: "c" * 64, remote.WORKFLOW_PATH: "d" * 64},
            "source_code_files": {remote.SOURCE_MODULE_PATH: "e" * 64},
            "dependency_files": {
                "ai": {"uv.lock": "f" * 64, "pyproject.toml": "f" * 64},
                "source": {
                    "services/api/requirements.txt": "f" * 64,
                    "services/api/requirements-dev.txt": "f" * 64,
                    "data/requirements-parquet.txt": "f" * 64,
                },
            },
            "source_implementation": {"version": "fixture"},
            "source_provenance": {
                "code_files": {"fixture.py": "e" * 64},
                "code_sha256": "e" * 64,
                "dependency_files": {"requirements.txt": "f" * 64},
                "dependency_sha256": "f" * 64,
                "python_version": "3.11.15",
                "git_commit": "b" * 40,
                "code_state": "clean",
            },
            "ai_environment": {"python_version": "3.11.15", "packages": {"pyarrow": "25.0.1"}},
            "source_environment": {"python_version": "3.11.15", "packages": {"pyarrow": "25.0.1"}},
        },
    }
    execution = {
        "version": "forecast-remote-preparation-execution-1.0.0",
        "authorized": True,
        "phase": "cohort_preparation_only",
        "freeze_path": "contracts/forecast/v2/test.freeze.json",
        "ai_commit": "a" * 40,
        "source_commit": "b" * 40,
        "max_parallel": 4,
        "github_run_number": 1,
    }

    def write(modified=None, activation=None):
        descriptor = body if modified is None else modified
        freeze = {
            "freeze_id": "functional-v12-freeze-sha256-" + remote.sha(remote.canonical(descriptor)),
            "descriptor": descriptor,
        }
        freeze_path = root / execution["freeze_path"]
        freeze_path.parent.mkdir(parents=True, exist_ok=True)
        freeze_path.write_bytes(remote.canonical(freeze) + b"\n")
        instructions = dict(execution, freeze_sha256=remote.sha(freeze_path.read_bytes()))
        if activation is not None:
            instructions.update(activation)
        (root / remote.EXECUTION_PATH).write_bytes(remote.canonical(instructions) + b"\n")
        return remote.sha((root / remote.EXECUTION_PATH).read_bytes())

    expected = write()
    return root, body, write, expected


def test_absent_activation_exact_raw_hash_and_preregistered_seed_fail_before_generation(
    plan_files,
    tmp_path,
    monkeypatch,
):
    root, _, _, expected = plan_files
    with pytest.raises(remote.PreparationError, match="missing_or_oversized"):
        remote.read_plan(tmp_path / "missing")
    plan = remote.read_plan(root, expected_execution_sha256=expected)
    assert plan["freeze"]["descriptor"]["seeds"] == [720001, 720002]
    with pytest.raises(remote.PreparationError, match="execution_sha256_mismatch"):
        remote.read_plan(root, expected_execution_sha256="0" * 64)
    calls = []
    monkeypatch.setattr(remote, "verify_checkout", lambda *args: calls.append(args))
    with pytest.raises(remote.PreparationError, match="seed_not_planned"):
        remote.preflight(root, tmp_path, tmp_path, 42)
    assert calls == []
    freeze_path = root / plan["execution"]["freeze_path"]
    freeze_path.write_bytes(freeze_path.read_bytes() + b" ")
    with pytest.raises(remote.PreparationError, match="freeze_mismatch"):
        remote.preflight(root, tmp_path, tmp_path, 720001)
    assert calls == []


@pytest.mark.parametrize(
    "mutation",
    [
        "unauthorized",
        "moving_ref",
        "scoring",
        "large_archive",
        "parallel",
        "old_seed",
        "duplicate_seed",
        "oversized_seeds",
        "source_dirty",
        "source_commit",
        "source_fingerprint_only",
    ],
)
def test_activation_and_resource_contract_cannot_silently_expand(plan_files, mutation):
    root, body, write, _ = plan_files
    body = deepcopy(body)
    activation = {}
    if mutation == "unauthorized":
        activation["authorized"] = False
    elif mutation == "moving_ref":
        activation["source_commit"] = "main"
    elif mutation == "scoring":
        activation["phase"] = "score_holdout"
    elif mutation == "large_archive":
        body["remote_preparation"]["max_checkpoint_bytes"] += 1
    elif mutation == "parallel":
        activation["max_parallel"] = 64
    elif mutation == "old_seed":
        body["seeds"] = [42]
    elif mutation == "duplicate_seed":
        body["seeds"] = [720001, 720001]
    elif mutation == "oversized_seeds":
        body["seeds"] = list(range(720001, 720066))
    elif mutation == "source_dirty":
        body["remote_preparation"]["source_provenance"]["code_state"] = "dirty"
    elif mutation == "source_commit":
        body["remote_preparation"]["source_provenance"]["git_commit"] = "c" * 40
    else:
        del body["remote_preparation"]["source_provenance"]["git_commit"]
    write(body, activation)
    with pytest.raises(remote.PreparationError):
        remote.read_plan(root)


def test_reruns_and_mutated_pinned_code_stop_before_generator(plan_files, tmp_path, monkeypatch):
    root, _, _, _ = plan_files
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    with pytest.raises(remote.PreparationError, match="rerun_forbidden"):
        remote.preflight(root, tmp_path, tmp_path, 720001)
    monkeypatch.delenv("GITHUB_RUN_ATTEMPT")
    code = tmp_path / "runner.py"
    code.write_text("original code")
    pins = {"runner.py": remote.sha(code.read_bytes())}
    remote._pins(tmp_path, pins)
    code.write_text("changed code")
    with pytest.raises(remote.PreparationError, match="code_or_dependency_drift"):
        remote._pins(tmp_path, pins)
    monkeypatch.setattr(remote, "_git", lambda *args: "b" * 40)
    with pytest.raises(remote.PreparationError, match="checkout_commit_or_tracked_drift"):
        remote.verify_checkout(tmp_path, "a" * 40, pins, pins)


@pytest.mark.parametrize("parallel", [4, 8, 16])
def test_reviewed_standard_runner_parallelism_requires_exact_freeze_binding(plan_files, parallel):
    root, body, write, _ = plan_files
    body = deepcopy(body)
    body["remote_preparation"]["max_parallel"] = parallel
    write(body, {"max_parallel": parallel})
    assert remote.read_plan(root)["execution"]["max_parallel"] == parallel
    write(body, {"max_parallel": 8 if parallel != 8 else 4})
    with pytest.raises(remote.PreparationError, match="commit_binding"):
        remote.read_plan(root)


def test_remote_backstop_matches_versioned_resource_policy_but_selected_cap_can_be_lower(
    plan_files,
):
    from retailops_ai.forecasting.functional_v12_resources import MAX_CHECKPOINT_BYTES, VERSION

    root, body, write, _ = plan_files
    assert VERSION == "forecast-functional-resource-plan-1.1.0"
    assert remote.MAX_CHECKPOINT_BYTES == MAX_CHECKPOINT_BYTES == 768 * 1024**2
    body = deepcopy(body)
    body["remote_preparation"]["max_checkpoint_bytes"] = 512 * 1024**2
    write(body)
    selected = remote.read_plan(root)["freeze"]["descriptor"]["remote_preparation"]
    assert selected["max_checkpoint_bytes"] == 512 * 1024**2


@pytest.mark.parametrize("actual", [None, "2", "01", "not-a-number"])
def test_new_or_unknown_run_blocks_plan_and_preflight_before_code_or_generator(
    plan_files, tmp_path, monkeypatch, actual
):
    root, _, _, _ = plan_files
    if actual is None:
        monkeypatch.delenv("GITHUB_RUN_NUMBER")
    else:
        monkeypatch.setenv("GITHUB_RUN_NUMBER", actual)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")

    def forbidden(*_args, **_kwargs):
        pytest.fail("No checkout, source import or generation allowed in an unreserved run")

    monkeypatch.setattr(remote, "_pins", forbidden)
    monkeypatch.setattr(remote, "verify_checkout", forbidden)
    monkeypatch.setattr(remote.importlib, "import_module", forbidden)
    monkeypatch.setattr(remote.sys, "argv", ["runner", "plan", "--control-root", str(root)])
    with pytest.raises(remote.PreparationError, match="run_number_not_reserved"):
        remote.main()
    with pytest.raises(remote.PreparationError, match="run_number_not_reserved"):
        remote.preflight(root, tmp_path, tmp_path, 720001)


@pytest.mark.parametrize("number", [None, True, 0, -1, 1.0, "1"])
def test_execution_run_number_is_required_positive_integer(plan_files, number):
    root, _, write, _ = plan_files
    write(activation={"github_run_number": number})
    with pytest.raises(remote.PreparationError, match="not_authorized_or_not_immutable"):
        remote.read_plan(root)


def test_freeze_run_number_must_match_execution_and_environment(plan_files, tmp_path, monkeypatch):
    root, body, write, _ = plan_files
    body = deepcopy(body)
    body["remote_preparation"]["github_run_number"] = 7
    write(body)
    with pytest.raises(remote.PreparationError, match="commit_binding"):
        remote.read_plan(root)
    write(body, {"github_run_number": 7})
    monkeypatch.setenv("GITHUB_RUN_NUMBER", "7")
    checked = []
    monkeypatch.setattr(remote, "verify_checkout", lambda *args: checked.append(args))
    assert remote.preflight(root, tmp_path, tmp_path, 720001)["execution"]["github_run_number"] == 7
    assert len(checked) == 2
    monkeypatch.delenv("GITHUB_RUN_ATTEMPT")
    with pytest.raises(remote.PreparationError, match="rerun_forbidden"):
        remote.preflight(root, tmp_path, tmp_path, 720001)


def test_source_implementation_or_space_failure_precedes_run(plan_files, tmp_path, monkeypatch):
    root, _, _, _ = plan_files
    plan = remote.read_plan(root)
    calls = []
    fake = SimpleNamespace(
        implementation=lambda: {"version": "changed"},
        run=lambda *args, **kwargs: calls.append("generator"),
        DatasetGenerationConfig=lambda **kwargs: kwargs,
        resolve_generation_config=lambda _: SimpleNamespace(
            parameters=lambda: {"fixture": "resolved", "seed": 720001}
        ),
        default_inventory_config=lambda _: SimpleNamespace(
            model_dump=lambda: {"fulfillment": {"seed": 720001}}
        ),
    )
    monkeypatch.setattr(remote.importlib, "import_module", lambda _: fake)
    monkeypatch.setattr(remote, "verify_environment", lambda x: x)
    work = tmp_path / "data/generated/new"
    with pytest.raises(remote.PreparationError, match="implementation_drift"):
        remote._source_stage(plan, tmp_path, 720001, work)
    assert not work.exists() and calls == []
    fake.implementation = lambda: {"version": "fixture"}
    monkeypatch.setattr(remote.shutil, "disk_usage", lambda _: SimpleNamespace(free=1))
    with pytest.raises(remote.PreparationError, match="insufficient_free_space"):
        remote._source_stage(plan, tmp_path, 720001, work)
    assert not work.exists() and calls == []


def test_full_source_population_and_snapshot_parent_binding(plan_files):
    _, body, _, _ = plan_files
    frozen = body["source_configuration"]
    provenance = body["remote_preparation"]["source_provenance"]
    desc = {
        **{key: provenance[key] for key in ("code_sha256", "dependency_sha256", "python_version")},
        "schema_version": frozen["source_schema_version"],
        "resolved_parameters": frozen["resolved_parameters"] | {"seed": 720001},
        "context": frozen["context"],
        "inventory_configuration_sha256": frozen["inventory_configuration_sha256"]["720001"],
    }
    source = {
        "schema_version": frozen["source_schema_version"],
        "descriptor": desc,
        "provenance": provenance,
        "dataset_id": "source-sha256-" + remote.sha(remote.canonical(desc)),
    }
    snapshot_desc = {"include_evaluation_truth": False, "required_use_cases": ["forecast_source"]}
    snapshot = {
        "schema_version": frozen["snapshot_schema_version"],
        "source": source,
        "source_dataset_id": source["dataset_id"],
        "descriptor": snapshot_desc,
        "snapshot_id": "snapshot-sha256-" + remote.sha(remote.canonical(snapshot_desc)),
    }
    remote.check_source_binding(frozen, 720001, source, snapshot, provenance=provenance)
    for field, value in (
        ("context", {"changed": "calendar"}),
        ("inventory_configuration_sha256", frozen["inventory_configuration_sha256"]["720002"]),
        ("resolved_parameters", {"fixture": "resolved", "seed": 720002}),
    ):
        changed = deepcopy(source)
        changed["descriptor"][field] = value
        changed["dataset_id"] = "source-sha256-" + remote.sha(
            remote.canonical(changed["descriptor"])
        )
        with pytest.raises(remote.PreparationError, match="source_configuration_binding"):
            remote.check_source_binding(frozen, 720001, changed, provenance=provenance)
    for field, value in (("git_commit", "c" * 40), ("code_state", "dirty")):
        changed = deepcopy(source)
        changed["provenance"][field] = value
        with pytest.raises(remote.PreparationError, match="source_provenance_binding"):
            remote.check_source_binding(frozen, 720001, changed, provenance=provenance)
    bad_snapshot = deepcopy(snapshot)
    bad_snapshot["source"]["descriptor"]["context"] = {"changed": "context"}
    with pytest.raises(remote.PreparationError, match="snapshot_source_binding"):
        remote.check_source_binding(frozen, 720001, source, bad_snapshot, provenance=provenance)


def test_real_source_provenance_drift_blocks_before_qualification_or_export(
    plan_files, tmp_path, monkeypatch
):
    root, body, _, _ = plan_files
    plan = remote.read_plan(root)
    source = tmp_path / "fixture-source"
    source.mkdir()
    provenance = deepcopy(body["remote_preparation"]["source_provenance"])
    provenance["git_commit"] = "c" * 40
    (source / "dataset_manifest.v2.json").write_bytes(
        remote.canonical({"descriptor": {}, "provenance": provenance})
    )
    calls = []

    def generated(*_args, **_kwargs):
        calls.append("source")
        return {"directory": str(source)}

    def forbidden(*_args, **_kwargs):
        pytest.fail("Qualification/export must not see a source with wrong provenance")

    fake = SimpleNamespace(
        implementation=lambda: {"version": "fixture"},
        run=generated,
        DatasetGenerationConfig=lambda **kwargs: kwargs,
        resolve_generation_config=lambda _: SimpleNamespace(
            parameters=lambda: {"fixture": "resolved", "seed": 720001}
        ),
        default_inventory_config=lambda _: SimpleNamespace(
            model_dump=lambda: {"fulfillment": {"seed": 720001}}
        ),
        write_qualification=forbidden,
        export_inventory_snapshot=forbidden,
    )
    monkeypatch.setattr(remote.importlib, "import_module", lambda _: fake)
    monkeypatch.setattr(remote, "verify_environment", lambda x: x)
    monkeypatch.setattr(remote.shutil, "disk_usage", lambda _: SimpleNamespace(free=16 * 1024**3))
    work = tmp_path / "data/generated/new"
    with pytest.raises(remote.PreparationError, match="source_provenance_binding"):
        remote._source_stage(plan, tmp_path, 720001, work)
    assert calls == ["source"]
    assert (source / "dataset_manifest.v2.json").exists()


def test_stage_disk_accounting_reads_sizes_without_copying_payload(tmp_path):
    (tmp_path / "nested").mkdir()
    (tmp_path / "raw").write_bytes(b"a" * 17)
    (tmp_path / "nested/compact.gz").write_bytes(b"b" * 23)
    measured = remote.tree_usage(tmp_path)
    assert measured["files"] == 2
    assert measured["expanded_bytes"] == 40
    assert measured["free_bytes"] > 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["nested", "raw"]


def test_source_interpreter_keeps_venv_identity_even_with_shared_base_python(tmp_path):
    base = tmp_path / "base-python"
    base.write_text("fixture executable is never invoked")
    source = tmp_path / "source"
    expected = source / "services/api/.venv/bin/python"
    wrong = tmp_path / "ai/.venv/bin/python"
    for path in (expected, wrong):
        path.parent.mkdir(parents=True)
        path.symlink_to(base)
    assert expected.resolve() == wrong.resolve()
    remote.check_source_python(source, expected)
    with pytest.raises(remote.PreparationError, match="source_python_path"):
        remote.check_source_python(source, wrong)


def test_upload_gate_verifies_retained_payload_and_counts_manifest_bytes(tmp_path):
    from retailops_ai.forecasting.functional_v12_archive import seal_checkpoint

    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "example.json").write_text(json.dumps({"retained": "bytes"}))
    checkpoint = seal_checkpoint(
        {"receipts": inputs}, tmp_path / "checkpoints", lineage={"test": True}
    )
    total = sum(p.stat().st_size for p in checkpoint.iterdir())
    assert remote.checkpoint_for_upload(checkpoint, total)["checkpoint_id"] == checkpoint.name
    with pytest.raises(remote.PreparationError, match="exceeds_upload_budget"):
        remote.checkpoint_for_upload(checkpoint, total - 1)
    assert (checkpoint / "payload.tar.gz").is_file()
    with pytest.raises(remote.PreparationError, match="upload_budget_invalid"):
        remote.checkpoint_for_upload(checkpoint, remote.MAX_CHECKPOINT_BYTES + 1)


def test_workflow_has_only_explicit_triggers_and_bounded_prep_matrix():
    root = Path(__file__).parents[1]
    workflow = yaml.safe_load((root / remote.WORKFLOW_PATH).read_text())
    trigger = workflow.get("on", workflow.get(True))  # YAML 1.1 treats `on` as a boolean.
    assert set(trigger) == {"workflow_dispatch", "push"}
    assert trigger["push"] == {
        "branches": ["ai/04-01-task-calendar"],
        "paths": [remote.EXECUTION_PATH],
    }
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["prepare"]
    canary = workflow["jobs"]["canary"]
    assert job["needs"] == ["plan", "canary"]
    assert job["if"] == "needs.plan.outputs.has_remaining == 'true'"
    assert canary["needs"] == "plan"
    assert canary["strategy"]["max-parallel"] == 1
    assert canary["timeout-minutes"] == 270
    assert canary["steps"] == job["steps"]
    assert canary["env"] == job["env"]
    assert canary["strategy"]["matrix"] == "${{ fromJSON(needs.plan.outputs.canary_matrix) }}"
    assert job["strategy"]["matrix"] == "${{ fromJSON(needs.plan.outputs.remaining_matrix) }}"
    assert job["runs-on"] == "ubuntu-24.04"
    assert job["timeout-minutes"] == 270
    assert remote.WORKER_TIMEOUT_SECONDS == 240 * 60
    for worker in (canary, job):
        assert worker["timeout-minutes"] * 60 - remote.WORKER_TIMEOUT_SECONDS >= 30 * 60
    assert job["strategy"]["fail-fast"] is False
    upload = next(step for step in job["steps"] if "upload-artifact@" in step.get("uses", ""))
    assert upload["with"]["retention-days"] == 1
    assert upload["if"] == "always()"
    assert "/verified-upload/" in upload["with"]["path"]
    assert upload["with"]["overwrite"] is False
    assert upload["with"]["if-no-files-found"] == "error"
    for item in workflow["jobs"].values():
        for step in item["steps"]:
            if "uses" in step:
                assert remote._hex(step["uses"].split("@")[1], 40)
    source = (root / remote.RUNNER_PATH).read_text()
    assert "StreamingCampaignScorer" not in source
    assert "open_holdout(" not in source


@pytest.mark.parametrize("seeds", [[720001], list(range(720001, 720065))])
def test_canary_and_remaining_are_exact_disjoint_frozen_inventory(seeds):
    original = list(seeds)
    result = remote.preparation_matrices(seeds)
    canary = json.loads(result["canary_matrix"])["seed"]
    remaining = json.loads(result["remaining_matrix"])["seed"]
    assert canary == [seeds[0]]
    assert result["first_seed"] == str(seeds[0])
    assert canary + remaining == original == seeds
    assert not set(canary) & set(remaining)
    assert result["has_remaining"] == ("true" if len(seeds) > 1 else "false")
    assert set(result) == {"first_seed", "canary_matrix", "remaining_matrix", "has_remaining"}


def test_failed_preparation_preserves_published_source_and_excludes_incomplete_staging(
    plan_files,
    tmp_path,
    monkeypatch,
):
    from retailops_ai.forecasting.functional_v12_archive import (
        restore_checkpoint,
        verify_checkpoint,
    )

    control, _, _, _ = plan_files
    plan = remote.read_plan(control)
    source_root = tmp_path / "source-checkout"
    work = source_root / "data/generated/ai04-remote" / plan["freeze"]["freeze_id"] / "seed-720001"
    dataset_id = "source-sha256-" + "1" * 64
    expected_payload = b"exact,retained,source\n1,2,3\n"
    monkeypatch.setattr(remote, "preflight", lambda *args, **kwargs: plan)

    def fail_after_publication(arguments):
        published = work / "sources" / dataset_id
        published.mkdir(parents=True)
        remote.save(published / "dataset_manifest.v2.json", {"dataset_id": dataset_id})
        (published / "observations.csv").write_bytes(expected_payload)
        staging = work / "sources/.incomplete-source-123"
        staging.mkdir()
        (staging / "unfinished.csv").write_bytes(b"must not be uploaded")
        raise remote.PreparationError("simulated_compact_failure")

    monkeypatch.setattr(remote, "_worker_process", fail_after_publication)
    output = tmp_path / "out"
    with pytest.raises(remote.PreparationError, match="simulated_compact_failure"):
        remote.bounded_preparation(
            control, tmp_path / "ai", source_root, 720001, tmp_path / "unused-python", output
        )
    checkpoints = list((output / "verified-upload").iterdir())
    assert len(checkpoints) == 1
    manifest = verify_checkpoint(checkpoints[0])
    assert manifest["descriptor"]["lineage"]["scope"] == "partial_preparation_not_qualified"
    assert manifest["descriptor"]["lineage"]["forecast_model_status"] == "not_ready"
    assert manifest["descriptor"]["lineage"]["holdout_metrics_evaluated"] is False
    assert set(name.split("/")[0] for name in manifest["descriptor"]["files"]) == {
        "source",
        "receipts",
    }
    restored = restore_checkpoint(checkpoints[0], tmp_path / "restored")
    assert (restored / "source/observations.csv").read_bytes() == expected_payload
    assert not list(restored.rglob("unfinished.csv"))
    assert (work / "sources" / dataset_id / "observations.csv").read_bytes() == expected_payload


def test_worker_timeout_terminates_process_group_before_preservation(monkeypatch):
    signals = []
    waits = []

    class Process:
        pid = 123456

        def wait(self, timeout):
            waits.append(timeout)
            if len(waits) == 1:
                raise subprocess.TimeoutExpired("fixture-worker", timeout)
            return -15

    def launch(arguments, *, start_new_session):
        assert start_new_session is True
        return Process()

    monkeypatch.setattr(remote.subprocess, "Popen", launch)
    monkeypatch.setattr(remote.os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    with pytest.raises(remote.PreparationError, match="worker_timeout"):
        remote._worker_process(["unused-fixture-worker"])
    assert waits[0] == 240 * 60
    assert signals == [(123456, remote.signal.SIGTERM), (123456, remote.signal.SIGKILL)]
