"""One explicit model-only repair, retaining the failed attempt and its original data."""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import measure_ai09_development_capacity as probe  # noqa: E402
import run_ai09_portfolio_development as development  # noqa: E402
from ai09_preparation_worker import digest, runtime  # noqa: E402

PLAN = ROOT / "docs/reference/ai09-portfolio-development-repair-v1.json"


def frozen_plan() -> dict[str, Any]:
    plan = probe.read(PLAN)
    if (
        plan["version"] != "ai09-portfolio-development-repair-1.0.0"
        or plan["maximum_additional_comparison_attempts"] != 1
        or any(
            plan[key] is not False
            for key in ("source_regenerated", "final_test_opened", "stage_ready")
        )
        or plan["comparison_policy"] != "identical_to_ai09-portfolio-development-v1.json"
    ):
        raise ValueError("portfolio_repair_scope_changed")
    return plan


def restore_archive(archive: Path, output: Path, plan: dict[str, Any]) -> None:
    """Restore only the pinned original Actions ZIP into a fresh private root."""
    from retailops_ai.source_snapshot.files import file_hash

    if (
        archive.is_symlink()
        or archive.stat().st_size != plan["original"]["zip_size_bytes"]
        or file_hash(archive.parent, archive.name)[1] != plan["original"]["zip_sha256"]
        or output.exists()
    ):
        raise ValueError("portfolio_repair_archive_identity")
    with zipfile.ZipFile(archive) as zipped:
        entries = zipped.infolist()
        names = [entry.filename for entry in entries]
        if (
            len(entries) > 10000
            or len(names) != len(set(names))
            or sum(entry.file_size for entry in entries) > plan["transport_maximum_bytes"]
        ):
            raise ValueError("portfolio_repair_archive_budget")
        for entry in entries:
            path = PurePosixPath(entry.filename)
            if (
                path.is_absolute()
                or not path.parts
                or path.parts[0] not in {"control", "control-preflight"}
                or any(part in {"", ".", ".."} for part in entry.filename.rstrip("/").split("/"))
                or any(c in entry.filename for c in ("\\", "\x00", ":"))
                or stat.S_IFMT(entry.external_attr >> 16) not in {0, stat.S_IFREG, stat.S_IFDIR}
                or entry.flag_bits & 1
            ):
                raise ValueError("portfolio_repair_unsafe_archive")
        output.mkdir(mode=0o700)
        for entry in entries:
            target = output / entry.filename
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
            else:
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with zipped.open(entry) as source, target.open("xb") as destination:
                    shutil.copyfileobj(source, destination, length=1024**2)
                if target.stat().st_size != entry.file_size:
                    raise ValueError("portfolio_repair_member_size")


def original_parents(restored: Path, plan: dict[str, Any]) -> dict[str, Path]:
    original = restored / "control"
    if probe.read(original / "preparation-plan.json") != development.frozen_plan():
        raise ValueError("portfolio_repair_original_plan_changed")
    result = probe.read(original / "portfolio-development-result.json")
    attempt = probe.read(original / "comparison/attempt.json")
    parents = probe.read(original / "forecast-parents.json")
    if (
        result["status"] != "failed"
        or result["preparation"]["status"] != "complete"
        or result["preparation"]["completed_phases"] != 5
        or attempt["error_code"] != "forecast_model_fit_final_budget_exceeded"
        or parents["final_test_opened"] is not False
    ):
        raise ValueError("portfolio_repair_original_attempt_changed")
    output = {}
    for key, relative in plan["parents"].items():
        if parents[key] != plan["original_path_prefix"] + relative:
            raise ValueError("portfolio_repair_parent_path_changed")
        path = (restored / relative).resolve(strict=True)
        if not path.is_relative_to(original.resolve()) or not path.is_dir():
            raise ValueError("portfolio_repair_parent_outside_restore")
        output[key] = path
    if probe.read(original / "comparison/protocol.json")["policy"] != development.comparison_policy(
        development.frozen_plan()
    ).model_dump(mode="json"):
        raise ValueError("portfolio_repair_must_keep_original_model_policy")
    return output


