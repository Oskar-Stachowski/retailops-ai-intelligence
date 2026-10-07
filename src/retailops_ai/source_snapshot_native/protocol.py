"""Versioned metadata checks derived from the handoff, without producer code."""

from __future__ import annotations

from dataclasses import dataclass
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
    read_bytes,
    read_json,
    relative_path,
)

LOGICAL_FIELDS = (
    "table",
    "data_class",
    "row_count",
    "content_sha256",
    "grain",
    "date_range",
    "field_ranges",
    "schema",
)
BASE_SCHEMAS = {
    "ai_snapshot.v1.schema.json",
    "source_dataset_manifest.v2.schema.json",
    "retail_dimensions.v1.schema.json",
    "retail_pricing.v1.schema.json",
    "retail_demand.v1.schema.json",
    "retail_returns.v1.schema.json",
    "observation_history.v1.schema.json",
    "retailops_seed_dataset.contract.json",
}
REPORTS = {
    "dataset_manifest.json",
    "quality_report.json",
    "realism_report.json",
    "realism_report.md",
    "dimensions_report.json",
    "pricing_report.json",
    "pricing_report.md",
    "demand_report.json",
    "demand_report.md",
    "returns_report.json",
    "returns_report.md",
    "source_report.json",
    "source_report.md",
}
GATES = {
    "legacy:" + key
    for key in (
        "primary_keys_are_unique",
        "sales_reference_products",
        "orders_reference_stores",
        "order_items_reference_orders_and_products",
        "pricing_references_products",
        "inventory_references_products_and_warehouses",
        "stock_movements_reference_products_and_warehouses",
        "returns_reference_orders_order_items_and_products",
        "operational_records_reference_valid_entities",
        "sales_values_are_positive",
        "inventory_and_returns_are_non_negative",
        "order_totals_match_order_items",
        "returns_do_not_exceed_order_item_quantity",
        "date_windows_are_ordered",
        "sales_data_quality_statuses_are_known",
    )
} | {
    "dimension_schema_pk_sku",
    "selling_stock_channel_region",
    "assignment_routing_versions",
    "catalog_lifecycle_assortment",
    "calendar_exact_coverage",
    "category_season_coverage",
    "legacy_adapter_consistency",
    "sales_active_open_known_routing",
    "pricing_schema_versions_scope",
    "known_price_coverage_and_priority",
    "transaction_price_reconciliation",
    "realized_price_aggregates",
    "legacy_pricing_adapter",
    "promotion_truth_direction",
    "demand_schema",
    "daily_panel_coverage",
    "daily_transaction_aggregation",
    "basket_sku_totals",
    "daily_demand_budget",
    "daily_source_completeness",
    "returns_schema",
    "return_window_policy",
    "transaction_chronology",
    "return_reference_and_window",
    "return_quantity_and_refunds",
    "return_snapshot_reconciliation",
    "return_tail_completeness",
    "fact_schema_without_simulation",
    "simulation_parameters_schema_coverage",
    "feature_fact_projection",
    "append_only_observation_history",
}


def resource_bytes(name: str) -> bytes:
    resource = files("retailops_ai.source_snapshot").joinpath(name)
    if resource.is_file():
        return resource.read_bytes()
    root = Path(__file__).absolute().parents[3]
    path = root / (
        "uv.lock" if name == "dependencies.lock" else "contracts/source_snapshot/v1/" + name
    )
    return path.read_bytes()


def contract_document() -> dict[str, Any]:
    return decode_json(resource_bytes("contract.json"))


@dataclass(frozen=True)
class Limits:
    max_bytes: int = 2 * 1024**3
    max_files: int = 10000
    max_rows: int = 20000000
    batch_rows: int = 8192

    def __post_init__(self) -> None:
        if (
            min(self.max_bytes, self.max_files, self.max_rows) < 1
            or not 1 <= self.batch_rows <= 65536
        ):
            raise SnapshotError("invalid_import_limits")


@dataclass(frozen=True)
class Snapshot:
    manifest: dict[str, Any]
    references: tuple[dict[str, Any], ...]
    manifest_sha256: str

    @property
    def source_id(self) -> str:
        return str(self.manifest["source_dataset_id"])

    @property
    def snapshot_id(self) -> str:
        return str(self.manifest["snapshot_id"])

    @property
    def names(self) -> set[str]:
        return {"snapshot_manifest.json", "manifest.sha256", *(r["path"] for r in self.references)}


