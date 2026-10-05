"""Supervise an explicitly authorized final world and independent installed-wheel replay."""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil  # type: ignore[import-untyped]

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.stockout_campaign.assembly import guard
from retailops_ai.stockout_campaign.contract import CampaignFreeze, CampaignPermission
from retailops_ai.stockout_campaign.evaluation import bound_recipes
from retailops_ai.stockout_campaign.implementation import code_digest, lock_digest
from retailops_ai.stockout_lifecycle.release import receipt
from retailops_ai.stockout_runtime.contracts import ScoringPolicy, ScoringRecipe

LIMITS = dict(
    tree_rss_bytes=1280 * 1024**2,
    scratch_bytes=640 * 1024**2,
    wall_seconds=2700,
    minimum_free_bytes=6 * 1024**3,
)


def tree_size(root: Path) -> tuple[int, int]:
    logical = allocated = 0
    for parent, dirs, names in os.walk(root, followlinks=False):
        for name in [*dirs, *names]:
            path = Path(parent) / name
            if path.is_symlink():
                raise ValueError("stockout_final_scratch_symlink")
            try:
                info = path.stat()
            except FileNotFoundError:
                continue
            logical += info.st_size
            allocated += info.st_blocks * 512
    return logical, allocated


def stop_owned(child: subprocess.Popen[bytes]) -> None:
    if child.poll() is None:
        os.killpg(child.pid, signal.SIGTERM)
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)


