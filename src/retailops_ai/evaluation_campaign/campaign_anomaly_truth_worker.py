"""Offline complete ordinary Source verification under its pinned interpreter.

Only the audited campaign controller may authorize a Project invocation. This
worker does not accept public snapshots or absence of a scenario as clean proof.
"""

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import resource
import shutil
import subprocess
import sys
from pathlib import Path
from time import perf_counter
from typing import Any


def _digest(value: Any) -> str:
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def _producer(source: Path, request: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    executable = shutil.which("git")
    if executable is None:
        raise ValueError("campaign_anomaly_truth_git_unavailable")
    for arguments, expected in (
        (("rev-parse", "HEAD"), request["producer_commit"]),
        (("diff", "HEAD", "--name-only"), ""),
        (("ls-files", "--others", "--exclude-standard", "--", "data"), ""),
    ):
        actual = subprocess.check_output(  # noqa: S603 - fixed read-only Git arguments, no shell
            [executable, "-C", str(source), *arguments], text=True, timeout=30
        ).strip()
        if actual != expected:
            raise ValueError("campaign_anomaly_truth_dirty_or_wrong_producer")
    for relative in ("services/api/requirements.txt", "data/requirements-parquet.txt"):
        for line in (source / relative).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            package, version = line.split("==")
            if importlib.metadata.version(package.split("[")[0]) != version:
                raise ValueError("campaign_anomaly_truth_installed_dependency_mismatch")
    sys.path.insert(0, str(source))
    io = importlib.import_module("data.inventory.source_dataset_io")
    identity = importlib.import_module("data.generator.identity")
    provenance = identity.code_provenance(io.fingerprint())
    if (
        provenance["git_commit"] != request["producer_commit"]
        or provenance["code_state"] != "clean"
        or provenance["dependency_sha256"] != request["producer_lock_sha256"]
        or hashlib.sha256((source / "data/requirements-parquet.txt").read_bytes()).hexdigest()
        != request["exporter_lock_sha256"]
    ):
        raise ValueError("campaign_anomaly_truth_producer_or_lock_mismatch")
    return io, provenance


def verify_ordinary_source(source: Path, dataset: Path, request: dict[str, Any]) -> dict[str, Any]:
    """Actually replay all facts, private truth, reports and the native file inventory."""
    io, provenance = _producer(source, request)
    # This call independently recomputes every report from complete source data.
    # Hashes and an absent scenario field alone are explicitly insufficient.
    tables, manifest = io.read_source_dataset(dataset)
    descriptor = manifest["descriptor"]
    if (
        manifest["schema_version"] != "2.7.0"
        or "scenario" in manifest
        or "scenario_plan_sha256" in descriptor
        or manifest["dataset_id"] != request["source_dataset_id"]
        or manifest["dataset_id"] != "source-sha256-" + _digest(descriptor)
        or manifest["provenance"] != provenance
        or not manifest["facts_ready"]
        or descriptor["resolved_parameters"] != request["resolved_parameters"]
        or set(tables) != set(descriptor["tables"])
        or any(
            len(tables[name]) != spec["row_count"] for name, spec in descriptor["tables"].items()
        )
    ):
        raise ValueError("campaign_anomaly_truth_full_ordinary_source_binding")
    # The native reader verified the complete report bytes and all hard gates.
    # Preserve that actual report identity, not a replacement clean-label file.
    report_ref = manifest["reports"]["source_report.json"]
    report_path = io.verify_artifact(dataset, report_ref, "source_report.json")
    report = io.load_json(report_path)
    checks = {row["check_id"]: row for row in report["checks"]}
    required = {
        "inventory_daily_demand_conservation",
        "private_supplier_realization",
        "known_fact_reorder_policy",
        "private_simulation_parameters",
        "inventory_graph_and_projection",
        "commerce_inventory_parity",
    }
    if (
        report["facts_ready"] is not True
        or report["status"] != "passed"
        or len(checks) != len(report["checks"])
        or not required <= checks.keys()
        or any(row["status"] != "passed" for row in checks.values())
        or {"anomaly_process_replay", "anomaly_effect_reconciliation"} & checks.keys()
    ):
        raise ValueError("campaign_anomaly_truth_ordinary_report_replay")
    table_count, row_count = len(tables), sum(len(rows) for rows in tables.values())
    del tables
    references = [
        *manifest["artifacts"].values(),
        *manifest["reports"].values(),
        manifest["inventory_configuration"],
    ]
    for reference in references:
        io.verify_artifact(dataset, reference, reference["path"])
    if {p.relative_to(dataset).as_posix() for p in dataset.rglob("*") if p.is_file()} != {
        "dataset_manifest.v2.json",
        *(r["path"] for r in references),
    }:
        raise ValueError("campaign_anomaly_truth_source_inventory_changed")
    manifest_raw = io.safe_file(dataset, "dataset_manifest.v2.json").read_bytes()
    if io.load_json(dataset / "dataset_manifest.v2.json") != manifest:
        raise ValueError("campaign_anomaly_truth_source_changed")
    _, final_provenance = _producer(source, request)
    if final_provenance != provenance:
        raise ValueError("campaign_anomaly_truth_producer_changed")
    return {
        "version": "ai09-complete-ordinary-source-verification-1.0.0",
        "source_dataset_id": manifest["dataset_id"],
        "source_schema_version": "2.7.0",
        "source_manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "source_descriptor_sha256": _digest(descriptor),
        "source_report_sha256": report_ref["sha256"],
        "source_table_inventory_sha256": _digest(descriptor["tables"]),
        "source_tables": table_count,
        "source_rows": row_count,
        "producer_commit": provenance["git_commit"],
        "producer_code_sha256": provenance["code_sha256"],
        "producer_lock_sha256": provenance["dependency_sha256"],
        "exporter_lock_sha256": request["exporter_lock_sha256"],
        "producer_python_version": provenance["python_version"],
        "resolved_parameters": descriptor["resolved_parameters"],
        "source_scenario_sha256": None,
        "native_reader": "data.inventory.source_dataset_io.read_source_dataset",
        "complete_source_and_reports_replayed": True,
        "quality_qualified": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    # This helper imports only the standard library in the producer environment.
    from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write

    started = perf_counter()
    verified = verify_ordinary_source(args.source, args.dataset, read(args.root / "request.json"))
    usage = resource.getrusage(resource.RUSAGE_SELF)
    child = resource.getrusage(resource.RUSAGE_CHILDREN)
    scale = 1 if sys.platform == "darwin" else 1024
    write(args.root / "ordinary-source-verification.json", verified)
    write(
        args.root / "truth-worker-resources.json",
        {
            "wall_seconds": perf_counter() - started,
            "cpu_seconds": usage.ru_utime + usage.ru_stime + child.ru_utime + child.ru_stime,
            "conservative_worker_tree_peak_rss_bytes": int(
                (usage.ru_maxrss + child.ru_maxrss) * scale
            ),
        },
    )


if __name__ == "__main__":
    main()
