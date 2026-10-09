"""Complete paired Source replay under the producer interpreter, offline only.

The enclosing controller must reserve both-parent access and verify final
selection before starting this worker. This module does not grant that access.
"""

import argparse
import gc
import hashlib
import importlib
import resource
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any

REQUIRED_CHECKS = frozenset(
    {
        "inventory_daily_demand_conservation",
        "private_supplier_realization",
        "known_fact_reorder_policy",
        "private_simulation_parameters",
        "inventory_graph_and_projection",
        "commerce_inventory_parity",
    }
)
ANOMALY_CHECKS = frozenset({"anomaly_process_replay", "anomaly_effect_reconciliation"})
PAIRED_FIELDS = (
    "resolved_parameters",
    "inventory_configuration_sha256",
    "context",
    "code_sha256",
    "dependency_sha256",
    "python_version",
)


def _references(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        *manifest["artifacts"].values(),
        *manifest["reports"].values(),
        manifest["inventory_configuration"],
        *([manifest["scenario"]] if "scenario" in manifest else []),
    ]


def _initial(io: Any, path: Path, request: dict[str, Any], *, planned: bool) -> dict[str, Any]:
    from retailops_ai.evaluation_campaign.campaign_anomaly_truth_worker import _digest

    manifest: dict[str, Any] = io.load_json(
        io.safe_file(path, "dataset_manifest.v2.json", limit=io.MAX_METADATA_BYTES)
    )
    descriptor = manifest["descriptor"]
    identity = request["planned_source_dataset_id" if planned else "ordinary_source_dataset_id"]
    if (
        manifest["schema_version"] != ("2.8.0" if planned else "2.7.0")
        or ("scenario" in manifest) != planned
        or ("scenario_plan_sha256" in descriptor) != planned
        or manifest["dataset_id"] != identity
        or identity != "source-sha256-" + _digest(descriptor)
        or descriptor["resolved_parameters"] != request["resolved_parameters"]
        or (planned and descriptor["scenario_plan_sha256"] != _digest(request["scenario_plan"]))
        or sum(r["size_bytes"] for r in _references(manifest)) > request["max_source_bytes"]
        or sum(r["row_count"] for r in descriptor["tables"].values()) > request["max_source_rows"]
    ):
        raise ValueError("campaign_paired_truth_manifest_binding_or_budget")
    return manifest


