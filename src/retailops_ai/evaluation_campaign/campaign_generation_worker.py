"""One isolated preparation phase; producer phases require only producer dependencies."""

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import os
import resource
import stat
import sys
from datetime import date
from pathlib import Path
from typing import Any


def read(path: Path) -> dict[str, Any]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 2 * 1024**2:
            raise ValueError("campaign_generation_invalid_metadata")
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("campaign_generation_metadata_object_required")
    return value


def write(path: Path, value: dict[str, Any]) -> None:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def planned_backend(source: Path) -> tuple[Any, dict[str, Any]]:
    """Use the reviewed producer revision's cache when present, retaining old pins.

    The public wrapper has already checked the complete clean producer commit,
    and producer() verifies its fingerprint/dependencies before calling this.
    The addon is included by Source's ordinary anomaly-module fingerprint glob.
    Both backends publish the same unchanged Source2.8 contracts and validators.
    """
    addon = source / "data/anomalies/source_cohort.py"
    if addon.is_symlink() or (addon.exists() and not addon.is_file()):
        raise ValueError("campaign_generation_invalid_planned_backend")
    if addon.is_file():
        process = importlib.import_module("data.anomalies.source_cohort")
        return process, process.implementation()
    process = importlib.import_module("data.anomalies.source_process")
    return process, {"version": "ordinary_planned_source_2_8", "cached_execution": False}


def producer(phase: str, source: Path, root: Path, request: dict[str, Any]) -> dict[str, Any]:
    sys.path.insert(0, str(source))
    plan, recipe = request["plan"], request["source"]
    if (
        hashlib.sha256((source / "data/requirements-parquet.txt").read_bytes()).hexdigest()
        != plan["exporter_lock_sha256"]
    ):
        raise ValueError("campaign_generation_exporter_lock_mismatch")
    for relative in ("services/api/requirements.txt", "data/requirements-parquet.txt"):
        for line in (source / relative).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            package, version = line.split("==")
            if importlib.metadata.version(package.split("[")[0]) != version:
                raise ValueError("campaign_generation_installed_dependency_mismatch")
    io = importlib.import_module("data.inventory.source_dataset_io")
    identity = importlib.import_module("data.generator.identity")
    provenance = identity.code_provenance(io.fingerprint())
    if (
        provenance["git_commit"] != recipe["producer_commit"]
        or provenance["code_state"] != "clean"
        or provenance["dependency_sha256"] != recipe["producer_lock_sha256"]
    ):
        raise ValueError("campaign_generation_producer_identity_mismatch")
    if phase == "generation":
        configuration = importlib.import_module("data.generator.configuration")
        parameters = {
            k: date.fromisoformat(v) if k in {"start_date", "end_date"} else v
            for k, v in plan["requested_parameters"].items()
        }
        generation = configuration.DatasetGenerationConfig(**parameters)
        effective = configuration.resolve_generation_config(generation).parameters()
        if effective != plan["resolved_parameters"]:
            raise ValueError("campaign_generation_resolved_configuration_mismatch")
        if plan["entrypoint"] == "cached_inventory_v2":
            runner = importlib.import_module("data.inventory.source_cohort_batch_v2")
            result = runner.run(generation, root / "raw")
            if result["status"] != "passed" or not result["facts_ready"]:
                raise ValueError("campaign_generation_source_not_ready")
            directory = Path(result["directory"])
            # run() already invokes the ordinary full writer and reader.
            manifest = read(directory / "dataset_manifest.v2.json")
        else:
            ordinary = importlib.import_module("data.inventory.run_source_dataset")
            process, backend = planned_backend(source)
            config = ordinary.default_inventory_config(generation)
            tables, context = process.build_tables(generation, plan["scenario_plan"], config)
            write_options = (
                {"consume_input": True}
                if backend["version"] == "planned-source-cached-execution-1.1.0"
                else {}
            )
            directory = io.write_source_dataset(
                tables,
                context,
                generation,
                config,
                root / "raw",
                scenario_plan=plan["scenario_plan"],
                **write_options,
            )
            del tables, context
            _, manifest = io.read_source_dataset(directory)
        expected_schema = "2.8.0" if plan["entrypoint"] == "planned_anomaly" else "2.7.0"
        if (
            manifest["descriptor"]["resolved_parameters"] != effective
            or manifest["schema_version"] != expected_schema
            or manifest["provenance"] != provenance
            or not manifest["facts_ready"]
        ):
            raise ValueError("campaign_generation_source_provenance_mismatch")
        value: dict[str, Any] = {
            "directory": str(directory),
            "source_dataset_id": manifest["dataset_id"],
        }
        if plan["entrypoint"] == "planned_anomaly":
            value["producer_execution"] = backend
        return value
    generated = read(root / "generation.json")
    if phase == "qualification":
        qualification = importlib.import_module("data.inventory.qualification_io")
        directory = qualification.write_qualification(
            Path(generated["directory"]), root / "qualification"
        )
        return {"directory": str(directory)}
    export = importlib.import_module("data.export.inventory_snapshot")
    qualification_result = read(root / "qualification.json")
    result = export.export_inventory_snapshot(
        Path(generated["directory"]),
        generated["source_dataset_id"],
        Path(qualification_result["directory"]),
        source / "data/generated/ai09-campaign" / root.name / "snapshot",
        include_truth=False,
        chunk_rows=plan["chunk_rows"],
        required_use_cases=tuple(plan["required_use_cases"]),
        partition_by_day=False,
    )
    return {"directory": str(result["path"])}


