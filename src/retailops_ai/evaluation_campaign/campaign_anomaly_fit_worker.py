"""Fixed isolated full-parent fitting or fresh-process portable model reload."""

import argparse
import resource
import sys
from pathlib import Path
from time import perf_counter


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
    scale = 1 if sys.platform == "darwin" else 1024
    result.update(
        wall_seconds=perf_counter() - started,
        cpu_seconds=usage.ru_utime + usage.ru_stime + child.ru_utime + child.ru_stime,
        worker_peak_rss_bytes=int(usage.ru_maxrss * scale),
        conservative_worker_tree_peak_rss_bytes=int((usage.ru_maxrss + child.ru_maxrss) * scale),
    )
    write(args.root / (args.phase + ".json"), result)


if __name__ == "__main__":
    main()
