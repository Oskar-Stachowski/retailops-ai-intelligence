"""Optional, explicitly activated remote PREPARATION ONLY; never scores a holdout.

No execution file is supplied with this runner. Activation requires a reviewed
``contracts/forecast/v2/remote-preparation.execute.json`` containing version
``forecast-remote-preparation-execution-1.0.0``, authorized=true,
phase=cohort_preparation_only, freeze_path, freeze_sha256, ai_commit,
source_commit, max_parallel (4, 8 or 16) and github_run_number (positive integer).
Both revisions are full commit SHAs. Exactly that run number and attempt 1 are
allowed; a new dispatch/push cannot regenerate the frozen seeds in a new run.

The ordinary exposure freeze descriptor additionally contains remote_preparation:
enabled, ai_commit, source_commit, ai_code_files, source_code_files,
dependency_files={ai:{path:sha},source:{path:sha}}, source_implementation,
source_provenance (full fingerprint, exact source git_commit and clean code_state),
ai_environment/source_environment={python_version,packages:{name:version}},
max_checkpoint_bytes, min_free_bytes, max_parallel and github_run_number. Source configuration has
generation (six explicit generator fields), source_schema_version,
snapshot_schema_version, resolved_parameters without seed, full context, and
inventory_configuration_sha256 keyed by decimal seed. Full method_policy,
split_policy and origin_window are mandatory top-level freeze keys.

The workflow owns checkout/dependency installation. Source generation runs in the
separate producer environment with its ordinary qualification/export path. The
consumer prepares local recipes, fully rebuilds compact inputs from retained
snapshot bytes, replays recipes without fits, and seals one verified checkpoint.
This file can run in the producer environment: AI imports are deliberately lazy.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from typing import Any

EXECUTION_PATH = "contracts/forecast/v2/remote-preparation.execute.json"
RUNNER_PATH = "scripts/prepare_forecast_functional_v12_remote.py"
WORKFLOW_PATH = ".github/workflows/ai04-cohort-preparation.yml"
SOURCE_MODULE = "data.inventory.source_cohort_batch_v2"
SOURCE_MODULE_PATH = "data/inventory/source_cohort_batch_v2.py"
SOURCE_CONSTRAINTS_PATH = "contracts/forecast/v2/source-runtime.constraints.txt"
SOURCE_RUNTIME_DISTRIBUTIONS = frozenset(
    {
        "annotated-types",
        "attrs",
        "cloudpickle",
        "jsonschema",
        "jsonschema-specifications",
        "numpy",
        "pyarrow",
        "pydantic",
        "pydantic-core",
        "referencing",
        "rpds-py",
        "typing-extensions",
        "typing-inspection",
    }
)
# Mirrors resource-plan 1.1.0 without importing AI dependencies in the source venv.
MAX_CHECKPOINT_BYTES = 768 * 1024**2
MIN_FREE_BYTES = 8 * 1024**3
# Source generation, ordinary qualification/export, both full AI verifications,
# compact replay and checkpoint sealing share one technical deadline. The workflow
# reserves a further 30 minutes for setup, failure preservation and upload.
WORKER_TIMEOUT_SECONDS = 240 * 60


class PreparationError(ValueError):
    """A failed prerequisite never permits source generation or partial qualification."""


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def safe_path(root: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if not relative or path.is_absolute() or any(p in {"", ".", ".."} for p in relative.split("/")):
        raise PreparationError("remote_unsafe_relative_path")
    if "\\" in relative or "\x00" in relative or path.as_posix() != relative:
        raise PreparationError("remote_unsafe_relative_path")
    candidate = root / relative
    if any(p.is_symlink() for p in (candidate, *candidate.parents)):
        raise PreparationError("remote_symlink_path")
    return candidate


def read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024**2:
        raise PreparationError("remote_missing_or_oversized_reviewed_file")
    result = json.loads(path.read_bytes())
    if not isinstance(result, dict):
        raise PreparationError("remote_expected_json_object")
    canonical(result)
    return result


def save(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(canonical(body) + b"\n")


def _git(root: Path, *args: str) -> str:
    executable = shutil.which("git")
    if executable is None:
        raise PreparationError("remote_git_required")
    return subprocess.check_output([executable, "-C", str(root), *args], text=True).strip()  # noqa: S603


def _hex(value: Any, length: int) -> bool:
    return (
        isinstance(value, str) and re.fullmatch("[0-9a-f]{" + str(length) + "}", value) is not None
    )


def _pins(root: Path, pins: Any) -> None:
    if not isinstance(pins, dict) or not pins:
        raise PreparationError("remote_missing_code_or_dependency_pins")
    for relative, expected in pins.items():
        path = safe_path(root, relative)
        if not _hex(expected, 64) or not path.is_file() or sha(path.read_bytes()) != expected:
            raise PreparationError("remote_code_or_dependency_drift:" + relative)


def source_runtime_packages(root: Path) -> dict[str, str]:
    """Strict distribution pins; never resolve dependencies or import producer modules."""
    path = safe_path(root, SOURCE_CONSTRAINTS_PATH)
    if not path.is_file() or path.stat().st_size > 4096:
        raise PreparationError("remote_source_runtime_constraints_missing_or_oversized")
    packages = {}
    for line in path.read_text().splitlines():
        pin = line.strip()
        if not pin or pin.startswith("#"):
            continue
        match = re.fullmatch(r"([a-z0-9]+(?:-[a-z0-9]+)*)==([A-Za-z0-9][A-Za-z0-9.!+_-]*)", pin)
        if match is None or match[1] in packages:
            raise PreparationError("remote_source_runtime_constraints_not_exact")
        packages[match[1]] = match[2]
    if set(packages) != SOURCE_RUNTIME_DISTRIBUTIONS:
        raise PreparationError("remote_source_runtime_closure_incomplete")
    return packages


def read_plan(
    control_root: Path, *, expected_execution_sha256: str | None = None
) -> dict[str, Any]:
    """Pure file/contract validation; no generator imports, network requests or writes."""
    execution_file = safe_path(control_root, EXECUTION_PATH)
    execution = read_json(execution_file)
    if expected_execution_sha256 is not None and (
        not _hex(expected_execution_sha256, 64)
        or sha(execution_file.read_bytes()) != expected_execution_sha256
    ):
        raise PreparationError("remote_execution_sha256_mismatch")
    fields = {
        "version",
        "authorized",
        "phase",
        "freeze_path",
        "freeze_sha256",
        "ai_commit",
        "source_commit",
        "max_parallel",
        "github_run_number",
    }
    if set(execution) != fields or (
        execution["version"] != "forecast-remote-preparation-execution-1.0.0"
        or execution["authorized"] is not True
        or execution["phase"] != "cohort_preparation_only"
        or not _hex(execution["ai_commit"], 40)
        or not _hex(execution["source_commit"], 40)
        or not _hex(execution["freeze_sha256"], 64)
        or type(execution["max_parallel"]) is not int
        or execution["max_parallel"] not in {4, 8, 16}
        or type(execution["github_run_number"]) is not int
        or execution["github_run_number"] <= 0
    ):
        raise PreparationError("remote_execution_not_authorized_or_not_immutable")
    freeze_file = safe_path(control_root, execution["freeze_path"])
    if not execution["freeze_path"].startswith(
        ("contracts/forecast/v2/", "contracts/forecast/v3/")
    ):
        raise PreparationError("remote_freeze_path_scope")
    freeze = read_json(freeze_file)
    body = freeze["descriptor"]
    if sha(freeze_file.read_bytes()) != execution["freeze_sha256"] or (
        freeze["freeze_id"] != "functional-v12-freeze-sha256-" + sha(canonical(body))
        or body["version"] != "forecast-functional-cohort-plan-1.0.0"
        or body["holdout_metrics_evaluated_before_freeze"] is not False
    ):
        raise PreparationError("remote_freeze_mismatch")
    seeds = body["seeds"]
    if (
        not isinstance(seeds, list)
        or not 1 <= len(seeds) <= 64
        or any(type(seed) is not int or not 0 <= seed < 2**32 for seed in seeds)
        or seeds != sorted(set(seeds))
        or set(seeds) & set(body["previously_used_seeds"])
    ):
        raise PreparationError("remote_seed_inventory_or_previous_exposure")
    remote = body["remote_preparation"]
    if (
        remote["enabled"] is not True
        or type(remote.get("github_run_number")) is not int
        or any(
            remote[name] != execution[name]
            for name in ("ai_commit", "source_commit", "max_parallel", "github_run_number")
        )
    ):
        raise PreparationError("remote_preparation_not_enabled_or_commit_binding")
    if (
        type(remote["max_checkpoint_bytes"]) is not int
        or not 1 <= remote["max_checkpoint_bytes"] <= MAX_CHECKPOINT_BYTES
        or type(remote["min_free_bytes"]) is not int
        or remote["min_free_bytes"] < MIN_FREE_BYTES
        or set(remote["dependency_files"]) != {"ai", "source"}
        or not {RUNNER_PATH, WORKFLOW_PATH} <= set(remote["ai_code_files"])
        or SOURCE_MODULE_PATH not in remote["source_code_files"]
        or not {"uv.lock", "pyproject.toml", SOURCE_CONSTRAINTS_PATH}
        <= set(remote["dependency_files"]["ai"])
        or not {
            "services/api/requirements.txt",
            "services/api/requirements-dev.txt",
            "data/requirements-parquet.txt",
        }
        <= set(remote["dependency_files"]["source"])
    ):
        raise PreparationError("remote_resources_or_pin_inventory")
    for role in ("ai", "source"):
        env = remote[role + "_environment"]
        if env["python_version"] != "3.11.15" or not env["packages"]:
            raise PreparationError("remote_environment_not_frozen")
    if source_runtime_packages(control_root) != remote["source_environment"]["packages"]:
        raise PreparationError("remote_source_runtime_environment_not_complete_constraints")
    provenance = remote.get("source_provenance", {})
    if set(provenance) != {
        "code_files",
        "code_sha256",
        "dependency_files",
        "dependency_sha256",
        "python_version",
        "git_commit",
        "code_state",
    } or (
        provenance["git_commit"] != remote["source_commit"]
        or provenance["code_state"] != "clean"
        or provenance["python_version"] != remote["source_environment"]["python_version"]
        or not _hex(provenance["code_sha256"], 64)
        or not _hex(provenance["dependency_sha256"], 64)
        or not provenance["code_files"]
        or not provenance["dependency_files"]
    ):
        raise PreparationError("remote_source_provenance_not_fully_frozen")
    source_configuration = body["source_configuration"]
    if set(source_configuration) != {
        "generation",
        "source_schema_version",
        "snapshot_schema_version",
        "resolved_parameters",
        "context",
        "inventory_configuration_sha256",
    } or (
        not isinstance(source_configuration["resolved_parameters"], dict)
        or "seed" in source_configuration["resolved_parameters"]
        or not source_configuration["context"]
        or not source_configuration["source_schema_version"]
        or not source_configuration["snapshot_schema_version"]
        or set(source_configuration["inventory_configuration_sha256"]) != {str(s) for s in seeds}
        or any(
            not _hex(value, 64)
            for value in source_configuration["inventory_configuration_sha256"].values()
        )
    ):
        raise PreparationError("remote_source_template_or_configuration_hash_inventory")
    config = source_configuration["generation"]
    if set(config) != {"profile", "days", "products", "stores", "warehouses", "end_date"} or (
        config["profile"] != "ai-intermittent-v1"
        or config["days"] != 232
        or config["products"] != 100
        or config["stores"] != 2
        or config["warehouses"] != 2
        or date.fromisoformat(config["end_date"]).isoformat() != config["end_date"]
    ):
        raise PreparationError("remote_source_configuration_not_reviewed")
    for key in ("method_policy", "split_policy", "origin_window"):
        if not isinstance(body[key], dict) or not body[key]:
            raise PreparationError("remote_missing_frozen_policy")
    return {
        "execution": execution,
        "execution_sha256": sha(execution_file.read_bytes()),
        "freeze": freeze,
    }


def verify_checkout(
    root: Path, commit: str, code_files: dict[str, str], dependencies: dict[str, str]
) -> None:
    if _git(root, "rev-parse", "HEAD") != commit or _git(root, "diff", "HEAD", "--name-only"):
        raise PreparationError("remote_checkout_commit_or_tracked_drift")
    if any(
        name.endswith(".py")
        for name in _git(root, "ls-files", "--others", "--exclude-standard").splitlines()
    ):
        raise PreparationError("remote_untracked_python_code")
    _pins(root, code_files)
    _pins(root, dependencies)


def verify_environment(expected: dict[str, Any]) -> dict[str, Any]:
    try:
        actual = {
            "python_version": platform.python_version(),
            "packages": {name: importlib.metadata.version(name) for name in expected["packages"]},
        }
    except importlib.metadata.PackageNotFoundError as error:
        raise PreparationError("remote_python_or_dependency_environment_missing") from error
    if actual != expected:
        raise PreparationError("remote_python_or_dependency_environment_drift")
    return actual


def verify_workflow_run(plan: dict[str, Any]) -> None:
    """Run numbers advance on new dispatches; attempts advance on reruns of that run."""
    if os.environ.get("GITHUB_RUN_NUMBER") != str(plan["execution"]["github_run_number"]):
        raise PreparationError("remote_run_number_not_reserved_restore_retained_checkpoint")
    if os.environ.get("GITHUB_RUN_ATTEMPT") != "1":
        raise PreparationError("remote_rerun_forbidden_restore_retained_checkpoint")


def preflight(
    control_root: Path,
    ai_root: Path,
    source_root: Path,
    seed: int,
    *,
    expected_execution_sha256: str | None = None,
) -> dict[str, Any]:
    plan = read_plan(control_root, expected_execution_sha256=expected_execution_sha256)
    body = plan["freeze"]["descriptor"]
    if type(seed) is not int or seed not in body["seeds"]:
        raise PreparationError("remote_seed_not_planned")
    verify_workflow_run(plan)
    remote = body["remote_preparation"]
    _pins(
        control_root,
        {SOURCE_CONSTRAINTS_PATH: remote["dependency_files"]["ai"][SOURCE_CONSTRAINTS_PATH]},
    )
    for role, root in (("ai", ai_root), ("source", source_root)):
        verify_checkout(
            root,
            remote[role + "_commit"],
            remote[role + "_code_files"],
            remote["dependency_files"][role],
        )
    return plan


def _source_stage(plan: dict[str, Any], source_root: Path, seed: int, work: Path) -> None:
    """Executed only in the producer environment, after the exact same preflight."""
    body = plan["freeze"]["descriptor"]
    remote = body["remote_preparation"]
    environment = verify_environment(remote["source_environment"])
    if work.exists() or not work.is_relative_to(source_root / "data/generated"):
        raise PreparationError("remote_source_requires_new_generated_directory")
    # Fixed allowlisted module, never import a module name supplied by the freeze.
    generator = importlib.import_module(SOURCE_MODULE)
    configuration = importlib.import_module("data.generator.configuration")
    qualification_api = importlib.import_module("data.inventory.qualification_io")
    export_api = importlib.import_module("data.export.inventory_snapshot")
    if generator.implementation() != remote["source_implementation"]:
        raise PreparationError("remote_source_implementation_drift")
    config = dict(body["source_configuration"]["generation"])
    config["end_date"] = date.fromisoformat(config["end_date"])
    generation = configuration.DatasetGenerationConfig(seed=seed, **config)
    expected_source = body["source_configuration"]
    if (
        configuration.resolve_generation_config(generation).parameters()
        != expected_source["resolved_parameters"] | {"seed": seed}
        or sha(canonical(generator.default_inventory_config(generation).model_dump()))
        != expected_source["inventory_configuration_sha256"][str(seed)]
    ):
        raise PreparationError("remote_resolved_generation_or_inventory_config_drift")
    if shutil.disk_usage(source_root).free < remote["min_free_bytes"]:
        raise PreparationError("remote_insufficient_free_space_before_generation")
    work.mkdir(parents=True)
    stage_usage = {"before_generation": tree_usage(work)}
    source = generator.run(generation, work / "sources")
    source_manifest = read_json(Path(source["directory"]) / "dataset_manifest.v2.json")
    check_source_binding(
        body["source_configuration"],
        seed,
        source_manifest,
        provenance=remote["source_provenance"],
    )
    stage_usage["after_source"] = tree_usage(work)
    qualification = qualification_api.write_qualification(
        Path(source["directory"]), work / "qualifications"
    )
    stage_usage["after_qualification"] = tree_usage(work)
    snapshot = export_api.export_inventory_snapshot(
        Path(source["directory"]),
        source["dataset_id"],
        qualification,
        work / "snapshots",
        include_truth=False,
        chunk_rows=8192,
        required_use_cases=("forecast_source",),
    )
    snapshot_manifest = read_json(Path(snapshot["path"]) / "snapshot_manifest.json")
    check_source_binding(
        body["source_configuration"],
        seed,
        source_manifest,
        snapshot_manifest,
        provenance=remote["source_provenance"],
    )
    stage_usage["after_snapshot_export"] = tree_usage(work)
    save(
        work / "source-stage.json",
        {
            "source": source,
            "qualification": str(qualification),
            "snapshot": snapshot,
            "freeze_id": plan["freeze"]["freeze_id"],
            "seed": seed,
            "environment": environment,
            "stage_disk_usage": stage_usage,
            "source_rows": sum(
                table["row_count"] for table in source_manifest["descriptor"]["tables"].values()
            ),
            "source_tables": len(source_manifest["descriptor"]["tables"]),
            "snapshot_rows": sum(table["row_count"] for table in snapshot_manifest["tables"]),
            "snapshot_tables": len(snapshot_manifest["tables"]),
            "scope": "cohort_preparation_only",
            "holdout_metrics_evaluated": False,
        },
    )


def prepare_remote(
    control_root: Path,
    ai_root: Path,
    source_root: Path,
    seed: int,
    source_python: Path,
    output: Path,
    *,
    expected_execution_sha256: str | None = None,
) -> Path:
    """Exactly one new cohort and verified checkpoint; no campaign scoring API is imported."""
    plan = preflight(
        control_root,
        ai_root,
        source_root,
        seed,
        expected_execution_sha256=expected_execution_sha256,
    )
    body = plan["freeze"]["descriptor"]
    remote = body["remote_preparation"]
    environment = verify_environment(remote["ai_environment"])
    from retailops_ai.forecasting.contract import OriginWindow
    from retailops_ai.forecasting.functional_v12_archive import seal_checkpoint
    from retailops_ai.forecasting.functional_v12_cohort import (
        load_cohort,
        prepare_cohort,
        replay_cohort,
    )
    from retailops_ai.forecasting.functional_v12_inputs import replay_compact_inputs, seal_snapshot
    from retailops_ai.forecasting.functional_v12_recipe import FunctionalV12Policy
    from retailops_ai.forecasting.manifest_contract import SplitPolicy

    policy = FunctionalV12Policy.model_validate_json(canonical(body["method_policy"]))
    split = SplitPolicy.model_validate_json(canonical(body["split_policy"]))
    window = OriginWindow.model_validate_json(canonical(body["origin_window"]))
    if policy.mean_variant == "hgb_blend" or any(
        model.model_dump(mode="json") != body[name]
        for model, name in (
            (policy, "method_policy"),
            (split, "split_policy"),
            (window, "origin_window"),
        )
    ):
        raise PreparationError("remote_policy_not_fully_resolved_or_unsupported")
    check_source_python(source_root, source_python)
    if output.exists():
        raise PreparationError("remote_existing_output_restore_instead_of_regenerate")
    work = source_root / "data/generated/ai04-remote" / plan["freeze"]["freeze_id"] / f"seed-{seed}"
    if work.exists() or shutil.disk_usage(source_root).free < remote["min_free_bytes"]:
        raise PreparationError("remote_existing_source_or_insufficient_space")
    env = dict(os.environ, PYTHONPATH=str(source_root), PYTHONNOUSERSITE="1")
    subprocess.run(  # noqa: S603 - fixed local interpreter/script, explicit argument list, no shell
        [
            str(source_python),
            str(ai_root / RUNNER_PATH),
            "source",
            "--control-root",
            str(control_root),
            "--ai-root",
            str(ai_root),
            "--source-root",
            str(source_root),
            "--seed",
            str(seed),
            "--expected-execution-sha256",
            plan["execution_sha256"],
        ],
        cwd=source_root,
        env=env,
        check=True,
        timeout=WORKER_TIMEOUT_SECONDS,
    )
    result = read_json(work / "source-stage.json")
    if (
        result["seed"] != seed
        or result["freeze_id"] != plan["freeze"]["freeze_id"]
        or result.get("environment") != remote["source_environment"]
    ):
        raise PreparationError("remote_source_receipt_binding")
    snapshot = Path(result["snapshot"]["path"])
    check_source_binding(
        body["source_configuration"],
        seed,
        read_json(Path(result["source"]["directory"]) / "dataset_manifest.v2.json"),
        read_json(snapshot / "snapshot_manifest.json"),
        provenance=remote["source_provenance"],
    )
    prepared = prepare_cohort(snapshot, f"seed-{seed}", split, window, policy, work / "cohorts")
    stage_usage = {**result["stage_disk_usage"], "after_compact_and_fit": tree_usage(work)}
    cohort = load_cohort(prepared)
    receipts = work / "receipts"
    receipts.mkdir()
    verified = seal_snapshot(snapshot, receipts / "source-replay-seal.json")
    compact_root = prepared / cohort["descriptor"]["compact_parent"]["path"]
    compact = replay_compact_inputs(compact_root, verified)
    stage_usage["after_full_compact_replay"] = tree_usage(work)
    semantic = {
        "status": "passed",
        "inputs_id": compact["inputs_id"],
        "source_seal_id": verified.seal["seal_id"],
        "compact_manifest_sha256": sha((compact_root / "compact_manifest.json").read_bytes()),
        "replay": "full_sealed_snapshot_projection_features_labels_memberships",
        "model_fits": 0,
        "holdout_metrics_evaluated": False,
    }
    save(receipts / "semantic-replay.json", semantic)
    save(receipts / "recipe-replay.json", replay_cohort(prepared))
    stage_usage["after_recipe_replay"] = tree_usage(work)
    save(
        receipts / "resources.json",
        {
            "phase_samples": stage_usage,
            "measurement": "stage_end_tree_bytes_no_extra_payload_copies",
            "not_a_continuous_peak_measurement": True,
            "compact_counts": compact["descriptor"]["counts"],
            "source_rows": result["source_rows"],
            "source_tables": result["source_tables"],
            "snapshot_rows": result["snapshot_rows"],
            "snapshot_tables": result["snapshot_tables"],
            "checkpoint_cap_including_payload_and_manifest_bytes": remote["max_checkpoint_bytes"],
        },
    )
    save(receipts / "freeze.json", plan["freeze"])
    save(receipts / "execution.json", plan["execution"])
    save(receipts / "source-stage.json", result)
    save(
        receipts / "preparation.json",
        {
            "scope": "cohort_preparation_only",
            "seed": seed,
            "freeze_id": plan["freeze"]["freeze_id"],
            "ai_environment": environment,
            "source_environment": result["environment"],
            "prepared_at": datetime.now(UTC).isoformat(),
            "holdout_metrics_evaluated": False,
            "forecast_model_status": "not_ready",
            "github_run_id": os.environ.get("GITHUB_RUN_ID"),
            "github_run_number": os.environ.get("GITHUB_RUN_NUMBER"),
            "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        },
    )
    # Recheck all checked-out code and frozen bytes after every expensive operation.
    preflight(
        control_root, ai_root, source_root, seed, expected_execution_sha256=plan["execution_sha256"]
    )
    lineage = {
        "freeze_id": plan["freeze"]["freeze_id"],
        "seed": seed,
        "cohort_id": f"seed-{seed}",
        "source_dataset_id": cohort["descriptor"]["source_dataset_id"],
        "snapshot_id": cohort["descriptor"]["snapshot_id"],
        "prepared_cohort_id": cohort["cohort_artifact_id"],
        "scope": "cohort_preparation_only",
        "holdout_metrics_evaluated": False,
        "semantic_replay": receipt_ref(receipts, "semantic-replay.json"),
        "recipe_replay": receipt_ref(receipts, "recipe-replay.json"),
    }
    checkpoint = seal_checkpoint(
        {
            "source": Path(result["source"]["directory"]),
            "qualification": Path(result["qualification"]),
            "snapshot": snapshot,
            "cohort": prepared,
            "receipts": receipts,
        },
        output,
        lineage=lineage,
    )
    manifest = checkpoint_for_upload(checkpoint, remote["max_checkpoint_bytes"])
    if manifest["descriptor"]["lineage"] != lineage:
        raise PreparationError("remote_checkpoint_lineage_mismatch")
    return checkpoint


def _published(root: Path, pattern: str, manifest_name: str, id_key: str) -> Path | None:
    """Only complete atomic publication names, never a .staging or failed directory."""
    found = []
    for candidate in sorted(root.glob(pattern)):
        if candidate.is_symlink() or not candidate.is_dir():
            raise PreparationError("remote_partial_publication_not_directory")
        manifest = read_json(candidate / manifest_name)
        if manifest[id_key] != candidate.name:
            raise PreparationError("remote_partial_publication_identity")
        found.append(candidate)
    if len(found) > 1:
        raise PreparationError("remote_partial_ambiguous_publication")
    return found[0] if found else None


def preserve_partial(
    work: Path,
    output: Path,
    plan: dict[str, Any],
    seed: int,
    error: str,
) -> Path:
    """Preserve exact published bytes for recovery; no generator, fitter or evaluator calls."""
    from retailops_ai.forecasting.functional_v12_archive import seal_checkpoint

    roots: dict[str, Path] = {}
    for namespace, directory, pattern, filename, id_key in (
        ("source", "sources", "source-sha256-*", "dataset_manifest.v2.json", "dataset_id"),
        (
            "qualification",
            "qualifications",
            "inventory-labels-sha256-*",
            "qualification_manifest.json",
            "qualification_id",
        ),
        ("snapshot", "snapshots", "source-sha256-*", "snapshot_manifest.json", "source_dataset_id"),
    ):
        published = _published(work / directory, pattern, filename, id_key)
        if published is not None:
            roots[namespace] = published
    receipts = output / "failure-receipts"
    receipts.mkdir(parents=True, exist_ok=False)
    save(
        receipts / "failure.json",
        {
            "status": "failed",
            "scope": "partial_preparation_not_qualified",
            "seed": seed,
            "freeze_id": plan["freeze"]["freeze_id"],
            "error": error,
            "published_namespaces_retained": sorted(roots),
            "incomplete_staging_included": False,
            "source_regeneration": "forbidden_restore_retained_bytes",
            "holdout_metrics_evaluated": False,
            "forecast_model_status": "not_ready",
            "failed_at": datetime.now(UTC).isoformat(),
        },
    )
    save(receipts / "freeze.json", plan["freeze"])
    save(receipts / "execution.json", plan["execution"])
    if (work / "source-stage.json").is_file():
        save(receipts / "source-stage.json", read_json(work / "source-stage.json"))
    # Include completed replay/resource receipts if present, but do not imply success.
    for path in sorted((work / "receipts").glob("*.json")):
        if path.name not in {"freeze.json", "execution.json", "source-stage.json", "failure.json"}:
            save(receipts / path.name, read_json(path))
    roots["receipts"] = receipts
    lineage = {
        "scope": "partial_preparation_not_qualified",
        "freeze_id": plan["freeze"]["freeze_id"],
        "seed": seed,
        "cohort_id": f"seed-{seed}",
        "holdout_metrics_evaluated": False,
        "forecast_model_status": "not_ready",
        "published_namespaces_retained": sorted(roots),
        "source_regeneration": "forbidden_restore_retained_bytes",
    }
    checkpoint = seal_checkpoint(roots, output / "partial-staging", lineage=lineage)
    checkpoint_for_upload(
        checkpoint, plan["freeze"]["descriptor"]["remote_preparation"]["max_checkpoint_bytes"]
    )
    return checkpoint


def _worker_process(arguments: list[str]) -> None:
    """A bounded process group leaves time for verified preservation before the job timeout."""
    process = subprocess.Popen(arguments, start_new_session=True)  # noqa: S603 - explicit fixed local script, no shell
    try:
        code = process.wait(timeout=WORKER_TIMEOUT_SECONDS)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pass
        # The direct worker may already be gone while a producer grandchild is alive.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=15)
        raise PreparationError("remote_worker_timeout_or_interruption") from None
    if code:
        # A killed/OOM worker may leave its producer child alive; stop every writer first.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        raise PreparationError(f"remote_worker_failed_exit_{code}")


def bounded_preparation(
    control_root: Path,
    ai_root: Path,
    source_root: Path,
    seed: int,
    source_python: Path,
    output: Path,
    *,
    expected_execution_sha256: str | None = None,
) -> Path:
    plan = preflight(
        control_root,
        ai_root,
        source_root,
        seed,
        expected_execution_sha256=expected_execution_sha256,
    )
    if output.exists():
        raise PreparationError("remote_existing_output_restore_instead_of_regenerate")
    work = source_root / "data/generated/ai04-remote" / plan["freeze"]["freeze_id"] / f"seed-{seed}"
    if work.exists():
        raise PreparationError("remote_existing_source_restore_instead_of_regenerate")
    output.mkdir(parents=True)
    try:
        _worker_process(
            [
                sys.executable,
                str(ai_root / RUNNER_PATH),
                "worker",
                "--control-root",
                str(control_root),
                "--ai-root",
                str(ai_root),
                "--source-root",
                str(source_root),
                "--seed",
                str(seed),
                "--source-python",
                str(source_python),
                "--output",
                str(output / "full-staging"),
                "--expected-execution-sha256",
                plan["execution_sha256"],
            ]
        )
        candidates = list((output / "full-staging").glob("functional-checkpoint-sha256-*"))
        if len(candidates) != 1:
            raise PreparationError("remote_completed_checkpoint_inventory")
        checkpoint = candidates[0]
        checkpoint_for_upload(
            checkpoint, plan["freeze"]["descriptor"]["remote_preparation"]["max_checkpoint_bytes"]
        )
    except Exception as exc:
        checkpoint = preserve_partial(work, output, plan, seed, str(exc))
        publish_upload(checkpoint, output / "verified-upload")
        raise
    return publish_upload(checkpoint, output / "verified-upload")


def publish_upload(checkpoint: Path, output: Path) -> Path:
    """Rename verified data only; upload globs never include unverified/oversized staging."""
    from retailops_ai.source_snapshot.publish import publish_noreplace

    output.mkdir(parents=True, exist_ok=True)
    destination = output / checkpoint.name
    publish_noreplace(checkpoint, destination)
    return destination


def checkpoint_for_upload(root: Path, maximum_bytes: int) -> dict[str, Any]:
    from retailops_ai.forecasting.functional_v12_archive import verify_checkpoint

    if type(maximum_bytes) is not int or not 1 <= maximum_bytes <= MAX_CHECKPOINT_BYTES:
        raise PreparationError("remote_upload_budget_invalid")
    manifest = verify_checkpoint(root)
    if sum(p.stat().st_size for p in root.iterdir()) > maximum_bytes:
        raise PreparationError("remote_checkpoint_exceeds_upload_budget_retained_locally")
    return manifest


def check_source_python(source_root: Path, interpreter: Path) -> None:
    # Resolving the interpreter itself would collapse distinct venv symlinks to the same binary.
    if interpreter.absolute() != source_root / "services/api/.venv/bin/python":
        raise PreparationError("remote_source_python_path")


def check_source_binding(
    frozen: dict[str, Any],
    seed: int,
    source: dict[str, Any],
    snapshot: dict[str, Any] | None = None,
    *,
    provenance: dict[str, Any],
) -> None:
    """Match the complete frozen source population definition, not merely the claimed seed."""
    desc = source["descriptor"]
    if source.get("provenance") != provenance or any(
        desc.get(key) != provenance[key]
        for key in ("code_sha256", "dependency_sha256", "python_version")
    ):
        raise PreparationError("remote_source_provenance_binding")
    if (
        source["schema_version"] != frozen["source_schema_version"]
        or desc["schema_version"] != frozen["source_schema_version"]
        or desc["resolved_parameters"] != frozen["resolved_parameters"] | {"seed": seed}
        or desc["context"] != frozen["context"]
        or desc["inventory_configuration_sha256"]
        != frozen["inventory_configuration_sha256"][str(seed)]
        or source["dataset_id"] != "source-sha256-" + sha(canonical(desc))
    ):
        raise PreparationError("remote_source_configuration_binding")
    if snapshot is not None and (
        snapshot["schema_version"] != frozen["snapshot_schema_version"]
        or snapshot["source"] != source
        or snapshot["source_dataset_id"] != source["dataset_id"]
        or snapshot["snapshot_id"] != "snapshot-sha256-" + sha(canonical(snapshot["descriptor"]))
        or snapshot["descriptor"]["include_evaluation_truth"] is not False
        or snapshot["descriptor"]["required_use_cases"] != ["forecast_source"]
    ):
        raise PreparationError("remote_snapshot_source_binding")


def tree_usage(root: Path) -> dict[str, int]:
    files_count = total = 0
    for base, directories, names in os.walk(root, followlinks=False):
        for name in (*directories, *names):
            path = Path(base) / name
            if path.is_symlink():
                raise PreparationError("remote_resource_tree_symlink")
            if path.is_file():
                files_count += 1
                total += path.stat().st_size
    return {
        "files": files_count,
        "expanded_bytes": total,
        "free_bytes": shutil.disk_usage(root).free,
    }


def receipt_ref(root: Path, name: str) -> dict[str, str]:
    return {"path": "receipts/" + name, "sha256": sha((root / name).read_bytes())}


def preparation_matrices(seeds: list[int]) -> dict[str, str]:
    """Technical canary then the remaining frozen inventory; no outcome-based selection."""
    if (
        not seeds
        or len(seeds) > 64
        or seeds != sorted(set(seeds))
        or any(type(seed) is not int or not 0 <= seed < 2**32 for seed in seeds)
    ):
        raise PreparationError("remote_canary_seed_inventory")
    return {
        "first_seed": str(seeds[0]),
        "canary_matrix": json.dumps({"seed": seeds[:1]}, separators=(",", ":")),
        "remaining_matrix": json.dumps({"seed": seeds[1:]}, separators=(",", ":")),
        "has_remaining": "true" if len(seeds) > 1 else "false",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "prepare", "worker", "source"))
    parser.add_argument("--control-root", type=Path, required=True)
    parser.add_argument("--ai-root", type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--source-python", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--expected-execution-sha256")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    control = args.control_root.resolve()
    if args.mode == "plan":
        plan = read_plan(control, expected_execution_sha256=args.expected_execution_sha256)
        verify_workflow_run(plan)
        remote = plan["freeze"]["descriptor"]["remote_preparation"]
        _pins(control, remote["ai_code_files"])
        _pins(control, remote["dependency_files"]["ai"])
        changed = set(
            _git(control, "diff", "--name-only", remote["ai_commit"], "HEAD").splitlines()
        )
        if changed - {EXECUTION_PATH, plan["execution"]["freeze_path"]}:
            raise PreparationError("remote_control_commit_changes_unreviewed_code")
        outputs = {
            **preparation_matrices(plan["freeze"]["descriptor"]["seeds"]),
            "ai_commit": remote["ai_commit"],
            "source_commit": remote["source_commit"],
            "max_parallel": str(remote["max_parallel"]),
            "execution_sha256": plan["execution_sha256"],
            "freeze_id": plan["freeze"]["freeze_id"],
        }
        if args.github_output is not None:
            with args.github_output.open("a") as stream:
                stream.write("".join(name + "=" + value + "\n" for name, value in outputs.items()))
        print(json.dumps(outputs, sort_keys=True))
        return 0
    if args.ai_root is None or args.source_root is None or args.seed is None:
        parser.error("prepare/source require --ai-root, --source-root and --seed")
    ai_root, source_root = args.ai_root.resolve(), args.source_root.resolve()
    if args.mode == "source":
        plan = preflight(
            control,
            ai_root,
            source_root,
            args.seed,
            expected_execution_sha256=args.expected_execution_sha256,
        )
        work = (
            source_root
            / "data/generated/ai04-remote"
            / plan["freeze"]["freeze_id"]
            / f"seed-{args.seed}"
        )
        _source_stage(plan, source_root, args.seed, work)
        return 0
    if args.source_python is None or args.output is None:
        parser.error("prepare requires --source-python and --output")
    runner = bounded_preparation if args.mode == "prepare" else prepare_remote
    checkpoint = runner(
        control,
        ai_root,
        source_root,
        args.seed,
        args.source_python.absolute(),
        args.output.resolve(),
        expected_execution_sha256=args.expected_execution_sha256,
    )
    print(
        json.dumps(
            {
                "status": "prepared",
                "checkpoint": str(checkpoint),
                "forecast_model_status": "not_ready",
                "holdout_metrics_evaluated": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PreparationError, KeyError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(
            json.dumps(
                {"status": "blocked", "error": str(exc), "forecast_model_status": "not_ready"}
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
