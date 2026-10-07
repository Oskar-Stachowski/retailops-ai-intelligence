"""Run tiny real generation phases on an isolated runner, without a campaign journal."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from pydantic import JsonValue

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign.campaign_generation import PHASES, _environment, _producer_pin
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationPlan,
    CampaignGenerationResources,
)
from retailops_ai.evaluation_campaign.campaign_generation_monitor import monitor
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.partitions import runtime_pin
from retailops_ai.source_snapshot.files import SnapshotError

PRODUCER = "16d34887b058b3dfb270af474f28242194a968ca"
SETUP = """import json,sys
from datetime import date
sys.path.insert(0,sys.argv[1])
from data.generator.configuration import DatasetGenerationConfig,resolve_generation_config
from data.inventory.source_dataset_io import fingerprint
from data.anomalies.physical_scenarios import physical_example_plan
p=json.loads(sys.argv[2])
g=DatasetGenerationConfig(**{k:date.fromisoformat(v) if k in {'start_date','end_date'} else v for k,v in p.items()})
print(json.dumps({'resolved':resolve_generation_config(g).parameters(),
 'lock':fingerprint()['dependency_sha256'],
 'scenario':physical_example_plan(g) if sys.argv[3]=='planned_anomaly' else None}))
"""


def control(source: Path, source_python: Path, output: Path, *, anomaly: bool) -> dict[str, Any]:
    root = output / ("control-" + uuid4().hex)
    root.mkdir(mode=0o700)
    (root / "tmp").mkdir(mode=0o700)
    requested: dict[str, JsonValue] = {
        "profile": "ai-load",
        "seed": 42,
        "days": 45,
        "products": 8 if anomaly else 2,
        "stores": 3 if anomaly else 1,
        "warehouses": 2 if anomaly else 1,
        "start_date": "2026-06-17",
        "end_date": "2026-07-31",
        "max_daily_rows": 1080 if anomaly else 90,
        "forecast_plan_days": 14,
    }
    entrypoint = "planned_anomaly" if anomaly else "cached_inventory_v2"
    setup = subprocess.run(  # noqa: S603 -- fixed owned producer setup, no shell
        [
            str(source_python),
            "-I",
            "-B",
            "-c",
            SETUP,
            str(source),
            json.dumps(requested),
            entrypoint,
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
        env=_environment(root),
    )
    resolved = json.loads(setup.stdout)
    exporter_lock = hashlib.sha256(
        (source / "data/requirements-parquet.txt").read_bytes()
    ).hexdigest()
    # These are producer identity pins, deliberately not a canonical source recipe.
    # The tiny plan cannot bind to CampaignSourceRecipe and authorizes no campaign.
    producer = {"producer_commit": PRODUCER, "producer_lock_sha256": resolved["lock"]}
    plan = CampaignGenerationPlan(
        source_recipe_sha256=canonical_sha256(producer),
        exporter_lock_sha256=exporter_lock,
        requested_parameters=requested,
        resolved_parameters=resolved["resolved"],
        entrypoint="planned_anomaly" if anomaly else "cached_inventory_v2",
        scenario_plan=resolved["scenario"],
        snapshot_schema_version="1.2.0" if anomaly else "1.1.0",
        required_use_cases=("forecast_source", "inventory_source", "anomaly_source")
        if anomaly
        else ("forecast_source", "inventory_source"),
        resources=CampaignGenerationResources(
            wall_seconds=600,
            tree_rss_bytes=1024**3,
            scratch_bytes=512 * 1024**2,
            minimum_free_disk_bytes=6 * 1024**3,
            minimum_available_memory_bytes=1024**3,
        ),
    )
    write(
        root / "request.json",
        {
            "plan": plan.model_dump(mode="json"),
            "source": producer,
            "runtime": runtime_pin().model_dump(mode="json"),
        },
    )
    import retailops_ai.evaluation_campaign.campaign_generation_worker as worker

    started = perf_counter()
    phases = []
    for phase in PHASES:
        interpreter = str(source_python) if phase in PHASES[:3] else sys.executable
        result = monitor(
            [
                interpreter,
                "-I",
                "-B",
                str(Path(worker.__file__).resolve()),
                phase,
                str(source),
                str(root),
            ],
            root=root,
            log=root / (phase + ".log"),
            env=_environment(root),
            scratch=(root, source / "data/generated/ai09-campaign" / root.name / "snapshot"),
            resources=plan.resources,
            deadline=started + plan.resources.wall_seconds,
        )
        phases.append({"phase": phase, **result})
        write(root / (phase + "-resources.json"), phases[-1])
        if result["status"] != "passed":
            break
        actual = read(root / (phase + ".json"))
        if actual["worker_peak_rss_bytes"] > plan.resources.tree_rss_bytes:
            raise SnapshotError("generation_control_worker_peak_limit")
    report = {
        "scope": "tiny_real_workers_no_canonical_binding_no_campaign_journal",
        "entrypoint": entrypoint,
        "root": str(root),
        "phases": phases,
        "completed_phases": sum(p["status"] == "passed" for p in phases),
        "project_fits": 0,
        "final_test_opened": False,
        "stage_ready": False,
    }
    if report["completed_phases"] == len(PHASES):
        report["verified_source"] = read(root / "verify.json")["source"]
    write(root / "control.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--producer-python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "linux" or os.environ.get("GITHUB_ACTIONS") != "true":
        raise SnapshotError("generation_control_requires_isolated_github_runner")
    _producer_pin(args.source, PRODUCER)
    args.output.mkdir(mode=0o700)
    reports = [
        control(args.source, args.producer_python, args.output, anomaly=anomaly)
        for anomaly in (False, True)
    ]
    write(args.output / "summary.json", {"controls": reports, "canonical_qualified": False})
    passed = all(r["completed_phases"] == len(PHASES) for r in reports)
    print(
        json.dumps({"passed": passed, "completed_phases": [r["completed_phases"] for r in reports]})
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
