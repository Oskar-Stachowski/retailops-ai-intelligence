"""Finish the three original final datasets after a preparation wiring failure; no refits."""

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
sys.path.insert(0, str(ROOT / "src"))

import measure_ai09_development_capacity as probe  # noqa: E402
import run_ai09_portfolio_final as final  # noqa: E402
from ai09_preparation_worker import digest, runtime  # noqa: E402
from run_ai09_portfolio_repair import restore_archive  # noqa: E402

PLAN = ROOT / "docs/reference/ai09-portfolio-final-repair-v1.json"


def frozen_plan(seed: int) -> dict[str, Any]:
    plan = probe.read(PLAN)
    if (
        type(seed) is not int
        or seed not in (42, 137, 2026)
        or set(plan["seeds"]) != {"42", "137", "2026"}
        or plan["maximum_additional_assessment_attempts_per_seed"] != 1
        or any(
            plan[key] is not False
            for key in (
                "source_regenerated",
                "models_refitted",
                "calibration_changed",
                "stage_ready",
            )
        )
        or plan["repeat_final_parent_access_disclosed"] is not True
        or plan["original_final_plan_sha256"] != probe.sha(final.PLAN)
        or plan["budgets"] != final.source_plan(seed)["budgets"]
    ):
        raise ValueError("portfolio_final_repair_scope_changed")
    return plan


def original_binding(output: Path, seed: int, plan: dict[str, Any]) -> tuple[Path, Path]:
    """Map pinned original paths without rewriting any original receipt or data."""
    entry = plan["seeds"][str(seed)]
    original = output / "original/final"
    for name, checksum in entry["receipt_sha256"].items():
        if probe.sha(original / name) != checksum:
            raise ValueError("portfolio_final_repair_original_receipt_changed")
    intent = probe.read(original / "intent.json")
    identity = probe.read(original / "identity.json")
    result = probe.read(original / "result.json")
    if (
        intent["seed"] != seed
        or result["seed"] != seed
        or intent["run_id"] != str(entry["original"]["run_id"])
        or identity["consumer_commit"] != entry["original"]["head"]
        or identity["intent_sha256"] != probe.sha(original / "intent.json")
        or intent["source_plan"] != final.source_plan(seed)
        or intent["assessment"] != probe.read(final.ASSESSMENT)
        or intent["assessment_sha256"] != digest(intent["assessment"])
        or intent["freeze_sha256"] != probe.read(final.PLAN)["freeze_sha256"]
        or intent["whole_parent_reserved"] is not True
        or intent["final_test_opened"] is not True
        or result["status"] != "failed"
        or result["error_code"] != "portfolio_final_prepare_failed"
        or result["new_model_fits"] != 0
        or result["final_test_opened"] is not True
        or any(result["resources"][p]["status"] != "passed" for p in probe.PHASES)
        or any(p in result["resources"] for p in ("infer", "score"))
        or any((original / p).exists() for p in ("report.json", "predictions.jsonl", "final-data"))
    ):
        raise ValueError("portfolio_final_repair_original_attempt_not_eligible")
    final.verified_freeze(original / "frozen", probe.read(final.PLAN))
    paths = []
    for name, suffix in (("import.json", "snapshot"), ("curation.json", "")):
        destination = Path(probe.read(original / name)["destination"])
        relative = destination.relative_to(plan["original_output_prefix"])
        path = (original / relative / suffix).resolve(strict=True)
        if not path.is_relative_to(original.resolve()) or not path.is_dir():
            raise ValueError("portfolio_final_repair_parent_outside_original")
        paths.append(path)
    return paths[0], paths[1]


def execute(output: Path, seed: int, operation: str) -> None:
    from retailops_ai.evaluation_campaign.portfolio_assessment import metrics
    from retailops_ai.evaluation_campaign.portfolio_data_io import scored_rows
    from retailops_ai.evaluation_campaign.portfolio_final_data import prepare_final

    plan = frozen_plan(seed)
    identity = probe.read(output / "identity.json")
    probe.clean_pin(ROOT, identity["commit"])
    before = digest(runtime(ROOT, producer=False))
    if (
        before != identity["runtime_sha256"]
        or probe.sha(output / "intent.json") != identity["intent_sha256"]
    ):
        raise ValueError("portfolio_final_repair_execution_changed")
    parents = original_binding(output, seed, plan)
    intent = probe.read(output / "intent.json")
    frozen = output / "original/final/frozen"
    if operation == "prepare":
        prepare_final(
            output, intent["assessment"], intent["source_plan"]["generation"], parent_paths=parents
        )
    elif operation == "infer":
        from run_ai09_portfolio_assessment import infer

        infer(output, frozen / "models")
    else:
        freeze = final.verified_freeze(frozen, probe.read(final.PLAN))
        report = metrics(
            scored_rows(output), probe.read(frozen / "calibration.json"), freeze["required_groups"]
        )
        report.update(seed=seed, freeze_sha256=intent["freeze_sha256"], final_test_opened=True)
        probe.write(output / "report.json", report)
    if digest(runtime(ROOT, producer=False)) != before:
        raise ValueError("portfolio_final_repair_runtime_changed_during_operation")