def execute(output: Path) -> None:
    from retailops_ai.evaluation_campaign.development import run_development_comparison

    plan = frozen_plan()
    if probe.read(output / "plan.json") != plan:
        raise ValueError("portfolio_repair_plan_changed")
    identity = probe.read(output / "identity.json")
    probe.clean_pin(ROOT, identity["commit"])
    if digest(runtime(ROOT, producer=False)) != identity["runtime_sha256"]:
        raise ValueError("portfolio_repair_runtime_changed")
    parents = original_parents(output / "original", plan)
    # Native comparison verifies feature, split and curated identities/bytes and
    # nonempty role membership before fitting. No old preparation phase is rerun.
    report = run_development_comparison(
        features=parents["features"],
        split=parents["split"],
        curated=parents["curated"],
        fold_name=development.frozen_plan()["comparison"]["fold"]["name"],
        output=output / "comparison",
        policy=development.comparison_policy(development.frozen_plan()),
    )
    if digest(runtime(ROOT, producer=False)) != identity["runtime_sha256"]:
        raise ValueError("portfolio_repair_runtime_changed_during_fit")
    probe.write(output / "completed.json", report)


def run(output: Path) -> None:
    from ai09_checkpoint_transport import validate_metadata
    from download_forecast_cohort_checkpoint import _download_zip, artifact_metadata
    from run_ai09_preparation import environment

    probe.require_remote()
    plan = frozen_plan()
    if os.environ.get("GITHUB_RUN_ATTEMPT") != "1":
        raise ValueError("portfolio_repair_automatic_rerun_forbidden")
    commit = os.environ["GITHUB_SHA"]
    probe.clean_pin(ROOT, commit)
    if not output.is_absolute() or any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError("portfolio_repair_absolute_unlinked_output_required")
    output.mkdir(mode=0o700)  # Existing attempt cannot be silently retried.
    probe.write(output / "plan.json", plan)
    probe.write(
        output / "identity.json",
        {
            "commit": commit,
            "runtime_sha256": digest(runtime(ROOT, producer=False)),
            "run_id": os.environ["GITHUB_RUN_ID"],
            "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
        },
    )
    started = time.perf_counter()
    result: dict[str, Any] = {
        "status": "running",
        "original": plan["original"],
        "source_regenerated": False,
        "final_test_opened": False,
        "stage_ready": False,
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
            raise ValueError("portfolio_repair_remote_digest_changed")
        probe.write(output / "github-artifact.json", metadata)
        archive = output / "original.zip"
        _download_zip(
            original["artifact_id"], archive, checksum, plan["transport_maximum_bytes"], token
        )
        restore_archive(archive, output / "original", plan)
        original_parents(output / "original", plan)
        probe.write(output / "fit-intent.json", {"maximum_attempts": 1, "original": original})
        measured = probe.monitor(
            [
                sys.executable,
                "-I",
                "-B",
                str(Path(__file__).resolve()),
                "--output",
                str(output),
                "--worker",
            ],
            cwd=ROOT,
            env=environment(output),
            log=output / "comparison.log",
            roots=(output,),
            budgets=plan["budgets"],
            deadline=started + plan["budgets"]["wall_seconds"],
        )
        result["resources"] = measured
        if measured["status"] != "passed":
            raise ValueError("portfolio_repair_comparison_failed")
        result["comparison_id"] = probe.read(output / "completed.json")["comparison_id"]
        result["status"] = "completed_development_diagnostic"
    except Exception as exc:
        result.update(status="failed", error_code=str(exc))
        raise
    finally:
        result["repair_wall_seconds_including_transport"] = time.perf_counter() - started
        probe.write(output / "result.json", result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    execute(args.output) if args.worker else run(args.output)
