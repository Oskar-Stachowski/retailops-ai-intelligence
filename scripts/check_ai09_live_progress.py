"""Small real Source control with an explicit window for observing live Actions logs."""

from __future__ import annotations

import argparse
import importlib
import os
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PRODUCER = "ff2504a9cf6e4f46040c8327901b7bcb82d115c0"
HOLD_SECONDS = 130
VERSION = "ai09-actions-live-control-1.0.0"


def modules() -> tuple[Any, Any]:
    sys.path.insert(0, str(ROOT))
    return (
        importlib.import_module("scripts.measure_ai09_development_capacity"),
        importlib.import_module("scripts.ai09_live_progress"),
    )


def worker(source: Path, output: Path, hold_seconds: int) -> None:
    probe, _ = modules()
    probe.clean_pin(source, PRODUCER)
    sys.path.insert(0, str(source))
    progress = importlib.import_module("data.generator.progress")
    configuration = importlib.import_module("data.generator.configuration")
    runner = importlib.import_module("data.inventory.source_cohort_batch_v2")
    generation = configuration.DatasetGenerationConfig(
        profile="ai-smoke",
        seed=42,
        days=10,
        products=8,
        stores=2,
        warehouses=1,
        start_date=date(2026, 7, 22),
        end_date=date(2026, 7, 31),
        forecast_plan_days=14,
    )
    with progress.reporting(sys.stdout, interval_seconds=60):
        result = runner.run(generation, output / "raw")
        if result["status"] != "passed" or result["facts_ready"] is not True:
            raise ValueError("live_control_native_source_failed")
        probe.write(
            output / "source-result.json",
            {
                "version": VERSION,
                "source_dataset_id": result["dataset_id"],
                "tables": result["table_count"],
                "rows": sum(table["row_count"] for table in result["tables"].values()),
                "producer_commit": PRODUCER,
                "native_source_writer_and_reader_completed": True,
                "canonical_profile": False,
                "project_fits": 0,
                "final_test_opened": False,
            },
        )

        # The real source has finished. This named idle window allows a human/UI
        # observer to see flushed output before the job ends; it is never work progress.
        def hold() -> None:
            time.sleep(hold_seconds)

        progress.stage("visibility_observation_hold_no_source_work")(hold)()
    probe.clean_pin(source, PRODUCER)


def run(source: Path, source_python: Path, output: Path) -> None:
    probe, live_module = modules()
    probe.require_remote()
    probe.clean_pin(source, PRODUCER)
    head = probe.git(ROOT, "rev-parse", "HEAD")
    probe.clean_pin(ROOT, head)
    if (
        not source.is_absolute()
        or not output.is_absolute()
        or not source_python.is_absolute()
        or output.exists()
        or source.is_relative_to(output)
        or any(p.is_symlink() for p in (source, output, *source.parents, *output.parents))
    ):
        raise ValueError("live_control_fresh_absolute_owned_output_required")
    output.mkdir(parents=True, mode=0o700)
    started = time.perf_counter()
    live = live_module.LiveProgress(
        "live_source_control",
        started=started,
        budget_seconds=600,
        artifact=output / "live.progress.jsonl",
        interval_seconds=60,
    )
    resource = probe.monitor(
        [
            str(source_python),
            "-I",
            "-B",
            str(Path(__file__).resolve()),
            "--source",
            str(source),
            "--output",
            str(output),
            "--worker",
            "--hold-seconds",
            str(HOLD_SECONDS),
        ],
        cwd=source,
        env={
            **os.environ,
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONHASHSEED": "0",
        },
        log=output / "live-source-control.log",
        roots=(output,),
        budgets={
            "tree_rss_bytes": 1024**3,
            "scratch_bytes": 512 * 1024**2,
            "minimum_free_disk_bytes": 6 * 1024**3,
            "minimum_available_memory_bytes": 1024**3,
            "sample_seconds": 0.2,
        },
        deadline=started + 600,
        live_progress=live,
    )
    receipt = {
        "version": VERSION,
        "control_commit": head,
        "producer_commit": PRODUCER,
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "code_sha256": {
            name: probe.sha(ROOT / name)
            for name in (
                "scripts/ai09_live_progress.py",
                "scripts/measure_ai09_development_capacity.py",
                "scripts/check_ai09_live_progress.py",
            )
        },
        "resource": resource,
        "visibility_observation_hold_seconds": HOLD_SECONDS,
        "source_result": probe.read(output / "source-result.json")
        if (output / "source-result.json").is_file()
        else None,
        "actions_ui_incremental_visibility": "unverified_requires_live_UI_observation",
        "canonical_diagnostic_dispatched": False,
        "project_fits": 0,
        "final_test_opened": False,
        "stage_ready": False,
    }
    probe.write(output / "live-control.json", receipt)
    if resource["status"] != "passed":
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--producer-python", type=Path)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--hold-seconds", type=int, choices=(0, HOLD_SECONDS), default=HOLD_SECONDS)
    args = parser.parse_args()
    if args.worker:
        worker(args.source, args.output, args.hold_seconds)
    else:
        run(args.source, args.producer_python or args.source / ".venv/bin/python", args.output)


if __name__ == "__main__":
    main()
