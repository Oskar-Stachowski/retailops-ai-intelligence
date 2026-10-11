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
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any

KEY = ("product_id", "selling_location_id", "channel", "currency")


def complete_ordinary_windows(
    tables: dict[str, Any], window: dict[str, str]
) -> list[dict[str, Any]]:
    """Use the verified native day universe, retaining gaps as unknown truth."""
    start, end = date.fromisoformat(window["start"]), date.fromisoformat(window["end"])
    windows: list[dict[str, Any]] = []

    def stamp(value: str) -> datetime:
        result = datetime.fromisoformat(value)
        if result.utcoffset() != timedelta(0):
            raise ValueError("campaign_ordinary_truth_native_utc_required")
        return result

    def append(event: str, key: tuple[str, ...], first: date, last: date, known: datetime) -> None:
        # These are the unchanged native evaluator maturity delays, not a claim
        # that an event-time watermark proves return cohort completeness.
        maturity = datetime.combine(last + timedelta(days=1), datetime.min.time(), UTC)
        maturity += timedelta(hours=24 if event == "sale_completed" else 72)
        windows.append(
            {
                "event_type": event,
                **dict(zip(KEY, key, strict=True)),
                "window": {"start": first.isoformat(), "end": last.isoformat()},
                "available_at": max(known, maturity).isoformat().replace("+00:00", "Z"),
            }
        )
        if len(windows) > 10000:
            raise ValueError("campaign_ordinary_truth_window_budget")

    sales: dict[tuple[str, ...], list[tuple[date, datetime]]] = {}
    for row in tables["daily_demand_observations"]:
        day = date.fromisoformat(row["business_date"])
        if (
            start <= day <= end
            and row["source_data_complete"] is True
            and row["quality_status"] == "valid"
        ):
            key = tuple(row[k] for k in KEY)
            sales.setdefault(key, []).append((day, stamp(row["available_at"])))
    for key, days in sorted(sales.items()):
        ordered = sorted(days)
        first, known = ordered[0]
        previous = first
        for day, available in ordered[1:]:
            if day <= previous:
                raise ValueError("campaign_ordinary_truth_duplicate_native_day")
            if day != previous + timedelta(days=1):
                append("sale_completed", key, first, previous, known)
                first, known = day, available
            else:
                known = max(known, available)
            previous = day
        append("sale_completed", key, first, previous, known)

    products = {row["id"]: row for row in tables["product_catalog"]}
    policies = {(row["category_id"], row["channel"]): row for row in tables["return_policies"]}
    purchases: dict[tuple[str, ...], tuple[date, datetime]] = {}
    for row in tables["inventory_sales"]:
        key = tuple(row[k] for k in KEY)
        day, available = stamp(row["sold_at"]).date(), stamp(row["available_at"])
        previous_purchase = purchases.get(key)
        purchases[key] = (
            (max(day, previous_purchase[0]), min(available, previous_purchase[1]))
            if previous_purchase
            else (day, available)
        )
    for key, (last_purchase, first_available) in sorted(purchases.items()):
        policy = policies[products[key[0]]["category_id"], key[2]]
        finish = min(end, last_purchase + timedelta(days=policy["window_days"]))
        if finish < start:
            continue
        closed = datetime.combine(finish + timedelta(days=1), datetime.min.time(), UTC)
        known = max(
            closed + timedelta(days=policy["max_ingestion_delay_days"]),
            first_available,
            stamp(policy["known_at"]),
        )
        append("return_completed", key, start, finish, known)
    windows.sort(
        key=lambda item: (item["event_type"], *(item[k] for k in KEY), item["window"]["start"])
    )
    total = sum(
        (date.fromisoformat(w["window"]["end"]) - date.fromisoformat(w["window"]["start"])).days + 1
        for w in windows
    )
    if not windows or total > 1000000:
        raise ValueError("campaign_ordinary_truth_census_budget")
    return windows


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


def verify_ordinary_source(
    source: Path, dataset: Path, request: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Actually replay all facts, private truth, reports and the native file inventory."""
    io, provenance = _producer(source, request)
    initial = io.load_json(
        io.safe_file(dataset, "dataset_manifest.v2.json", limit=io.MAX_METADATA_BYTES)
    )
    references = [
        *initial["artifacts"].values(),
        *initial["reports"].values(),
        initial["inventory_configuration"],
    ]
    if (
        initial["schema_version"] != "2.7.0"
        or sum(r["size_bytes"] for r in references) > request["max_source_bytes"]
        or sum(r["row_count"] for r in initial["descriptor"]["tables"].values())
        > request["max_source_rows"]
    ):
        raise ValueError("campaign_ordinary_truth_source_budget_or_kind")
    # This call independently recomputes every report from complete source data.
    # Hashes and an absent scenario field alone are explicitly insufficient.
    tables, manifest = io.read_source_dataset(dataset)
    if manifest != initial:
        raise ValueError("campaign_anomaly_truth_source_changed")
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
    # The native reader returns typed inventory rows but canonical CSV strings
    # for commerce. Use the producer's own scalar conversion: a nonempty
    # "false" string is not evidence of completeness, and policy days are ints.
    canonical_cell = importlib.import_module("data.generator.identity").canonical_cell
    window_tables = {
        **tables,
        "daily_demand_observations": (
            {
                **row,
                "source_data_complete": canonical_cell(
                    "source_data_complete", row["source_data_complete"]
                ),
            }
            for row in tables["daily_demand_observations"]
        ),
        "return_policies": (
            {
                **row,
                "window_days": canonical_cell("window_days", row["window_days"]),
                "max_ingestion_delay_days": canonical_cell(
                    "max_ingestion_delay_days", row["max_ingestion_delay_days"]
                ),
            }
            for row in tables["return_policies"]
        ),
    }
    windows = complete_ordinary_windows(window_tables, request["window"])
    del window_tables
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
        "window": request["window"],
        "complete_windows_sha256": _digest(windows),
        "complete_window_count": len(windows),
        "complete_observation_count": sum(
            (date.fromisoformat(w["window"]["end"]) - date.fromisoformat(w["window"]["start"])).days
            + 1
            for w in windows
        ),
        "population": "all_native_sales_days_and_parent_purchase_return_tail",
        "source_scenario_sha256": None,
        "native_reader": "data.inventory.source_dataset_io.read_source_dataset",
        "complete_source_and_reports_replayed": True,
        "quality_qualified": False,
    }, windows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    # This helper imports only the standard library in the producer environment.
    from retailops_ai.evaluation_campaign.campaign_generation_worker import (
        read,
        worker_peak_rss_bytes,
        write,
    )

    started = perf_counter()
    request = read(args.root / "request.json")
    verified, windows = verify_ordinary_source(args.source, args.dataset, request)
    usage = resource.getrusage(resource.RUSAGE_SELF)
    child = resource.getrusage(resource.RUSAGE_CHILDREN)
    write(args.root / "ordinary-source-verification.json", verified)
    write(args.root / "ordinary-source-windows.json", {"complete_windows": windows})
    write(
        args.root / "truth-worker-resources.json",
        {
            "wall_seconds": perf_counter() - started,
            "cpu_seconds": usage.ru_utime + usage.ru_stime + child.ru_utime + child.ru_stime,
            "worker_peak_rss_bytes": worker_peak_rss_bytes(),
        },
    )


if __name__ == "__main__":
    main()