def check_lineage(manifest: dict[str, Any], contract: dict[str, Any], allow_truth: bool) -> None:
    source, descriptor = manifest["source"], manifest["descriptor"]
    source_descriptor = source["descriptor"]
    if (
        manifest["source_repository"] != contract["producer"]
        or source_descriptor["owner"] != contract["producer"]
        or manifest["source_dataset_id"] != "source-sha256-" + json_sha256(source_descriptor)
        or source["dataset_id"] != manifest["source_dataset_id"]
        or descriptor["parent_source_dataset_id"] != source["dataset_id"]
        or source_descriptor["parent_ids"]
        or manifest["snapshot_id"] != "snapshot-sha256-" + json_sha256(descriptor)
    ):
        raise SnapshotError("identity_or_lineage_mismatch")
    if (
        descriptor["policy_version"] != contract["export_policy_version"]
        or descriptor["format_version"] != contract["format_version"]
        or source_descriptor["versions"]["canonicalization"] != contract["canonicalization_version"]
        or source_descriptor["schema_version"] != source["schema_version"]
        or source_descriptor["resolved_parameters"]["business_timezone"] != "UTC"
        or not manifest["snapshot_ready"]
        or not source["source_ready"]
        or source["inventory_ready"]
    ):
        raise SnapshotError("unsupported_policy_or_unqualified_source")
    provenance = source["provenance"]
    for stamp in [
        manifest["generated_at"],
        source["generated_at"],
        *(w["as_of_time"] for w in source["watermarks"].values()),
    ]:
        instant = datetime.fromisoformat(stamp)
        if instant.tzinfo is None or instant.utcoffset() != UTC.utcoffset(instant):
            raise SnapshotError("metadata_timestamp_requires_utc")
    parameters = source_descriptor["resolved_parameters"]
    if any(
        value is not None and value != parameters[key]
        for key, value in source["requested_parameters"].items()
        if key in parameters
    ):
        raise SnapshotError("requested_resolved_parameter_mismatch")
    for kind in ("code", "dependency"):
        digest = json_sha256(provenance[kind + "_files"])
        if digest != provenance[kind + "_sha256"] or digest != source_descriptor[kind + "_sha256"]:
            raise SnapshotError("source_provenance_mismatch")
    if (
        provenance["python_version"] != source_descriptor["python_version"]
        or json_sha256(manifest["exporter"]["code_files"]) != descriptor["exporter_code_sha256"]
        or manifest["exporter"]["dependency_sha256"] != descriptor["dependency_sha256"]
    ):
        raise SnapshotError("exporter_provenance_mismatch")
    specs = dict(contract["fact_tables"])
    if descriptor["include_evaluation_truth"]:
        if not allow_truth:
            raise SnapshotError("evaluation_truth_requires_explicit_opt_in")
        specs.update(contract["evaluation_truth_tables"])
    tables = manifest["tables"]
    names = [t["table"] for t in tables]
    if len(set(names)) != len(names) or set(names) != set(specs):
        raise SnapshotError("table_allowlist_mismatch")
    logical = [{k: t[k] for k in LOGICAL_FIELDS} for t in tables]
    if descriptor["tables"] != logical:
        raise SnapshotError("snapshot_descriptor_mismatch")
    artifacts = source["artifacts"]
    artifact_names = [a["table"] for a in artifacts]
    expected_source = (
        set(contract["fact_tables"])
        | set(contract["evaluation_truth_tables"])
        | set(contract["excluded_source_tables"])
    )
    if (
        len(set(artifact_names)) != len(artifact_names)
        or set(artifact_names) != expected_source
        or set(source_descriptor["tables"]) != expected_source
    ):
        raise SnapshotError("source_table_inventory_mismatch")
    for artifact in artifacts:
        relative_path(artifact["path"])
        identity = source_descriptor["tables"][artifact["table"]]
        if any(artifact[k] != v for k, v in identity.items()):
            raise SnapshotError("source_artifact_identity_mismatch")
    selected = [a for a in artifacts if a["table"] in specs]
    if [a["table"] for a in selected] != names:
        raise SnapshotError("source_projection_order_mismatch")
    for table, artifact in zip(tables, selected, strict=True):
        spec = specs[table["table"]]
        if any(table[k] != spec[k] for k in ("schema", "grain", "data_class")):
            raise SnapshotError("schema_grain_classification_mismatch")
        if artifact["columns"] != [c["name"] for c in table["schema"]] or any(
            table[k] != artifact[k] for k in LOGICAL_FIELDS if k not in {"schema"}
        ):
            raise SnapshotError("source_projection_mismatch")