def execute(args: argparse.Namespace) -> dict[str, Any]:
    if (
        sys.platform != "linux"
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_OS") != "Linux"
        or os.environ.get("GITHUB_REPOSITORY") != "Oskar-Stachowski/retailops-ai-intelligence"
    ):
        raise ValueError("stockout_final_requires_owned_github_runner")
    commit = os.environ.get("GITHUB_SHA", "")
    run_id = int(os.environ.get("GITHUB_RUN_ID", "0"))
    attempt = int(os.environ.get("GITHUB_RUN_ATTEMPT", "0"))
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or min(run_id, attempt) < 1:
        raise ValueError("stockout_final_execution_receipt_identity_required")
    freeze = CampaignFreeze.model_validate_json(args.freeze.read_bytes())
    permission = CampaignPermission.model_validate_json(args.permission.read_bytes())
    source = next(s for s in freeze.sources if (s.world, s.seed) == (args.world, args.seed))
    guard(freeze, permission, source)
    bound_recipes(
        freeze,
        ScoringRecipe.model_validate_json(args.recipe.read_bytes()),
        ScoringPolicy.model_validate_json(args.policy.read_bytes()),
    )
    # Validate both environments before downloading or reading a target.
    if not args.wheel_python.is_file():
        raise ValueError("stockout_final_independent_wheel_python_required")
    environment = {**os.environ, "PYTHONNOUSERSITE": "1"}
    environment.pop("PYTHONPATH", None)
    wheel_probe = subprocess.run(  # noqa: S603 - fixed Python probe, no data access
        [
            str(args.wheel_python),
            "-P",
            "-c",
            "import json,sys; from pathlib import Path; import retailops_ai.stockout_campaign as c; from retailops_ai.stockout_campaign.implementation import code_digest,lock_digest; print(json.dumps(dict(prefix=sys.prefix,module=str(Path(c.__file__).resolve()),code=code_digest(),lock=lock_digest())))",
        ],
        capture_output=True,
        check=True,
        env=environment,
        timeout=30,
    )
    wheel_identity = json.loads(wheel_probe.stdout)
    if (
        Path(wheel_identity["prefix"]).resolve() == Path(sys.prefix).resolve()
        or not Path(wheel_identity["module"]).is_relative_to(
            Path(wheel_identity["prefix"]).resolve()
        )
        or wheel_identity["code"] != code_digest()
        or wheel_identity["lock"] != lock_digest()
    ):
        raise ValueError("stockout_final_wheel_install_or_content_changed")
    if args.output.exists() or args.output.is_symlink():
        raise ValueError("stockout_final_world_output_immutable")
    if (
        shutil.disk_usage(args.output.parent).free
        < LIMITS["minimum_free_bytes"] + LIMITS["scratch_bytes"]
    ):
        raise ValueError("stockout_final_insufficient_remote_disk_reserve")
    args.output.mkdir(mode=0o700)
    scratch = args.output / "temporary"
    scratch.mkdir(mode=0o700)
    started = time.perf_counter()
    peak_rss = peak_logical = peak_allocated = samples = 0
    minimum = shutil.disk_usage(args.output).free
    phases = []
    failure = None
    equal = False
    try:
        for phase, python in (("native", Path(sys.executable)), ("wheel", args.wheel_python)):
            command = [str(python), "-P", "-m", "retailops_ai.stockout_campaign.runner"]
            for key, value in (
                ("freeze", args.freeze),
                ("permission", args.permission),
                ("recipe", args.recipe),
                ("policy", args.policy),
                ("archive", args.output / "source.zip"),
                ("report", args.output / (phase + ".json")),
                ("audit", args.output / (phase + "-access.jsonl")),
                ("world", args.world),
                ("seed", args.seed),
            ):
                command.extend(["--" + key, str(value)])
            environment = {**os.environ, "TMPDIR": str(scratch.absolute()), "PYTHONNOUSERSITE": "1"}
            environment.pop("PYTHONPATH", None)
            phase_started = time.perf_counter()
            with (args.output / (phase + ".log")).open("xb") as log:
                child = subprocess.Popen(  # noqa: S603 - own typed final evaluation module
                    command,
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                try:
                    while True:
                        rss = 0
                        try:
                            parent = psutil.Process(child.pid)
                            processes = [parent, *parent.children(recursive=True)]
                        except psutil.NoSuchProcess:
                            processes = []
                        for process in processes:
                            try:
                                rss += process.memory_info().rss
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                continue
                        logical, allocated = tree_size(args.output)
                        free = shutil.disk_usage(args.output).free
                        peak_rss = max(peak_rss, rss)
                        peak_logical = max(peak_logical, logical)
                        peak_allocated = max(peak_allocated, allocated)
                        minimum = min(minimum, free)
                        samples += 1
                        if (
                            rss > LIMITS["tree_rss_bytes"]
                            or max(logical, allocated) > LIMITS["scratch_bytes"]
                            or free < LIMITS["minimum_free_bytes"]
                            or time.perf_counter() - started > LIMITS["wall_seconds"]
                        ):
                            failure = "stockout_final_live_resource_limit"
                            stop_owned(child)
                            break
                        if child.poll() is not None:
                            break
                        time.sleep(0.2)
                finally:
                    stop_owned(child)
            phases.append(
                dict(
                    phase=phase,
                    exit_code=child.returncode,
                    wall_seconds=time.perf_counter() - phase_started,
                )
            )
            if child.returncode != 0 or failure:
                raise ValueError(failure or "stockout_final_worker_failed")
        native, wheel = (
            json.loads((args.output / (phase + ".json")).read_bytes())
            for phase in ("native", "wheel")
        )
        if native != wheel:
            raise ValueError("stockout_final_native_wheel_report_mismatch")
        equal = True
        return dict(
            schema_version="stockout-final-world-resource-1.0.0",
            status="passed",
            campaign_id=freeze.campaign_id,
            world=args.world,
            seed=args.seed,
            budgets=LIMITS,
            phases=phases,
            native_wheel_equal=True,
            independent_quality_status=native["status"],
            model_refits=0,
            model_promoted=False,
            ai08_ready=False,
        )
    except BaseException:
        failure = failure or "stockout_final_execution_failed"
        raise
    finally:
        result = dict(
            schema_version="stockout-final-world-resource-1.0.0",
            status="failed" if failure else "passed",
            campaign_id=freeze.campaign_id,
            world=args.world,
            seed=args.seed,
            budgets=LIMITS,
            execution_code_sha256=code_digest(),
            dependency_lock_sha256=lock_digest(),
            execution_commit=commit,
            workflow_run_id=run_id,
            workflow_run_attempt=attempt,
            phases=phases,
            native_wheel_equal=equal,
            receipts={
                name: receipt((args.output / name).read_bytes()).model_dump(mode="json")
                for name in (
                    "native.json",
                    "wheel.json",
                    "native-access.jsonl",
                    "wheel-access.jsonl",
                )
                if (args.output / name).is_file()
            },
            recorded_at=datetime.now(UTC).isoformat(),
            failure=failure,
            measurement=dict(
                wall_seconds=time.perf_counter() - started,
                sampled_tree_peak_rss_bytes=peak_rss,
                sampled_peak_scratch_logical_bytes=peak_logical,
                sampled_peak_scratch_allocated_bytes=peak_allocated,
                minimum_free_bytes=minimum,
                samples=samples,
            ),
            model_refits=0,
            model_promoted=False,
            ai08_ready=False,
        )
        path = args.output / "resource.json"
        path.write_bytes(canonical_bytes(result) + b"\n")
        path.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("freeze", "permission", "recipe", "policy", "wheel-python", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--world", choices=["matching", "future_stress"], required=True)
    parser.add_argument("--seed", type=int, choices=[42, 137, 2026], required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(execute(args)))
        return 0
    except Exception as error:
        print(
            json.dumps(
                dict(
                    status="failed",
                    error_type=type(error).__name__,
                    model_refits=0,
                    ai08_ready=False,
                )
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
