"""Inventory/anomaly snapshot metadata without a producer runtime dependency."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]
from jsonschema.exceptions import ValidationError  # type: ignore[import-untyped]

from retailops_ai.source_snapshot.files import (
    SnapshotError,
    decode_json,
    file_hash,
    inventory,
    json_sha256,
    nonfinite,
    read_bytes,
    read_json,
    relative_path,
    unique_keys,
)
from retailops_ai.source_snapshot.protocol import LOGICAL_FIELDS, Limits, Snapshot

VERSION = "1.1.0"
USE_CASES = {"forecast_source", "inventory_source"}
RESOURCE_NAMES = {
    "inventory_snapshot.v1_1.schema.json": "snapshot_manifest.schema.json",
    "inventory_source_dataset.v2_7.schema.json": "source_manifest.schema.json",
    "inventory_source_tables.v1.schema.json": "inventory_tables.schema.json",
    "inventory_label_qualification.v1.schema.json": "qualification.schema.json",
    "source_snapshot_handoff.v1_1.json": "contract.json",
}
ANOMALY_RESOURCE_NAMES = {
    "anomaly_snapshot.v1_2.schema.json": "snapshot_manifest.schema.json",
    "anomaly_source_dataset.v2_8.schema.json": "source_manifest.schema.json",
    "inventory_source_tables.v1.schema.json": "inventory_tables.schema.json",
    "inventory_label_qualification.v1.schema.json": "qualification.schema.json",
    "business_anomaly_plan.v1.schema.json": "anomaly_plan.schema.json",
    "business_physical_anomaly_plan.v1.schema.json": "physical_plan.schema.json",
    "source_snapshot_handoff.v1_2.json": "contract.json",
}


def resource_names(version: str) -> dict[str, str]:
    if version == "1.2.0":
        return ANOMALY_RESOURCE_NAMES
    if version == VERSION:
        return RESOURCE_NAMES
    raise SnapshotError("unsupported_inventory_snapshot_version")


def resource_bytes(name: str, version: str = VERSION) -> bytes:
    resource_names(version)
    directory = "v1_2" if version == "1.2.0" else "v1_1"
    packaged = files("retailops_ai.source_snapshot").joinpath(directory, name)
    if packaged.is_file():
        return packaged.read_bytes()
    return (
        Path(__file__).absolute().parents[3] / "contracts/source_snapshot" / directory / name
    ).read_bytes()


def contract_document(version: str = VERSION) -> dict[str, Any]:
    return decode_json(resource_bytes("contract.json", version))


def validate_schema(document: Any, name: str, version: str = VERSION) -> None:
    if (
        version == VERSION
        and document.get("source", {}).get("descriptor", {}).get("generator_version") == "0.9.1"
    ):
        name = "forecast_" + name
    try:
        Draft202012Validator(
            decode_json(resource_bytes(name, version)), format_checker=FormatChecker()
        ).validate(document)
    except ValidationError as exc:
        raise SnapshotError("invalid_inventory_snapshot_schema") from exc


def check_lineage(manifest: dict[str, Any], contract: dict[str, Any], allow_truth: bool) -> None:
    source, desc = manifest["source"], manifest["descriptor"]
    parent, qualification = source["descriptor"], desc["qualification"]
    planned = parent["generator_version"] == "0.9.1"
    if planned:
        requested, resolved = source["requested_parameters"], parent["resolved_parameters"]
        declarations = parent.get("forecast_watermarks")
        if (
            requested.get("forecast_plan_version") != "known-forecast-plans-1.0.0"
            or requested.get("forecast_plan_version") != resolved.get("forecast_plan_version")
            or requested.get("forecast_plan_days") != resolved.get("forecast_plan_days")
            or type(requested.get("forecast_plan_days")) is not int
            or not 1 <= requested["forecast_plan_days"] <= 14
            or not isinstance(declarations, dict)
            or set(declarations) != {"daily_demand_observations"}
        ):
            raise SnapshotError("forecast_plan_source_lineage_mismatch")
    elif (
        parent.get("forecast_watermarks") is not None
        or "forecast_plan_days" in parent["resolved_parameters"]
    ):
        raise SnapshotError("forecast_plan_source_lineage_mismatch")
    if (
        source["dataset_id"] != manifest["source_dataset_id"]
        or source["dataset_id"] != desc["parent_source_dataset_id"]
        or source["dataset_id"] != "source-sha256-" + json_sha256(parent)
        or manifest["snapshot_id"] != "snapshot-sha256-" + json_sha256(desc)
        or desc["parent_qualification_id"]
        != "inventory-labels-sha256-" + json_sha256(qualification)
        or qualification["parent_source_id"] != source["dataset_id"]
        or qualification["evaluated_at"] != parent["context"]["evaluated_at"]
        or qualification["grain"] != ["product_id", "stock_location_id", "origin"]
        or not source["facts_ready"]
        or source["source_ready"]
        or source["inventory_ready"]
        or source["model_ready"]
        or not manifest["snapshot_ready"]
        or parent["resolved_parameters"]["business_timezone"] != "UTC"
        or desc["policy_version"] != contract["export_policy_version"]
        or desc["format_version"] != contract["format_version"]
    ):
        raise SnapshotError("inventory_identity_or_lineage_mismatch")
    for stamp in (manifest["generated_at"], source["generated_at"], qualification["evaluated_at"]):
        instant = datetime.fromisoformat(stamp)
        if instant.tzinfo is None or instant.utcoffset() != UTC.utcoffset(instant):
            raise SnapshotError("metadata_timestamp_requires_utc")
    for kind in ("code", "dependency"):
        digest = json_sha256(source["provenance"][kind + "_files"])
        if digest != parent[kind + "_sha256"] or digest != source["provenance"][kind + "_sha256"]:
            raise SnapshotError("source_provenance_mismatch")
    if (
        source["provenance"]["python_version"] != parent["python_version"]
        or json_sha256(manifest["exporter"]["code_files"]) != desc["exporter_code_sha256"]
        or manifest["exporter"]["dependency_sha256"] != desc["dependency_sha256"]
    ):
        raise SnapshotError("exporter_provenance_mismatch")
    specs = dict(contract["fact_tables"])
    if desc["include_evaluation_truth"]:
        if not allow_truth:
            raise SnapshotError("evaluation_truth_requires_explicit_opt_in")
        specs.update(contract["evaluation_truth_tables"])
    names = [t["table"] for t in manifest["tables"]]
    expected = (
        set(contract["fact_tables"])
        | set(contract["evaluation_truth_tables"])
        | set(contract["excluded_source_tables"])
    )
    if (
        names
        != [
            *sorted(contract["fact_tables"]),
            *(
                sorted(contract["evaluation_truth_tables"])
                if desc["include_evaluation_truth"]
                else ()
            ),
        ]
        or set(source["artifacts"]) != expected
        or set(parent["tables"]) != expected
        or desc["tables"] != [{k: t[k] for k in LOGICAL_FIELDS} for t in manifest["tables"]]
    ):
        raise SnapshotError("inventory_table_allowlist_mismatch")
    for name, artifact in source["artifacts"].items():
        relative_path(artifact["path"])
        namespace = (
            "simulation_truth"
            if parent["tables"][name]["data_class"] == "simulation_truth"
            else "facts"
        )
        if artifact["path"] != f"{namespace}/{name}.csv":
            raise SnapshotError("source_artifact_path_mismatch")
    for table in manifest["tables"]:
        spec, identity = specs[table["table"]], parent["tables"][table["table"]]
        if (
            any(table[k] != spec[k] for k in ("schema", "grain", "data_class"))
            or (
                table["partition_source_field"] is not None
                and (
                    source["schema_version"] != "2.8.0"
                    or parent["resolved_parameters"]["profile"] != "ai-07-portfolio-v1"
                    or table["partition_source_field"] != "business_date"
                    or "business_date" not in [c["name"] for c in spec["schema"]]
                )
            )
            or identity["columns"] != [c["name"] for c in spec["schema"]]
            or any(table[k] != identity[k] for k in ("row_count", "grain", "data_class"))
        ):
            raise SnapshotError("inventory_source_projection_mismatch")


def inspect_snapshot(root: Path, allow_truth: bool, limits: Limits) -> Snapshot:
    manifest = read_json(root, "snapshot_manifest.json")
    version = manifest.get("schema_version", "")
    contract = contract_document(version)
    validate_schema(manifest, "snapshot_manifest.schema.json", version)
    check_lineage(manifest, contract, allow_truth)
    refs = (*manifest["metadata_files"], *(r for t in manifest["tables"] for r in t["files"]))
    names = [r["path"] for r in refs]
    for name in names:
        relative_path(name)
    if len(set(names)) != len(names) or {"snapshot_manifest.json", "manifest.sha256"} & set(names):
        raise SnapshotError("duplicate_file_reference")
    if (
        len(names) + 2 > limits.max_files
        or sum(r["bytes"] for r in refs) > limits.max_bytes
        or sum(t["row_count"] for t in manifest["tables"]) > limits.max_rows
    ):
        raise SnapshotError("snapshot_resource_limit")
    _, digest = file_hash(root, "snapshot_manifest.json")
    if read_bytes(root, "manifest.sha256", 128) != (digest + "\n").encode():
        raise SnapshotError("manifest_checksum_mismatch")
    snapshot = Snapshot(manifest, refs, digest)
    inventory(root, snapshot.names)
    return snapshot


def verify_metadata(root: Path, snapshot: Snapshot, required: tuple[str, ...]) -> None:
    manifest = snapshot.manifest
    version = manifest["schema_version"]
    contract = contract_document(version)
    resources = resource_names(version)
    source, desc = manifest["source"], manifest["descriptor"]
    metadata = {r["path"]: r for r in manifest["metadata_files"]}
    expected = {
        "manifests/dataset_manifest.v2.json",
        *("reports/" + n for n in contract["reports"]),
        *("schemas/" + n for n in resources),
        *("schemas/" + t["table"] + ".arrow.json" for t in manifest["tables"]),
    }
    if desc["include_evaluation_truth"]:
        expected |= {"evaluation_truth/qualification/" + n for n in contract["qualification_files"]}
        if version == "1.2.0":
            expected |= {contract["scenario_file"], contract["configuration_file"]}
    if set(metadata) != expected or set(source["reports"]) != set(contract["reports"]):
        raise SnapshotError("metadata_inventory_mismatch")
    if read_json(root, "manifests/dataset_manifest.v2.json") != source:
        raise SnapshotError("source_manifest_copy_mismatch")
    if {p: r["sha256"] for p, r in metadata.items() if p.startswith("schemas/")} != desc["schemas"]:
        raise SnapshotError("schema_fingerprint_mismatch")
    planned = source["descriptor"]["generator_version"] == "0.9.1"
    schema_variants = (False,) if version == "1.2.0" else ((True,) if planned else (False, True))
    if not any(
        all(
            read_bytes(root, "schemas/" + source_name)
            == resource_bytes(
                "forecast_" + packaged_name
                if extended
                and packaged_name
                in ("source_manifest.schema.json", "snapshot_manifest.schema.json")
                else packaged_name,
                version,
            )
            for source_name, packaged_name in resources.items()
        )
        for extended in schema_variants
    ):
        raise SnapshotError("unreviewed_inventory_contract")
    if source["descriptor"]["table_schema_sha256"] != json_sha256(
        contract["source_table_contract"]
    ):
        raise SnapshotError("native_table_contract_fingerprint_mismatch")
    if desc["qualification"]["qualification_schema_sha256"] != json_sha256(
        decode_json(resource_bytes("qualification.schema.json", version))
    ):
        raise SnapshotError("qualification_schema_fingerprint_mismatch")
    for name, report in source["reports"].items():
        ref = metadata["reports/" + name]
        if (ref["sha256"], ref["bytes"]) != (report["sha256"], report["size_bytes"]):
            raise SnapshotError("report_reference_mismatch")
    if metadata["reports/source_report.json"]["sha256"] != desc["source_qualification_sha256"]:
        raise SnapshotError("qualification_fingerprint_mismatch")
    report = read_json(root, "reports/source_report.json")
    checks = report.get("checks", [])
    if (
        report.get("policy_version")
        != contract.get("source_policy_version", "inventory-source-acceptance-1.0.0")
        or report.get("status") != "passed"
        or report.get("facts_ready") is not True
        or report.get("source_ready") is not False
        or report.get("inventory_ready") is not False
        or report.get("model_ready") is not False
        or not isinstance(checks, list)
        or len(checks) != len(contract["hard_gates"])
        or any(not isinstance(c, dict) for c in checks)
        or {c.get("check_id") for c in checks} != set(contract["hard_gates"])
        or any(
            c.get("status") != "passed"
            or c.get("severity") != "hard"
            or c.get("value", 0) != 0
            or c.get("threshold", 0) != 0
            for c in checks
        )
    ):
        raise SnapshotError("failed_source_hard_gate")
    cases = desc["required_use_cases"]
    if (
        not required
        or not cases
        or len(set(required)) != len(required)
        or len(set(cases)) != len(cases)
        or not {*required, *cases} <= set(contract.get("supported_use_cases", USE_CASES))
    ):
        raise SnapshotError("invalid_required_use_cases")
    if desc["include_evaluation_truth"]:
        verify_qualification(root, snapshot)
        if version == "1.2.0":
            verify_anomaly_truth(root, snapshot, contract)


def verify_anomaly_truth(root: Path, snapshot: Snapshot, contract: dict[str, Any]) -> None:
    """Check private lineage and plan schemas; no generator or detector fits here."""
    source = snapshot.manifest["source"]
    for field, name in (
        ("scenario", "scenario_file"),
        ("inventory_configuration", "configuration_file"),
    ):
        ref = source[field]
        if file_hash(root, contract[name]) != (ref["size_bytes"], ref["sha256"]):
            raise SnapshotError("private_anomaly_copy_mismatch")
    scenario = read_json(root, contract["scenario_file"])
    if (
        set(scenario) != {"data_class", "plan", "effects"}
        or scenario["data_class"] != "simulation_truth"
    ):
        raise SnapshotError("invalid_anomaly_truth_document")
    plan = scenario["plan"]
    schema_name = (
        "physical_plan.schema.json"
        if plan.get("contract_version") == "business-physical-anomaly-plan-1.0.0"
        else "anomaly_plan.schema.json"
    )
    validate_schema(plan, schema_name, "1.2.0")
    parent = source["descriptor"]
    if (
        json_sha256(plan) != parent["scenario_plan_sha256"]
        or json_sha256(decode_json(resource_bytes(schema_name, "1.2.0")))
        != parent["scenario_schema_sha256"]
        or json_sha256(read_json(root, contract["configuration_file"]))
        != parent["inventory_configuration_sha256"]
    ):
        raise SnapshotError("private_anomaly_binding_mismatch")


def verify_qualification(root: Path, snapshot: Snapshot) -> None:
    desc = snapshot.manifest["descriptor"]
    prefix = "evaluation_truth/qualification/"
    qmanifest = read_json(root, prefix + "qualification_manifest.json")
    schema = decode_json(
        resource_bytes("qualification.schema.json", snapshot.manifest["schema_version"])
    )
    try:
        Draft202012Validator(schema["manifest"]).validate(qmanifest)
    except ValidationError as exc:
        raise SnapshotError("invalid_qualification_manifest") from exc
    if (
        qmanifest["descriptor"] != desc["qualification"]
        or qmanifest["qualification_id"] != desc["parent_qualification_id"]
    ):
        raise SnapshotError("qualification_parent_mismatch")
    provenance = qmanifest["provenance"]
    if (
        any(
            json_sha256(provenance.get(kind + "_files")) != desc["qualification"][kind + "_sha256"]
            for kind in ("code", "dependency")
        )
        or provenance.get("python_version") != desc["qualification"]["python_version"]
    ):
        raise SnapshotError("qualification_provenance_mismatch")
    for key, name in (
        ("windows", "simulation_truth/inventory_qualified_windows.json"),
        ("report", "qualification_report.json"),
    ):
        ref = qmanifest[key]
        if (
            ref["path"] != name
            or file_hash(root, prefix + name) != (ref["size_bytes"], ref["sha256"])
            or json_sha256(
                read_windows(root, prefix + name)
                if key == "windows"
                else read_json(root, prefix + name)
            )
            != desc["qualification"][key + "_sha256"]
        ):
            raise SnapshotError("qualification_artifact_mismatch")
    windows = read_windows(root, prefix + "simulation_truth/inventory_qualified_windows.json")
    if not isinstance(windows, list) or len(windows) != desc["qualification"]["rows"]:
        raise SnapshotError("qualification_row_count_mismatch")
    validator = Draft202012Validator(schema["window"])
    grains: set[tuple[str, ...]] = set()
    for row in windows:
        try:
            validator.validate(row)
        except ValidationError as exc:
            raise SnapshotError("invalid_qualified_window") from exc
        grain = tuple(row[k] for k in desc["qualification"]["grain"])
        if (
            grain in grains
            or row["evaluated_at"] != desc["qualification"]["evaluated_at"]
            or (row["status"] == "evaluable") != (row["incident_stockout"] is not None)
        ):
            raise SnapshotError("invalid_qualification_grain_or_status")
        grains.add(grain)
    report = read_json(root, prefix + "qualification_report.json")
    statuses = Counter(row["status"] for row in windows)
    reasons = Counter(row["reason"] for row in windows if row["reason"] is not None)
    positive = sum(
        row["status"] == "evaluable" and row["incident_stockout"] == 1 for row in windows
    )
    negative = sum(
        row["status"] == "evaluable" and row["incident_stockout"] == 0 for row in windows
    )
    expected = {
        "rows": len(windows),
        "parent_source_id": snapshot.source_id,
        "positive_labels": positive,
        "negative_labels": negative,
        "statuses": {
            key: statuses[key] for key in ("evaluable", "already_stockout", "not_evaluable")
        },
        "reasons": dict(reasons),
        "source_facts_ready": True,
        "label_qualification": "qualified" if positive and negative else "not_evaluable",
        "source_ready": False,
        "inventory_ready": False,
        "model_ready": False,
    }
    if any(report.get(key) != value for key, value in expected.items()):
        raise SnapshotError("qualification_report_aggregate_mismatch")


def read_windows(root: Path, name: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(
            read_bytes(root, name), object_pairs_hook=unique_keys, parse_constant=nonfinite
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise SnapshotError("invalid_qualification_json") from exc
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise SnapshotError("qualification_array_required")
    return value
