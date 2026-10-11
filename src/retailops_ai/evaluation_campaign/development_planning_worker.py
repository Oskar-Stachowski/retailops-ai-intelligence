"""Native planning in the producer's isolated, pinned interpreter (stdlib entry)."""

import argparse
import hashlib
import importlib
import importlib.util
import os
import stat
from datetime import date
from pathlib import Path
from typing import Any


def helpers() -> Any:
    path = Path(__file__).with_name("campaign_generation_worker.py")
    spec = importlib.util.spec_from_file_location("ai09_generation_worker", path)
    if spec is None or spec.loader is None:
        raise ValueError("native_planning_worker_helpers_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def plan(source: Path, root: Path) -> None:
    worker = helpers()
    request = worker.read(root / "request.json")
    recipe, planning = request["source"], request["planning"]
    _, provenance = worker.producer_environment(source, recipe, recipe["exporter_lock_sha256"])
    descriptor = os.open(source / "data/anomalies/development_plan.py", os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 1024**2:
            raise ValueError("native_planning_invalid_producer_module")
        if hashlib.sha256(stream.read()).hexdigest() != planning["producer_planner_sha256"]:
            raise ValueError("native_planning_producer_module_pin_mismatch")
    native = importlib.import_module("data.anomalies.development_plan")
    if native.VERSION != planning["producer_planner_version"]:
        raise ValueError("native_planning_producer_version_mismatch")
    configuration = importlib.import_module("data.generator.configuration")
    generation = configuration.DatasetGenerationConfig(
        **{
            key: date.fromisoformat(value) if key in {"start_date", "end_date"} else value
            for key, value in request["generation"]["requested_parameters"].items()
        }
    )
    if (
        configuration.resolve_generation_config(generation).parameters()
        != request["generation"]["resolved_parameters"]
    ):
        raise ValueError("native_planning_resolved_generation_mismatch")
    selection = native.DevelopmentScenarioRecipe.model_validate(request["selection"])
    bundle = native.prepare_development_scenarios(
        Path(request["raw_source"]), generation, selection
    )
    if bundle["source_provenance"] != provenance:
        raise ValueError("native_planning_source_provenance_mismatch")
    worker.write(root / "native-plans.json", bundle)
    if (root / "native-plans.json").stat().st_size > planning["max_bundle_bytes"]:
        raise ValueError("native_planning_bundle_size_limit")
    worker.write(root / "result.json", {"worker_peak_rss_bytes": worker.worker_peak_rss_bytes()})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("producer", type=Path)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    plan(args.producer, args.root)


if __name__ == "__main__":
    main()