def consumer(phase: str, root: Path, request: dict[str, Any]) -> dict[str, Any]:
    # This exact file and the consumer modules are in the protocol runtime pin.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from retailops_ai.curated.builder import build_curated
    from retailops_ai.source_snapshot.importer import import_snapshot
    from retailops_ai.source_snapshot.protocol import Limits

    plan = request["plan"]
    budget = plan["parent_budget"]
    limits = Limits(
        max_bytes=budget["max_parent_bytes"],
        max_files=budget["max_parent_files"],
        max_rows=budget["max_rows_per_parent"],
        batch_rows=budget["batch_rows"],
    )
    if phase == "import":
        exported = read(root / "export.json")
        result = import_snapshot(
            Path(exported["directory"]), root / "input/data/generated", limits=limits
        )
        if result.snapshot.manifest["schema_version"] != plan["snapshot_schema_version"]:
            raise ValueError("campaign_generation_snapshot_version_mismatch")
        return result.summary()
    imported = read(root / "import.json")
    if phase == "curation":
        result_curated = build_curated(
            Path(imported["destination"]), root / "curated/data/generated", limits=limits
        )
        if result_curated.manifest["readiness"]["forecast_source"] != "passed":
            raise ValueError("campaign_generation_curated_not_ready")
        return result_curated.summary()
    from retailops_ai.data_contracts.identity import canonical_sha256
    from retailops_ai.evaluation_campaign.campaign_export import _producer_values
    from retailops_ai.evaluation_campaign.contract import PreparationRuntime
    from retailops_ai.evaluation_campaign.physical_contract import PhysicalSourceSpec
    from retailops_ai.evaluation_campaign.source_replay import _open_verified_source_parent
    from retailops_ai.forecasting.contract import Parent
    from retailops_ai.source_snapshot.files import file_hash

    curated_result = read(root / "curation.json")
    snapshot = Path(imported["destination"]) / "snapshot"
    curated = Path(curated_result["destination"])
    actual = read(curated / "curated_manifest.json")
    descriptor = actual["descriptor"]
    source = PhysicalSourceSpec(
        schema_version=plan["snapshot_schema_version"],
        parent=Parent.model_validate(
            {
                "source_dataset_id": descriptor["parent_source_dataset_id"],
                "snapshot_id": descriptor["parent_snapshot_id"],
                "curated_dataset_id": actual["curated_dataset_id"],
                "curated_descriptor_sha256": canonical_sha256(descriptor),
                "business_timezone": "UTC",
                "forecast_source_status": "passed",
            }
        ),
        source_parameters=plan["resolved_parameters"],
        snapshot_manifest_sha256=file_hash(snapshot, "snapshot_manifest.json")[1],
        curated_manifest_sha256=file_hash(curated, "curated_manifest.json")[1],
        **budget,
    )
    with _open_verified_source_parent(
        snapshot,
        curated,
        source,
        limits=limits,
        runtime=PreparationRuntime.model_validate(request["runtime"]),
    ) as replay:
        _producer_values(
            replay,
            request["source"]["producer_commit"],
            request["source"]["producer_lock_sha256"],
            plan["exporter_lock_sha256"],
        )
        replay.check_parents()
        inventories = {
            "snapshot": replay.snapshot_inventory_sha256,
            "curated": replay.curated_inventory_sha256,
            "logical_curated": replay.logical_curated_sha256,
        }
    return {"source": source.model_dump(mode="json"), "verified_inventories": inventories}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase", choices=("generation", "qualification", "export", "import", "curation", "verify")
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    request = read(args.root / "request.json")
    value = (
        producer(args.phase, args.source, args.root, request)
        if args.phase in {"generation", "qualification", "export"}
        else consumer(args.phase, args.root, request)
    )
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    value["worker_peak_rss_bytes"] = int(peak if sys.platform == "darwin" else peak * 1024)
    write(args.root / (args.phase + ".json"), value)


if __name__ == "__main__":
    main()
