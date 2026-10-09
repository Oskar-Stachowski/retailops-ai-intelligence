"""Fixed isolated full-parent fitting or fresh-process portable model reload."""

import argparse
import resource
import sys
from pathlib import Path
from time import perf_counter
from typing import Any


def memory_evidence(phase: str, result: dict[str, Any]) -> dict[str, Any]:
    """Native fit receipts already include their parent and short-lived child peaks."""
    from retailops_ai.anomaly_detectors.contract import Resources
    from retailops_ai.worker_resources import worker_peak_rss_bytes

    peak = worker_peak_rss_bytes()
    native_peaks = []
    if phase == "fit":
        if not isinstance(result.get("fit_resources"), list):
            raise ValueError("campaign_anomaly_fit_native_memory_evidence_missing")
        for raw in result["fit_resources"]:
            measured = Resources.model_validate(raw)
            if measured.peak_rss_bytes <= 0:
                raise ValueError("campaign_anomaly_fit_native_memory_evidence_missing")
            native_peaks.append(measured.peak_rss_bytes)
    elif phase != "reload":
        raise ValueError("campaign_anomaly_fit_unknown_memory_phase")
    return {
        "worker_peak_rss_bytes": peak,
        "conservative_worker_tree_peak_rss_bytes": max([peak, *native_peaks]),
        "memory_measurement": "current_executable_and_complete_native_fit_tree_receipts",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("fit", "reload"))
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from retailops_ai.data_contracts.identity import canonical_bytes
    from retailops_ai.evaluation_campaign.campaign_anomaly_fit_contract import (
        CampaignAnomalyFitPlan,
    )
    from retailops_ai.evaluation_campaign.campaign_anomaly_fit_data import (
        fit_physical_anomaly_parent,
        reload_physical_anomaly,
    )
    from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
    from retailops_ai.evaluation_campaign.contract import PreparationRuntime
    from retailops_ai.evaluation_campaign.partitions import runtime_pin
    from retailops_ai.evaluation_campaign.physical_contract import PhysicalSourceSpec

    started = perf_counter()
    request = read(args.root / "request.json")
    plan = CampaignAnomalyFitPlan.model_validate_json(canonical_bytes(request["plan"]))
    runtime = PreparationRuntime.model_validate_json(canonical_bytes(request["runtime"]))
    if runtime_pin() != runtime:
        raise ValueError("campaign_anomaly_fit_worker_runtime_mismatch")
    if args.phase == "fit":
        producer = request["producer"]
        result = fit_physical_anomaly_parent(
            Path(request["snapshot"]),
            Path(request["curated"]),
            args.root,
            plan=plan,
            source=PhysicalSourceSpec.model_validate_json(canonical_bytes(request["source"])),
            runtime=runtime,
            producer_commit=producer["commit"],
            producer_lock=producer["lock"],
            exporter_lock=producer["exporter_lock"],
        )
    else:
        result = reload_physical_anomaly(args.root, read(args.root / "fit.json"))
    if runtime_pin() != runtime:
        raise ValueError("campaign_anomaly_fit_worker_runtime_changed")
    usage = resource.getrusage(resource.RUSAGE_SELF)
    child = resource.getrusage(resource.RUSAGE_CHILDREN)
    result.update(
        wall_seconds=perf_counter() - started,
        cpu_seconds=usage.ru_utime + usage.ru_stime + child.ru_utime + child.ru_stime,
        **memory_evidence(args.phase, result),
    )
    write(args.root / (args.phase + ".json"), result)


if __name__ == "__main__":
    main()