def _replay(
    io: Any, dataset: Path, initial: dict[str, Any], provenance: dict[str, Any], *, planned: bool
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    from retailops_ai.anomaly_evaluation.paired_source_comparison import SOURCE_TABLES

    # The original reader recomputes all reports and, for 2.8, the complete
    # scenario process/effects. Hash checks alone do not establish clean truth.
    tables, manifest = io.read_source_dataset(dataset)
    if (
        manifest != initial
        or manifest["provenance"] != provenance
        or manifest["facts_ready"] is not True
        or set(tables) != SOURCE_TABLES
        or set(manifest["descriptor"]["tables"]) != SOURCE_TABLES
        or any(
            len(tables[name]) != spec["row_count"]
            for name, spec in manifest["descriptor"]["tables"].items()
        )
    ):
        raise ValueError("campaign_paired_truth_full_source_binding")
    report = io.load_json(
        io.verify_artifact(dataset, manifest["reports"]["source_report.json"], "source_report.json")
    )
    checks = {row["check_id"]: row for row in report["checks"]}
    if (
        report["facts_ready"] is not True
        or report["status"] != "passed"
        or len(checks) != len(report["checks"])
        or not REQUIRED_CHECKS <= checks.keys()
        or (planned and not ANOMALY_CHECKS <= checks.keys())
        or (not planned and bool(ANOMALY_CHECKS & checks.keys()))
        or any(row["status"] != "passed" for row in checks.values())
    ):
        raise ValueError("campaign_paired_truth_complete_report_replay")
    scenario = None
    if planned:
        reference = manifest["scenario"]
        scenario = io.load_json(io.verify_artifact(dataset, reference, reference["path"]))
    return tables, scenario


def _recheck(io: Any, dataset: Path, manifest: dict[str, Any]) -> str:
    if dataset.is_symlink() or any(p.is_symlink() for p in dataset.rglob("*")):
        raise ValueError("campaign_paired_truth_source_symlink")
    references = _references(manifest)
    for reference in references:
        io.verify_artifact(dataset, reference, reference["path"])
    if {p.relative_to(dataset).as_posix() for p in dataset.rglob("*") if p.is_file()} != {
        "dataset_manifest.v2.json",
        *(r["path"] for r in references),
    }:
        raise ValueError("campaign_paired_truth_source_inventory_changed")
    raw = io.safe_file(dataset, "dataset_manifest.v2.json").read_bytes()
    if io.load_json(dataset / "dataset_manifest.v2.json") != manifest:
        raise ValueError("campaign_paired_truth_source_changed")
    return hashlib.sha256(raw).hexdigest()


def native_windows(tables: dict[str, Any], window: dict[str, str]) -> list[dict[str, Any]]:
    from retailops_ai.evaluation_campaign.campaign_anomaly_truth_worker import (
        complete_ordinary_windows,
    )

    canonical_cell = importlib.import_module("data.generator.identity").canonical_cell
    return complete_ordinary_windows(
        {
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
        },
        window,
    )


def _cover(
    windows: list[dict[str, Any]], item: dict[str, Any], interval: dict[str, str]
) -> dict[str, Any]:
    event = "return_completed" if item["injection_type"] == "return_spike" else "sale_completed"
    selected = [
        w
        for w in windows
        if w["event_type"] == event
        and all(w[k] == item[k] for k in ("product_id", "selling_location_id", "channel"))
        and w["window"]["start"] <= interval["start"] <= interval["end"] <= w["window"]["end"]
    ]
    if len(selected) != 1:
        raise ValueError("campaign_paired_truth_episode_incomplete_or_currency_ambiguous")
    return selected[0]


def episode_evidence(
    tables: dict[str, Any], plan: dict[str, Any], window: dict[str, str]
) -> dict[str, dict[str, Any]]:
    """Use native completeness and clocks at each actual primary episode window.

    Reusing the full-parent window's end clock would incorrectly make an early
    development episode unavailable until the end of the entire Source history.
    These bounded scans keep only the small resulting windows, not another world.
    """
    result = {}
    for item in plan["injections"]:
        start, end = item["start_date"], item["end_date"]
        if end < window["start"] or start > window["end"]:
            continue
        if not window["start"] <= start <= end <= window["end"]:
            raise ValueError("campaign_paired_truth_episode_split_boundary")
        interval = {"start": start, "end": end}
        complete = _cover(native_windows(tables, interval), item, interval)
        first = {"start": start, "end": start}
        first_window = (
            complete if first == interval else _cover(native_windows(tables, first), item, first)
        )
        if item["id"] in result:
            raise ValueError("campaign_paired_truth_duplicate_episode")
        result[item["id"]] = {
            **{
                k: complete[k]
                for k in ("event_type", "product_id", "selling_location_id", "channel", "currency")
            },
            "episode_id": item["id"],
            "business_type": item["injection_type"],
            "window": interval,
            "first_evidence_available_at": first_window["available_at"],
            "label_available_at": complete["available_at"],
        }
    return result


def positive_episodes(
    ordinary: dict[str, dict[str, Any]],
    planned: dict[str, dict[str, Any]],
    scenario: dict[str, Any],
) -> list[dict[str, Any]]:
    """Retain native primary effects, including demand censored by physical stock."""
    effects = {e["id"]: e for e in scenario["effects"]["episodes"]}
    injections = {e["id"]: e for e in scenario["plan"]["injections"]}
    if (
        scenario["data_class"] != "simulation_truth"
        or scenario["effects"]["status"] != "passed"
        or len(effects) != len(scenario["effects"]["episodes"])
        or len(injections) != len(scenario["plan"]["injections"])
        or effects.keys() != injections.keys()
        or ordinary.keys() != planned.keys()
    ):
        raise ValueError("campaign_paired_truth_native_episode_inventory")
    for key, injection in injections.items():
        effect = effects[key]
        if (
            effect.get("data_class") != "simulation_truth"
            or type(effect.get("affected_daily_grains")) is not int
            or effect["affected_daily_grains"] <= 0
            or any(effect.get(k) != v for k, v in injection.items())
        ):
            raise ValueError("campaign_paired_truth_primary_effect_binding")
    result = []
    for key, item in sorted(planned.items()):
        baseline = ordinary[key]
        if key not in injections or any(
            item[k] != baseline[k]
            for k in item
            if k not in {"first_evidence_available_at", "label_available_at"}
        ):
            raise ValueError("campaign_paired_truth_episode_scope_binding")
        merged = dict(item)
        for clock in ("first_evidence_available_at", "label_available_at"):
            values = [datetime.fromisoformat(v[clock]) for v in (item, baseline)]
            if any(v.utcoffset() != timedelta(0) for v in values):
                raise ValueError("campaign_paired_truth_episode_utc")
            merged[clock] = max(values).isoformat().replace("+00:00", "Z")
        result.append(merged)
    return result


def verify_paired_sources(
    source: Path, ordinary_dataset: Path, planned_dataset: Path, root: Path, request: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    from retailops_ai.anomaly_evaluation.paired_source_comparison import (
        POLICY,
        PairedSourceComparison,
        paired_clean_windows,
    )
    from retailops_ai.evaluation_campaign.campaign_anomaly_truth_worker import _digest, _producer

    start, end = (date.fromisoformat(request["window"][k]) for k in ("start", "end"))
    parameters = request["resolved_parameters"]
    if (
        not date.fromisoformat(parameters["start_date"])
        <= start
        <= end
        <= date.fromisoformat(parameters["end_date"])
        or (end - start).days > 729
        or not 1 <= request["max_source_rows"] <= 20000000
        or not 1024 <= request["max_source_bytes"] <= 2 * 1024**3
        or ordinary_dataset.resolve() == planned_dataset.resolve()
    ):
        raise ValueError("campaign_paired_truth_request_limits")
    io, provenance = _producer(source, request)
    ordinary = _initial(io, ordinary_dataset, request, planned=False)
    planned = _initial(io, planned_dataset, request, planned=True)
    if (
        ordinary["provenance"] != provenance
        or planned["provenance"] != provenance
        or any(ordinary["descriptor"][k] != planned["descriptor"][k] for k in PAIRED_FIELDS)
    ):
        raise ValueError("campaign_paired_truth_parent_pair_mismatch")
    with PairedSourceComparison(
        root / "paired-source.sqlite", max_rows=request["max_source_rows"]
    ) as index:
        tables, _ = _replay(io, ordinary_dataset, ordinary, provenance, planned=False)
        ordinary_windows = native_windows(tables, request["window"])
        ordinary_episodes = episode_evidence(tables, request["scenario_plan"], request["window"])
        index.add(tables, parent="ordinary")
        del tables
        gc.collect()
        tables, scenario = _replay(io, planned_dataset, planned, provenance, planned=True)
        if scenario is None or scenario["plan"] != request["scenario_plan"]:
            raise ValueError("campaign_paired_truth_frozen_scenario_binding")
        planned_windows = native_windows(tables, request["window"])
        planned_episodes = episode_evidence(tables, scenario["plan"], request["window"])
        index.add(tables, parent="planned")
        del tables
        gc.collect()
        comparison = index.finish(scenario["plan"]["injections"])
    clean = paired_clean_windows(ordinary_windows, planned_windows, comparison)
    episodes = positive_episodes(ordinary_episodes, planned_episodes, scenario)
    windows = [
        *clean,
        *(
            {
                **{
                    k: v
                    for k, v in e.items()
                    if k
                    not in {
                        "episode_id",
                        "business_type",
                        "first_evidence_available_at",
                        "label_available_at",
                    }
                },
                "available_at": e["label_available_at"],
            }
            for e in episodes
        ),
    ]
    windows.sort(
        key=lambda w: (
            w["event_type"],
            w["product_id"],
            w["selling_location_id"],
            w["channel"],
            w["currency"],
            w["window"]["start"],
        )
    )
    parents = {}
    for key, dataset, manifest in (
        ("ordinary", ordinary_dataset, ordinary),
        ("planned", planned_dataset, planned),
    ):
        parents[key] = {
            "source_dataset_id": manifest["dataset_id"],
            "source_schema_version": manifest["schema_version"],
            "source_manifest_sha256": _recheck(io, dataset, manifest),
            "source_descriptor_sha256": _digest(manifest["descriptor"]),
            "source_report_sha256": manifest["reports"]["source_report.json"]["sha256"],
            "source_table_inventory_sha256": _digest(manifest["descriptor"]["tables"]),
            "source_tables": len(manifest["descriptor"]["tables"]),
            "source_rows": sum(r["row_count"] for r in manifest["descriptor"]["tables"].values()),
        }
    _, final_provenance = _producer(source, request)
    if final_provenance != provenance:
        raise ValueError("campaign_paired_truth_producer_changed")
    verification = {
        "version": "ai09-complete-paired-source-verification-1.0.0",
        **parents,
        "producer_commit": provenance["git_commit"],
        "producer_code_sha256": provenance["code_sha256"],
        "producer_lock_sha256": provenance["dependency_sha256"],
        "exporter_lock_sha256": request["exporter_lock_sha256"],
        "producer_python_version": provenance["python_version"],
        "resolved_parameters": parameters,
        "inventory_configuration_sha256": ordinary["descriptor"]["inventory_configuration_sha256"],
        "source_context_sha256": _digest(ordinary["descriptor"]["context"]),
        "source_scenario_sha256": planned["scenario"]["sha256"],
        "scenario_plan_sha256": _digest(scenario["plan"]),
        "window": request["window"],
        "comparison_sha256": _digest(comparison),
        "comparison_policy_sha256": _digest(POLICY),
        "clean_windows_sha256": _digest(clean),
        "clean_window_count": len(clean),
        "complete_windows_sha256": _digest(windows),
        "complete_window_count": len(windows),
        "complete_observation_count": sum(
            (date.fromisoformat(w["window"]["end"]) - date.fromisoformat(w["window"]["start"])).days
            + 1
            for w in windows
        ),
        "episodes_sha256": _digest(episodes),
        "episode_count": len(episodes),
        "native_reader": "data.inventory.source_dataset_io.read_source_dataset",
        "both_complete_sources_and_reports_replayed": True,
        "quality_qualified": False,
    }
    return verification, comparison, windows, episodes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "ordinary", "planned", "root"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from retailops_ai.evaluation_campaign.campaign_generation_worker import (
        read,
        worker_peak_rss_bytes,
        write,
    )

    started = perf_counter()
    verified, comparison, windows, episodes = verify_paired_sources(
        args.source, args.ordinary, args.planned, args.root, read(args.root / "request.json")
    )
    write(args.root / "paired-source-verification.json", verified)
    write(args.root / "paired-source-comparison.json", comparison)
    write(
        args.root / "paired-source-labels.json", {"complete_windows": windows, "episodes": episodes}
    )
    usage, child = (
        resource.getrusage(resource.RUSAGE_SELF),
        resource.getrusage(resource.RUSAGE_CHILDREN),
    )
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
