"""One prospectively reserved final seed using frozen models and calibration, with no refits."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ai09_preparation_worker as native  # noqa: E402
import measure_ai09_development_capacity as probe  # noqa: E402

PLAN = ROOT / "docs/reference/ai09-portfolio-final-v1.json"
ASSESSMENT = ROOT / "docs/reference/ai09-portfolio-assessment-v1.json"
CALIBRATION_FILES = frozenset(
    {
        "scripts/run_ai09_portfolio_assessment.py",
        "src/retailops_ai/evaluation_campaign/portfolio_assessment.py",
        "src/retailops_ai/evaluation_campaign/portfolio_data_io.py",
        "src/retailops_ai/evaluation_campaign/portfolio_model_io.py",
    }
)


def source_plan(seed: int) -> dict[str, Any]:
    assessment = probe.read(ASSESSMENT)
    if type(seed) is not int or seed not in assessment["final"]["data_seeds"]:
        raise ValueError("portfolio_final_unregistered_seed")
    plan = probe.read(ROOT / "docs/reference/ai09-portfolio-development-v1.json")
    plan.pop("comparison")
    plan.update(
        version="ai09-portfolio-final-source-1.0.0",
        scope="whole_reserved_final_source_no_fits",
        profile_name=assessment["final"]["profile_name"],
        generation=assessment["final"]["generation"] | {"seed": seed},
        budgets=assessment["final"]["budgets"],
        final_generation_authorized=True,
        final_test_opened=True,
    )
    for key in (
        "producer_commit",
        "producer_lock_sha256",
        "exporter_lock_sha256",
        "consumer_lock_sha256",
    ):
        if plan[key] != assessment["final"][key]:
            raise ValueError("portfolio_final_source_or_dependency_changed")
    return plan


def verified_freeze(root: Path, plan: dict[str, Any]) -> dict[str, Any]:
    from retailops_ai.data_contracts.identity import canonical_sha256
    from retailops_ai.evaluation_campaign.portfolio_model_io import verify_models

    freeze = probe.read(root / "freeze.json")
    models = verify_models(root / "models")
    calibration = probe.read(root / "calibration.json")
    if (
        probe.sha(root / "freeze.json") != plan["freeze_sha256"]
        or freeze["assessment"] != probe.read(ASSESSMENT)
        or freeze["assessment_sha256"] != canonical_sha256(freeze["assessment"])
        or freeze["model_bundle_sha256"] != canonical_sha256(models)
        or freeze["calibration_sha256"] != canonical_sha256(calibration)
        or models["original_comparison_id"] != plan["comparison_id"]
        or calibration["status"] != "fitted"
        or freeze["no_final_refit_or_reselection"] is not True
        or probe.read(root / "result.json")["status"] != "calibrated_and_frozen_before_final"
        or probe.read(root / "intent.json")["commit"] != plan["original"]["head"]
        or set(plan["calibration_code_sha256"]) != CALIBRATION_FILES
        or any(
            probe.sha(ROOT / name) != checksum
            for name, checksum in plan["calibration_code_sha256"].items()
        )
    ):
        raise ValueError("portfolio_final_incomplete_or_changed_freeze")
    return freeze


def worker(args: argparse.Namespace) -> None:
    identity = probe.read(args.output / "identity.json")
    intent = probe.read(args.output / "intent.json")
    plan = intent["source_plan"]
    if probe.sha(args.output / "intent.json") != identity["intent_sha256"] or plan != source_plan(
        intent["seed"]
    ):
        raise ValueError("portfolio_final_reserved_plan_changed")
    producer = args.operation in probe.PHASES[:3]
    probe.clean_pin(ROOT, identity["consumer_commit"])
    probe.clean_pin(args.source, identity["producer_commit"])
    before = native.digest(native.runtime(args.source if producer else ROOT, producer=producer))
    if before != identity["producer_runtime_sha256" if producer else "consumer_runtime_sha256"]:
        raise ValueError("portfolio_final_runtime_changed")
    if not producer:
        sys.path.insert(0, str(ROOT / "src"))
    if args.operation in probe.PHASES:
        namespace = argparse.Namespace(
            source=args.source, output=args.output, worker=args.operation
        )
        result = (probe.producer_worker if producer else probe.consumer_worker)(namespace, plan)
        result["worker_peak_self_rss_bytes"] = probe.worker_peak_rss_bytes()
        probe.write(args.output / (args.operation + ".json"), result)
    else:
        sys.path.insert(0, str(ROOT / "src"))
        if args.operation == "prepare":
            from retailops_ai.evaluation_campaign.portfolio_final_data import prepare_final

            prepare_final(args.output, intent["assessment"], plan["generation"])
        elif args.operation == "infer":
            from run_ai09_portfolio_assessment import infer

            infer(args.output, args.output / "frozen/models")
        else:
            from retailops_ai.evaluation_campaign.portfolio_assessment import metrics
            from retailops_ai.evaluation_campaign.portfolio_data_io import scored_rows

            freeze = verified_freeze(args.output / "frozen", probe.read(PLAN))
            report = metrics(
                scored_rows(args.output),
                probe.read(args.output / "frozen/calibration.json"),
                freeze["required_groups"],
            )
            report.update(
                seed=intent["seed"], freeze_sha256=intent["freeze_sha256"], final_test_opened=True
            )
            probe.write(args.output / "report.json", report)
    if (
        native.digest(native.runtime(args.source if producer else ROOT, producer=producer))
        != before
    ):
        raise ValueError("portfolio_final_runtime_changed_during_operation")


def run(args: argparse.Namespace) -> None:
    sys.path.insert(0, str(ROOT / "src"))
    import psutil  # type: ignore[import-untyped]
    from ai09_checkpoint_transport import validate_metadata
    from download_forecast_cohort_checkpoint import _download_zip, artifact_metadata
    from run_ai09_portfolio_repair import restore_archive
    from run_ai09_preparation import environment

    probe.require_remote()
    if os.environ.get("GITHUB_RUN_ATTEMPT") != "1":
        raise ValueError("portfolio_final_rerun_requires_explicit_review")
    plan, assessment = probe.read(PLAN), probe.read(ASSESSMENT)
    source = source_plan(args.seed)
    if (
        plan["maximum_attempts_per_seed"] != 1
        or plan["assessment_sha256"] != native.digest(assessment)
        or assessment["final"]["data_seeds"] != [42, 137, 2026]
    ):
        raise ValueError("portfolio_final_plan_changed")
    probe.clean_pin(ROOT, os.environ["GITHUB_SHA"])
    probe.clean_pin(args.source, source["producer_commit"])
    for path in (args.output, args.source):
        if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError("portfolio_final_owned_absolute_unlinked_paths_required")
    budgets = source["budgets"]
    if (
        psutil.virtual_memory().available
        < budgets["tree_rss_bytes"] + budgets["minimum_available_memory_bytes"]
        or shutil.disk_usage(args.output.parent).free
        < budgets["scratch_bytes"] + budgets["minimum_free_disk_bytes"]
        or (args.source / "data/generated/ai09-capacity-snapshot").exists()
    ):
        raise ValueError("portfolio_final_preflight_reserve_or_existing_source")
    if (
        probe.sha(ROOT / "uv.lock") != source["consumer_lock_sha256"]
        or probe.sha(ROOT / "environments/tensorflow/uv.lock")
        != assessment["final"]["tensorflow_lock_sha256"]
        or probe.sha(args.source / "data/requirements-parquet.txt")
        != source["exporter_lock_sha256"]
    ):
        raise ValueError("portfolio_final_lock_changed")
    args.output.mkdir(mode=0o700)
    started = time.perf_counter()
    result: dict[str, Any] = {
        "status": "running",
        "seed": args.seed,
        "resources": {},
        "final_test_opened": False,
        "stage_ready": False,
        "new_model_fits": 0,
    }
    try:
        original = plan["original"]
        token = os.environ.get("GITHUB_TOKEN")
        metadata = artifact_metadata(original["artifact_id"], token)
        checksum = validate_metadata(
            metadata,
            artifact_id=original["artifact_id"],
            run_id=original["run_id"],
            head=original["head"],
            name=original["name"],
            maximum=plan["transport_maximum_bytes"],
        )
        if checksum != original["zip_sha256"]:
            raise ValueError("portfolio_final_freeze_transport_changed")
        probe.write(args.output / "artifact.json", metadata)
        archive = args.output / "freeze.zip"
        _download_zip(
            original["artifact_id"], archive, checksum, plan["transport_maximum_bytes"], token
        )
        restore_archive(
            archive,
            args.output / "frozen",
            plan,
            roots=frozenset(
                {"models", "freeze.json", "calibration.json", "result.json", "intent.json"}
            ),
        )
        freeze = verified_freeze(args.output / "frozen", plan)
        # Durable whole-parent reservation precedes generation, inspection and
        # all final labels. Source dates outside the scoring role also count.
        probe.write(
            args.output / "intent.json",
            {
                "seed": args.seed,
                "assessment": assessment,
                "assessment_sha256": native.digest(assessment),
                "source_plan": source,
                "freeze_sha256": plan["freeze_sha256"],
                "whole_parent_reserved": True,
                "final_test_opened": True,
                "maximum_attempts": 1,
                "run_id": os.environ["GITHUB_RUN_ID"],
                "consumer_commit": os.environ["GITHUB_SHA"],
                "frozen_model_bundle_sha256": freeze["model_bundle_sha256"],
            },
        )
        result["final_test_opened"] = True
        producer_probe = probe.monitor(
            [
                str(args.producer_python),
                "-I",
                "-B",
                str(ROOT / "scripts/ai09_preparation_worker.py"),
                "--source",
                str(args.source),
                "--output",
                str(args.output / "producer-runtime.json"),
                "--inspect-runtime",
                "producer",
            ],
            cwd=args.source,
            env=environment(args.output),
            log=args.output / "producer-runtime.log",
            roots=(args.output,),
            budgets=budgets,
            deadline=min(started + budgets["wall_seconds"], time.perf_counter() + 120),
        )
        result["resources"]["producer_runtime"] = producer_probe
        if producer_probe["status"] != "passed":
            raise ValueError("portfolio_final_producer_runtime_inspection_failed")
        probe.write(
            args.output / "identity.json",
            {
                "producer_commit": source["producer_commit"],
                "consumer_commit": os.environ["GITHUB_SHA"],
                "producer_runtime_sha256": native.digest(
                    probe.read(args.output / "producer-runtime.json")
                ),
                "consumer_runtime_sha256": native.digest(native.runtime(ROOT, producer=False)),
                "intent_sha256": probe.sha(args.output / "intent.json"),
            },
        )
        for operation in (*probe.PHASES, "prepare", "infer", "score"):
            probe.write(
                args.output / (operation + "-intent.json"),
                {
                    "operation": operation,
                    "seed": args.seed,
                    "maximum_attempts": 1,
                    "final_test_opened": True,
                },
            )
            interpreter = (
                str(args.producer_python) if operation in probe.PHASES[:3] else sys.executable
            )
            measured = probe.monitor(
                [
                    interpreter,
                    "-I",
                    "-B",
                    str(Path(__file__).resolve()),
                    "--source",
                    str(args.source),
                    "--output",
                    str(args.output),
                    "--operation",
                    operation,
                ],
                cwd=ROOT,
                env=environment(args.output),
                log=args.output / (operation + ".log"),
                roots=(args.output, args.source / "data/generated/ai09-capacity-snapshot"),
                budgets=budgets,
                deadline=started + budgets["wall_seconds"],
            )
            result["resources"][operation] = measured
            if measured["status"] != "passed":
                raise ValueError("portfolio_final_" + operation + "_failed")
        result["status"] = "completed_frozen_final_seed"
        result["report_sha256"] = probe.sha(args.output / "report.json")
    except Exception as exc:
        result.update(status="failed", error_code=str(exc))
        raise
    finally:
        result["wall_seconds_including_transport"] = time.perf_counter() - started
        probe.write(args.output / "result.json", result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--producer-python", type=Path)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--operation", choices=(*probe.PHASES, "prepare", "infer", "score"))
    args = parser.parse_args()
    if args.operation:
        worker(args)
    elif args.seed is None or args.producer_python is None:
        parser.error("--seed and --producer-python are required for a final run")
    else:
        run(args)
