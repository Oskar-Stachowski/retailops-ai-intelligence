"""Review the pinned v1 handoff without a producer checkout, generator, DB or Arrow."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "contracts/source_snapshot/v1"
FIXTURE = ROOT / "data/fixtures/ai-smoke-v1"
MAX_METADATA_BYTES = 4 * 1024 * 1024
MAX_FIXTURE_BYTES = 5 * 1024 * 1024
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


def unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def nonfinite(value: str) -> NoReturn:
    raise ValueError("nonfinite_json_number: " + value)


def relative_path(relative: str) -> PurePosixPath:
    name = PurePosixPath(relative)
    if (
        not relative
        or name.is_absolute()
        or "\\" in relative
        or name.as_posix() != relative
        or any(p in {".", ".."} for p in relative.split("/"))
    ):
        raise ValueError("unsafe_artifact_path")
    return name


def safe_file(root: Path, relative: str) -> Path:
    relative_path(relative)
    path = root / relative
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("symlink_artifact")
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("regular_artifact_required")
    return path


def read_json(path: Path) -> dict[str, Any]:
    if path.stat().st_size > MAX_METADATA_BYTES:
        raise ValueError("metadata_size_limit")
    value = json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys, parse_constant=nonfinite
    )
    if not isinstance(value, dict):
        raise ValueError("json_object_required")
    return value


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_manifest(manifest: dict[str, Any], contract: dict[str, Any]) -> None:
    source = manifest.get("source", {})
    if (
        manifest.get("schema_version") not in contract["supported_snapshot_versions"]
        or source.get("schema_version") not in contract["supported_source_versions"]
    ):
        raise ValueError("unsupported_snapshot_or_source_version")
    Draft202012Validator(read_json(safe_file(REGISTRY, "snapshot_manifest.schema.json"))).validate(
        manifest
    )
    for ref in [
        *manifest["metadata_files"],
        *(ref for table in manifest["tables"] for ref in table["files"]),
    ]:
        relative_path(ref["path"])
    descriptor = manifest["descriptor"]
    source_descriptor = source["descriptor"]
    if (
        manifest["source_dataset_id"] != "source-sha256-" + canonical_sha256(source_descriptor)
        or manifest["snapshot_id"] != "snapshot-sha256-" + canonical_sha256(descriptor)
        or source["dataset_id"] != manifest["source_dataset_id"]
        or descriptor["parent_source_dataset_id"] != source["dataset_id"]
        or source_descriptor["parent_ids"]
    ):
        raise ValueError("invalid_identity_or_lineage")
    if (
        descriptor["policy_version"] != contract["export_policy_version"]
        or descriptor["format_version"] != contract["format_version"]
        or source_descriptor["versions"]["canonicalization"] != contract["canonicalization_version"]
    ):
        raise ValueError("unsupported_policy_or_canonicalization")
    tables = manifest["tables"]
    names = [table["table"] for table in tables]
    if (
        len(names) != len(set(names))
        or set(names) != set(contract["fact_tables"])
        or descriptor["include_evaluation_truth"]
    ):
        raise ValueError("fixture_requires_exact_fact_allowlist")
    if descriptor["tables"] != [{key: table[key] for key in LOGICAL_FIELDS} for table in tables]:
        raise ValueError("logical_descriptor_mismatch")
    source_artifacts = {a["table"]: a for a in source["artifacts"]}
    for table in tables:
        expected = contract["fact_tables"][table["table"]]
        if any(table[key] != expected[key] for key in ("data_class", "schema", "grain")):
            raise ValueError("schema_grain_classification_mismatch")
        if any(
            table[key] != source_artifacts[table["table"]][key]
            for key in (
                "data_class",
                "content_sha256",
                "row_count",
                "grain",
                "date_range",
                "field_ranges",
            )
        ):
            raise ValueError("source_projection_mismatch")


def verify_fixture(package: Path = FIXTURE) -> dict[str, Any]:
    if {path.name for path in package.iterdir()} != {
        "contract.json",
        "expected_manifest.json",
        "snapshot",
    }:
        raise ValueError("missing_or_extra_artifact")
    contract = read_json(safe_file(REGISTRY, "contract.json"))
    expected = read_json(safe_file(REGISTRY, "expected_manifest.json"))
    if read_json(safe_file(package, "contract.json")) != contract:
        raise ValueError("unreviewed_handoff_contract")
    if read_json(safe_file(package, "expected_manifest.json")) != expected:
        raise ValueError("unreviewed_expected_manifest")
    snapshot = package / "snapshot"
    manifest_file = safe_file(snapshot, "snapshot_manifest.json")
    manifest = read_json(manifest_file)
    validate_manifest(manifest, contract)
    names = {ref["path"] for ref in expected["files"]}
    inventory = set()
    for path in package.rglob("*"):
        if path.is_symlink() or (not path.is_dir() and not stat.S_ISREG(path.stat().st_mode)):
            raise ValueError("symlink_or_special_artifact")
        if path.is_file():
            inventory.add(path.relative_to(package).as_posix())
    if inventory != {"contract.json", "expected_manifest.json", *("snapshot/" + n for n in names)}:
        raise ValueError("missing_or_extra_artifact")
    total = 0
    for ref in expected["files"]:
        path = safe_file(snapshot, ref["path"])
        total += path.stat().st_size
        if path.stat().st_size != ref["bytes"] or file_sha256(path) != ref["sha256"]:
            raise ValueError("byte_checksum_or_size_mismatch")
    if total > MAX_FIXTURE_BYTES:
        raise ValueError("fixture_budget_exceeded")
    digest = file_sha256(manifest_file)
    if (
        digest != expected["snapshot_manifest_sha256"]
        or safe_file(snapshot, "manifest.sha256").read_text(encoding="ascii") != digest + "\n"
    ):
        raise ValueError("manifest_checksum_mismatch")
    if (
        manifest["snapshot_id"] != expected["snapshot_id"]
        or manifest["source_dataset_id"] != expected["source_dataset_id"]
    ):
        raise ValueError("expected_identity_mismatch")
    report = read_json(safe_file(snapshot, "reports/source_report.json"))
    statuses = {key: value["status"] for key, value in report["readiness"].items()}
    if (
        report["status"] != "passed"
        or len(report["checks"]) != 46
        or any(check["status"] != "passed" for check in report["checks"])
        or statuses != expected["readiness"]
        or report["inventory_ready"]
    ):
        raise ValueError("unqualified_source_or_readiness")
    return {
        "status": "passed",
        "handoff_contract_version": contract["contract_version"],
        "source_dataset_id": manifest["source_dataset_id"],
        "snapshot_id": manifest["snapshot_id"],
        "tables": len(manifest["tables"]),
        "rows": sum(t["row_count"] for t in manifest["tables"]),
        "snapshot_files": len(names),
        "snapshot_bytes": total,
        "typed_import": "not_implemented",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-dir", type=Path, default=FIXTURE)
    args = parser.parse_args()
    print(json.dumps(verify_fixture(args.fixture_dir), indent=2))


if __name__ == "__main__":
    main()
