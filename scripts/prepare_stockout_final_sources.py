"""Prepare prospective source worlds; never fit models or score final outcomes.

An isolated GitHub runner executes the pinned producer and consumer in separate
processes. This script has no model/registry/publication operation. Each accepted
checkpoint preserves all seven qualified consumer parents for a later campaign.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PRODUCER = "61eb215193106cc3f41e6b79e0470585cc9e791b"
CONSUMER = "4faaf4b6c1997fda3a609645595165643bf302a9"
BASELINE_PRODUCER = "08639e9188badb352ed64686a088fe237badad41"
BASELINE_SHA256 = "080fd9c2ec764a35e85e5a17ed0fd3447e0e0d318d960b8bff6546b564850239"
SEEDS = (42, 137, 2026)
RESERVE = 6 * 1024**3
LIMITS = dict(
    scratch_bytes=640 * 1024**2,
    tree_rss_bytes=1280 * 1024**2,
    wall_seconds=2700,
    minimum_free_bytes=RESERVE,
)
CAPS = dict(
    input_rows=500000,
    input_bytes=64 * 1024**2,
    qualification_bytes=4 * 1024**2,
    ledger_rows=100000,
    physical_origins=10000,
    main_database_bytes=128 * 1024**2,
    development_bytes=16 * 1024**2,
)


def load_helper(control: Path, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(filename, control / "scripts" / filename)
    if spec is None or spec.loader is None:
        raise ValueError("stockout_future_helper_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def profile_path(control: Path, seed: int) -> Path:
    return control / f"docs/reference/stockout-resource-pilot-1.8-future-seed{seed}.json"


def generation(seed: int) -> dict[str, Any]:
    return dict(
        profile="ai-stockout-stress-v1",
        days=102,
        products=30,
        stores=3,
        warehouses=2,
        seed=seed,
        start_date="2026-06-09",
        end_date="2026-09-18",
        max_daily_rows=9180,
        forecast_plan_days=0,
    )


def split() -> dict[str, Any]:
    return dict(
        version="stockout-temporal-split-1.0.0",
        start_at="2026-06-09T00:00:00Z",
        train_until="2026-06-24T00:00:00Z",
        tune_until="2026-07-01T00:00:00Z",
        calibration_until="2026-07-13T00:00:00Z",
        test_until="2026-09-19T00:00:00Z",
        evaluated_at="2026-09-19T00:00:00Z",
        purge="outcome_end_and_availability_strictly_before_next_role",
        final_test="membership_only_no_outcome_metrics_no_training_access",
    )


def runner_boundary() -> None:
    if (
        sys.platform != "linux"
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_OS") != "Linux"
        or os.environ.get("GITHUB_REPOSITORY") != "Oskar-Stachowski/retailops-ai-intelligence"
    ):
        raise ValueError("stockout_future_requires_isolated_github_runner")


def validate_profile(profile: dict[str, Any], seed: int) -> None:
    if (
        seed not in SEEDS
        or profile.get("schema_version") != "stockout-resource-pilot-1.8.0"
        or profile.get("execution_scope") != "isolated_github_hosted_runner"
        or profile.get("producer_commit") != PRODUCER
        or profile.get("consumer_baseline_commit") != CONSUMER
        or profile.get("generation") != generation(seed)
        or profile.get("split_policy") != split()
        or profile.get("budgets") != LIMITS
        or profile.get("unchanged_consumer_limits") != CAPS
        or profile.get("physical_origin_upper_bound") != 6120
        or profile.get("local_disk_policy") != dict(minimum_free_bytes=50 * 1024**3)
        or profile.get("measured_resource_baseline", {}).get("sha256") != BASELINE_SHA256
        or profile.get("model_training_permitted") is not False
        or profile.get("final_test_outcomes_evaluated") is not False
    ):
        raise ValueError("stockout_future_frozen_profile_changed")


def preflight(profile: dict[str, Any], baseline: dict[str, Any], free: int) -> dict[str, Any]:
    """Prior measurement estimates resources, never qualifies the new source world."""
    if not (
        baseline.get("status") == "passed"
        and baseline.get("whole_pilot_budget_passed") is True
        and baseline.get("producer_commit") == BASELINE_PRODUCER
        and baseline.get("consumer_commit") == CONSUMER
        and baseline.get("final_test_outcomes_evaluated") is False
        and baseline.get("model_promoted") is False
    ):
        raise ValueError("stockout_future_unqualified_resource_baseline")
    m, old_budget = baseline["measurement"], baseline["budgets"]
    if (
        m["wall_seconds"] > old_budget["wall_seconds"]
        or m["sampled_tree_peak_rss_bytes"] > old_budget["tree_rss_bytes"]
        or max(m["sampled_peak_scratch_logical_bytes"], m["sampled_peak_scratch_allocated_bytes"])
        > old_budget["scratch_bytes"]
        or m["minimum_free_bytes"] < old_budget["minimum_free_bytes"]
    ):
        raise ValueError("stockout_future_unqualified_resource_baseline")
    old = baseline["producer"]["effective_config"]
    g = profile["generation"]
    ratio = max(
        g["days"]
        * g["products"]
        * g["warehouses"]
        / (old["days"] * old["products"] * old["warehouses"]),
        g["days"] * g["products"] * g["stores"] / (old["days"] * old["products"] * old["stores"]),
    )
    # Extra4% is prospective event-growth headroom, not a claimed bound. The
    # qualified-window grid size stays fixed. Live monitoring and real input
    # limits, unchanged from the accepted consumer, decide actual acceptance.
    scale = ratio * 1.2 * 1.04
    stats = baseline["consumer"]["input_stats"]
    estimates = dict(
        tree_rss_bytes=m["sampled_tree_peak_rss_bytes"] * scale,
        scratch_bytes=max(
            m["sampled_peak_scratch_logical_bytes"], m["sampled_peak_scratch_allocated_bytes"]
        )
        * scale,
        wall_seconds=m["wall_seconds"] * scale,
        input_rows=stats["private"]["rows"] * scale,
        input_bytes=stats["private"]["bytes"] * scale,
        ledger_rows=stats["private"]["ledger_rows"] * scale,
        qualification_bytes=stats["qualification_bytes"] * ratio * 1.2,
    )
    checks = {k: estimates[k] <= LIMITS[k] for k in LIMITS if k != "minimum_free_bytes"}
    checks.update({k: estimates[k] <= CAPS[k] for k in CAPS if k in estimates})
    checks["free_disk"] = free >= RESERVE + LIMITS["scratch_bytes"]
    checks["physical_origins"] = profile["physical_origin_upper_bound"] <= CAPS["physical_origins"]
    return dict(
        ready_to_attempt=all(checks.values()),
        estimates=estimates,
        checks=checks,
        measured_baseline_producer_commit=BASELINE_PRODUCER,
        proposed_producer_commit=PRODUCER,
        estimate_headroom_fraction=0.2,
        prospective_event_growth_headroom_fraction=0.04,
        scope="heuristic prior-world resource estimates; new source qualification required",
        new_source_qualified=False,
        independent_quality_accepted=False,
    )


def consume(root: Path, probe: Any) -> None:
    from retailops_ai.curated.builder import build_curated
    from retailops_ai.source_snapshot.importer import import_snapshot
    from retailops_ai.stockout.split import SplitPolicy
    from retailops_ai.stockout_history.bundle import build_history_bundle
    from retailops_ai.stockout_label_partitions.bundle import build_label_bundle
    from retailops_ai.stockout_temporal_series.bundle import build_temporal_bundle
    from retailops_ai.stockout_temporal_storage.store import PartitionInputs
    from retailops_ai.stockout_upstream_series.bundle import build_upstream_bundle

    for name in ("data", "ml", "retailops"):
        if importlib.util.find_spec(name) is not None:
            raise ValueError("stockout_future_producer_importable_in_consumer")
    paths = json.loads((root / "producer.json").read_bytes())["paths"]
    records: list[dict[str, Any]] = []
    stats: dict[str, Any] = {}
    for name, private in (("facts", False), ("private", True)):
        imported = probe.stage(
            name + "_import",
            lambda name=name, private=private: import_snapshot(
                Path(paths[name + "_export"]),
                root / (name + "_import") / "data/generated",
                allow_evaluation_truth=private,
                required_use_cases=("forecast_source", "inventory_source"),
            ),
            records,
        )
        paths[name + "_import"] = str(imported.directory)
        stats[name] = dict(
            snapshot_id=imported.snapshot.snapshot_id,
            rows=sum(t["row_count"] for t in imported.snapshot.manifest["tables"]),
            bytes=sum(r["bytes"] for r in imported.snapshot.references),
            ledger_rows=sum(
                t["row_count"]
                for t in imported.snapshot.manifest["tables"]
                if t["table"] == "inventory_ledger"
            ),
        )
        if not private:
            curated = probe.stage(
                "curated",
                lambda imported=imported: build_curated(
                    imported.directory, root / "curated/data/generated"
                ),
                records,
            )
            paths["curated"] = str(curated.directory)
    curated_root, private_root = Path(paths["curated"]), Path(paths["private_import"]) / "snapshot"
    stats["qualification_bytes"] = (
        (
            private_root
            / "evaluation_truth/qualification/simulation_truth/inventory_qualified_windows.json"
        )
        .stat()
        .st_size
    )
    features, upstream, labels, temporal = (
        root / n for n in ("features", "upstream", "labels", "temporal")
    )
    feature_doc, _ = probe.stage(
        "features", lambda: build_history_bundle(curated_root, features), records
    )
    label_doc, _ = probe.stage(
        "labels",
        lambda: build_label_bundle(private_root, labels, allow_evaluation_truth=True),
        records,
    )
    upstream_doc, _ = probe.stage(
        "upstream", lambda: build_upstream_bundle(curated_root, features, upstream), records
    )
    inputs = PartitionInputs(curated_root, private_root, features, upstream, labels)
    policy = SplitPolicy.model_validate(
        json.loads((root / "pilot-config.json").read_bytes())["split_policy"]
    )
    temporal_doc, _ = probe.stage(
        "temporal",
        lambda: build_temporal_bundle(
            inputs, temporal, split_policy=policy, allow_evaluation_truth=True
        ),
        records,
    )
    groups = {
        n: probe.tree_size(Path(paths[n]) if n in paths else root / n)
        for n in (
            "source",
            "qualification",
            "facts_export",
            "private_export",
            "facts_import",
            "private_import",
            "curated",
            "features",
            "upstream",
            "labels",
            "temporal",
        )
    }
    probe.write_json(
        root / "consumer.json",
        dict(
            stages=records,
            input_stats=stats,
            paths=paths,
            groups=groups,
            ids=dict(
                feature_bundle_id=feature_doc["feature_bundle_id"],
                label_bundle_id=label_doc["label_bundle_id"],
                upstream_bundle_id=upstream_doc["upstream_bundle_id"],
                temporal_bundle_id=temporal_doc["temporal_bundle_id"],
            ),
            physical_origins=temporal_doc["descriptor"]["rows"],
            final_test_membership=temporal_doc["report"]["split"]["eligible_by_role"]["test"],
            model_training_performed=False,
            final_test_outcomes_evaluated=False,
            independent_quality_accepted=False,
            model_promoted=False,
            producer_roots_unimportable=["data", "ml", "retailops"],
        ),
    )


def worker(args: argparse.Namespace, probe: Any, remote: Any) -> None:
    runner_boundary()
    if args.retained is None:
        raise ValueError("stockout_future_worker_root_required")
    profile = remote.read(args.retained / "pilot-config.json")
    validate_profile(profile, args.seed)
    if args.worker == "producer":
        # Explicit expected pin for this isolated helper instance; the old
        # measurement file and its old source receipts are never changed.
        probe.PRODUCER_COMMIT = PRODUCER
        probe.produce(args.retained)
    elif args.worker == "consumer":
        if remote.head(args.consumer) != CONSUMER:
            raise ValueError("stockout_future_consumer_pin_changed")
        sys.path.insert(0, str(args.consumer / "src"))
        consume(args.retained, probe)
        for name, module in tuple(sys.modules.items()):
            path = getattr(module, "__file__", None)
            if (
                name.startswith("retailops_ai")
                and isinstance(path, str)
                and not Path(path).resolve().is_relative_to((args.consumer / "src").resolve())
            ):
                raise ValueError("stockout_future_wrong_consumer_import")
    else:
        parents = remote.archive_inputs(args.retained, args.output / "verified-upload")
        remote.write(args.output / "archive.json", parents)


def prepare(args: argparse.Namespace, probe: Any, remote: Any) -> None:
    import psutil  # type: ignore[import-untyped]

    runner_boundary()
    profile = remote.read(profile_path(args.control, args.seed))
    validate_profile(profile, args.seed)
    if remote.head(args.source) != PRODUCER or remote.head(args.consumer) != CONSUMER:
        raise ValueError("stockout_future_source_or_consumer_pin_changed")
    for root, pin, paths in (
        (args.source, PRODUCER, []),
        (args.consumer, CONSUMER, ["--", "src", "contracts", "pyproject.toml", "uv.lock"]),
    ):
        subprocess.check_call(  # noqa: S603 - pinned read-only git without shell
            [remote.git_binary(), "-C", str(root), "diff", "--quiet", pin, *paths]
        )
    baseline_path = args.control / "docs/reference/stockout-resource-baseline-30.json"
    if remote.sha(baseline_path) != BASELINE_SHA256:
        raise ValueError("stockout_future_resource_baseline_changed")
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    forecast = preflight(profile, remote.read(baseline_path), shutil.disk_usage(args.output).free)
    frozen = dict(
        schema_version="stockout-future-preparation-plan-1.0.0",
        control_commit=remote.head(args.control),
        producer_commit=PRODUCER,
        consumer_commit=CONSUMER,
        seed=args.seed,
        generation=generation(args.seed),
        profile_sha256=remote.sha(profile_path(args.control, args.seed)),
        baseline_sha256=BASELINE_SHA256,
        preparation_code_sha256=remote.sha(Path(__file__).resolve()),
        measurement_helper_sha256=remote.sha(args.control / "scripts/measure_stockout_pipeline.py"),
        archive_helper_sha256=remote.sha(args.control / "scripts/prepare_stockout_remote.py"),
        resource_preflight=forecast,
        model_training_permitted=False,
        final_test_outcomes_evaluated=False,
        promotion_permitted=False,
        ai08_ready=False,
    )
    remote.write(args.output / "plan.json", frozen)
    if not forecast["ready_to_attempt"]:
        raise ValueError("stockout_future_resource_preflight_not_ready")
    start = time.perf_counter()
    peak = logical = allocated = samples = 0
    minimum = shutil.disk_usage(args.output).free
    phases: list[dict[str, Any]] = []
    failure = None
    with probe.owned_scratch(args.output, retain=True) as (root, state):
        remote.write(root / "pilot-config.json", profile)
        upload = args.output / "verified-upload"
        upload.mkdir(mode=0o700)
        for phase in ("producer", "consumer", "archive"):
            argv = [
                sys.executable,
                "-P",
                str(Path(__file__).resolve()),
                "--control",
                str(args.control),
                "--consumer",
                str(args.consumer),
                "--source",
                str(args.source),
                "--seed",
                str(args.seed),
                "--output",
                str(args.output),
                "--worker",
                phase,
                "--retained",
                str(root),
            ]
            env = {
                **os.environ,
                "RETAILOPS_PROBE_PRODUCER": str(args.source),
                "PYTHONNOUSERSITE": "1",
            }
            env.pop("PYTHONPATH", None)
            phase_start = time.perf_counter()
            with (args.output / (phase + ".log")).open("x") as log:
                child = subprocess.Popen(  # noqa: S603 - fixed task-owned worker without shell
                    argv,
                    cwd=args.consumer,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                try:
                    while True:
                        try:
                            parent = psutil.Process(child.pid)
                            processes = [parent, *parent.children(recursive=True)]
                        except psutil.NoSuchProcess:
                            processes = []
                        rss = 0
                        for process in processes:
                            try:
                                rss += process.memory_info().rss
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                continue
                        size = probe.tree_size(args.output)
                        free = shutil.disk_usage(args.output).free
                        peak, logical, allocated = (
                            max(peak, rss),
                            max(logical, size["logical_bytes"]),
                            max(allocated, size["allocated_bytes"]),
                        )
                        minimum, samples = min(minimum, free), samples + 1
                        if (
                            rss > LIMITS["tree_rss_bytes"]
                            or max(size["logical_bytes"], size["allocated_bytes"])
                            > LIMITS["scratch_bytes"]
                            or free < RESERVE
                            or time.perf_counter() - start > LIMITS["wall_seconds"]
                        ):
                            failure = "stockout_future_live_resource_limit"
                            probe.stop_owned(child)
                            break
                        if child.poll() is not None:
                            break
                        time.sleep(0.2)
                finally:
                    if child.poll() is None:
                        probe.stop_owned(child)
                child.wait()
            phases.append(
                dict(
                    phase=phase,
                    exit_code=child.returncode,
                    wall_seconds=time.perf_counter() - phase_start,
                )
            )
            if child.returncode != 0 or failure:
                failure = failure or "stockout_future_worker_failed"
                break
        passed = (
            failure is None
            and (root / "consumer.json").is_file()
            and (args.output / "archive.json").is_file()
        )
        receipt = dict(
            schema_version="stockout-future-resource-receipt-1.0.0",
            status="passed" if passed else "failed",
            recorded_at_utc=datetime.now(UTC).isoformat(),
            plan=frozen,
            budgets=LIMITS,
            phases=phases,
            failure=failure,
            measurement=dict(
                wall_seconds=time.perf_counter() - start,
                sampled_tree_peak_rss_bytes=peak,
                sampled_peak_scratch_logical_bytes=logical,
                sampled_peak_scratch_allocated_bytes=allocated,
                minimum_free_bytes=minimum,
                samples=samples,
                sample_seconds=0.2,
            ),
            producer=remote.read(root / "producer.json")
            if (root / "producer.json").is_file()
            else None,
            consumer=remote.read(root / "consumer.json") if passed else None,
            source_preparation_passed=passed,
            model_training_performed=False,
            final_test_outcomes_evaluated=False,
            independent_quality_accepted=False,
            thresholds_approved=False,
            model_promoted=False,
            ai08_ready=False,
        )
        remote.write(args.output / "resource.json", receipt)
        if not passed:
            raise ValueError(failure or "stockout_future_missing_consumer_receipt")
        parents = remote.read(args.output / "archive.json")
        for name in ("plan.json", "resource.json"):
            shutil.copyfile(args.output / name, upload / name)
        remote.write(
            upload / "checkpoint.json",
            dict(
                schema_version="stockout-future-source-checkpoint-1.0.0",
                plan=frozen,
                parents=parents,
                resource_receipt_sha256=remote.sha(upload / "resource.json"),
                public_files={
                    n: dict(bytes=(upload / n).stat().st_size, sha256=remote.sha(upload / n))
                    for n in ("plan.json", "resource.json")
                },
                model_training_performed=False,
                final_test_outcomes_evaluated=False,
                independent_quality_accepted=False,
                thresholds_approved=False,
                model_promoted=False,
                ai08_ready=False,
            ),
        )
        # Only verified archived consumer parents are retained on the runner.
        # The context removes this script's duplicate raw working tree.
        state["success"] = False
        print(
            json.dumps(
                dict(
                    status="source_checkpoint_passed",
                    seed=args.seed,
                    checkpoint_sha256=remote.sha(upload / "checkpoint.json"),
                    model_training_performed=False,
                    final_test_outcomes_evaluated=False,
                    ai08_ready=False,
                )
            )
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("control", "consumer", "source", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--seed", type=int, choices=SEEDS, required=True)
    parser.add_argument("--worker", choices=("producer", "consumer", "archive"))
    parser.add_argument("--retained", type=Path)
    args = parser.parse_args()
    for name in ("control", "consumer", "source", "output", "retained"):
        if getattr(args, name) is not None:
            setattr(args, name, getattr(args, name).resolve())
    try:
        probe = load_helper(args.control, "measure_stockout_pipeline.py")
        remote = load_helper(args.control, "prepare_stockout_remote.py")
        if args.worker:
            worker(args, probe, remote)
        else:
            prepare(args, probe, remote)
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(
            json.dumps(
                dict(
                    status="failed",
                    reason="stockout_future_preparation_failed",
                    exception=type(exc).__name__,
                )
            )
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