def run(output: Path, seed: int) -> None:
    import psutil  # type: ignore[import-untyped]
    from ai09_checkpoint_transport import validate_metadata
    from download_forecast_cohort_checkpoint import _download_zip, artifact_metadata
    from run_ai09_preparation import environment

    probe.require_remote()
    if os.environ.get("GITHUB_RUN_ATTEMPT") != "1":
        raise ValueError("portfolio_final_repair_automatic_rerun_forbidden")
    plan = frozen_plan(seed)
    probe.clean_pin(ROOT, os.environ["GITHUB_SHA"])
    if not output.is_absolute() or any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError("portfolio_final_repair_owned_absolute_output_required")
    budgets = plan["budgets"]
    if (
        psutil.virtual_memory().available
        < budgets["tree_rss_bytes"] + budgets["minimum_available_memory_bytes"]
        or shutil.disk_usage(output.parent).free
        < budgets["scratch_bytes"] + budgets["minimum_free_disk_bytes"]
    ):
        raise ValueError("portfolio_final_repair_preflight_reserve")
    output.mkdir(mode=0o700)
    probe.write(output / "plan.json", plan)
    started = time.perf_counter()
    original = plan["seeds"][str(seed)]["original"]
    result: dict[str, Any] = {
        "status": "running",
        "seed": seed,
        "original": original,
        "resources": {},
        "source_regenerated": False,
        "new_model_fits": 0,
        "calibration_changed": False,
        "repeat_final_parent_access_disclosed": True,
        "final_test_opened": True,
        "stage_ready": False,
    }
    try:
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
            raise ValueError("portfolio_final_repair_transport_changed")
        probe.write(output / "artifact.json", metadata)
        archive = output / "original.zip"
        _download_zip(
            original["artifact_id"], archive, checksum, plan["transport_maximum_bytes"], token
        )
        restore_archive(
            archive,
            output / "original",
            plan | {"original": original},
            roots=frozenset({"final", "final-preflight"}),
        )
        original_binding(output, seed, plan)
        old = output / "original/final"
        result["original_result"] = probe.read(old / "result.json")
        intent = probe.read(old / "intent.json") | {
            "repair_run_id": os.environ["GITHUB_RUN_ID"],
            "repair_commit": os.environ["GITHUB_SHA"],
            "original_intent_sha256": probe.sha(old / "intent.json"),
            "repeat_final_parent_access_disclosed": True,
            "maximum_additional_assessment_attempts": 1,
        }
        probe.write(output / "intent.json", intent)
        probe.write(
            output / "identity.json",
            {
                "commit": os.environ["GITHUB_SHA"],
                "runtime_sha256": digest(runtime(ROOT, producer=False)),
                "intent_sha256": probe.sha(output / "intent.json"),
                "original_zip_sha256": checksum,
            },
        )
        for operation in ("prepare", "infer", "score"):
            probe.write(
                output / (operation + "-intent.json"),
                {
                    "operation": operation,
                    "seed": seed,
                    "maximum_additional_attempts": 1,
                    "final_test_opened": True,
                },
            )
            measured = probe.monitor(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(Path(__file__).resolve()),
                    "--output",
                    str(output),
                    "--seed",
                    str(seed),
                    "--operation",
                    operation,
                ],
                cwd=ROOT,
                env=environment(output),
                log=output / (operation + ".log"),
                roots=(output,),
                budgets=budgets,
                deadline=started + budgets["wall_seconds"],
            )
            result["resources"][operation] = measured
            if measured["status"] != "passed":
                raise ValueError("portfolio_final_repair_" + operation + "_failed")
        result.update(
            status="completed_frozen_final_seed", report_sha256=probe.sha(output / "report.json")
        )
    except Exception as exc:
        result.update(status="failed", error_code=str(exc))
        raise
    finally:
        result["repair_wall_seconds_including_transport"] = time.perf_counter() - started
        probe.write(output / "result.json", result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--operation", choices=("prepare", "infer", "score"))
    args = parser.parse_args()
    if args.operation:
        execute(args.output, args.seed, args.operation)
    else:
        run(args.output, args.seed)