def inspect_snapshot(root: Path, allow_truth: bool, limits: Limits) -> Snapshot:
    contract = contract_document()
    manifest = read_json(root, "snapshot_manifest.json")
    if manifest.get("schema_version") in {"1.1.0", "1.2.0"}:
        from retailops_ai.source_snapshot.inventory_protocol import (
            inspect_snapshot as inspect_inventory,
        )

        return inspect_inventory(root, allow_truth, limits)
    if (
        manifest.get("schema_version") not in contract["supported_snapshot_versions"]
        or manifest.get("source", {}).get("schema_version")
        not in contract["supported_source_versions"]
    ):
        raise SnapshotError("unsupported_snapshot_or_source_version")
    try:
        Draft202012Validator(
            decode_json(resource_bytes("snapshot_manifest.schema.json")),
            format_checker=FormatChecker(),
        ).validate(manifest)
    except ValidationError as exc:
        raise SnapshotError("invalid_snapshot_manifest_schema") from exc
    check_lineage(manifest, contract, allow_truth)
    references = (*manifest["metadata_files"], *(r for t in manifest["tables"] for r in t["files"]))
    names = [r["path"] for r in references]
    for name in names:
        relative_path(name)
    if len(set(names)) != len(names) or {"snapshot_manifest.json", "manifest.sha256"} & set(names):
        raise SnapshotError("duplicate_file_reference")
    if (
        len(names) + 2 > limits.max_files
        or sum(r["bytes"] for r in references) > limits.max_bytes
        or sum(t["row_count"] for t in manifest["tables"]) > limits.max_rows
    ):
        raise SnapshotError("snapshot_resource_limit")
    _, digest = file_hash(root, "snapshot_manifest.json")
    if read_bytes(root, "manifest.sha256", 128) != (digest + "\n").encode("ascii"):
        raise SnapshotError("manifest_checksum_mismatch")
    snapshot = Snapshot(manifest, references, digest)
    inventory(root, snapshot.names)
    return snapshot


def verify_metadata(root: Path, snapshot: Snapshot, required: tuple[str, ...]) -> None:
    manifest = snapshot.manifest
    if manifest["schema_version"] in {"1.1.0", "1.2.0"}:
        from retailops_ai.source_snapshot.inventory_protocol import (
            verify_metadata as verify_inventory,
        )

        verify_inventory(root, snapshot, required)
        return
    source, descriptor = manifest["source"], manifest["descriptor"]
    metadata = {r["path"]: r for r in manifest["metadata_files"]}
    schema_names = BASE_SCHEMAS | {t["table"] + ".arrow.json" for t in manifest["tables"]}
    if descriptor["include_evaluation_truth"]:
        schema_names |= {"retail_simulation.v1.schema.json"}
    expected = {
        "manifests/dataset_manifest.v2.json",
        *("reports/" + r for r in REPORTS),
        *("schemas/" + s for s in schema_names),
    }
    if set(metadata) != expected or {r["path"] for r in source["reports"]} != REPORTS:
        raise SnapshotError("metadata_inventory_mismatch")
    if read_json(root, "manifests/dataset_manifest.v2.json") != source:
        raise SnapshotError("source_manifest_copy_mismatch")
    if {p: r["sha256"] for p, r in metadata.items() if p.startswith("schemas/")} != descriptor[
        "schemas"
    ]:
        raise SnapshotError("schema_fingerprint_mismatch")
    if read_bytes(root, "schemas/ai_snapshot.v1.schema.json") != resource_bytes(
        "snapshot_manifest.schema.json"
    ):
        raise SnapshotError("unreviewed_snapshot_schema")
    for report in source["reports"]:
        ref = metadata["reports/" + report["path"]]
        if ref["sha256"] != report["sha256"] or ref["bytes"] != report["size_bytes"]:
            raise SnapshotError("report_reference_mismatch")
    if (
        metadata["reports/source_report.json"]["sha256"]
        != descriptor["source_qualification_sha256"]
    ):
        raise SnapshotError("qualification_fingerprint_mismatch")
    report = read_json(root, "reports/source_report.json")
    checks = report.get("checks", [])
    if (
        report.get("policy_version") != "forecast-source-acceptance-1.1.0"
        or report.get("status") != "passed"
        or report.get("source_ready") is not True
        or report.get("inventory_ready") is not False
        or report.get("target_type") != "observed_sales_units"
        or not isinstance(checks, list)
        or len(checks) != len(GATES)
        or any(not isinstance(c, dict) for c in checks)
        or {c.get("check_id") for c in checks} != GATES
        or any(
            c.get("status") != "passed"
            or c.get("severity") != "hard"
            or c.get("value") != 0
            or c.get("threshold") != 0
            for c in checks
        )
    ):
        raise SnapshotError("failed_source_hard_gate")
    readiness = report.get("readiness", {})
    if not isinstance(readiness, dict) or any(not isinstance(v, dict) for v in readiness.values()):
        raise SnapshotError("invalid_readiness_document")
    if any(readiness.get(k, {}).get("status") != v for k, v in source["readiness"].items()):
        raise SnapshotError("source_readiness_mismatch")
    cases = descriptor["required_use_cases"]
    if (
        not cases
        or len(set(cases)) != len(cases)
        or not required
        or len(set(required)) != len(required)
    ):
        raise SnapshotError("invalid_required_use_cases")
    for case in {*cases, *required, "forecast_source"}:
        if readiness.get(case, {}).get("status") != "ready":
            raise SnapshotError("required_use_case_not_ready")
