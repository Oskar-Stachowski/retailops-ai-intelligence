"""Prepare pinned stockout cohorts on an isolated runner; never score final outcomes.

The native and installed-wheel development proof is restricted to seed42. Other
seeds measure preparation mechanics and retain unopened holdout membership.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PRODUCER = "08639e9188badb352ed64686a088fe237badad41"
CONSUMER = "4faaf4b6c1997fda3a609645595165643bf302a9"
RESERVE = 6 * 1024**3
MAX_ARCHIVE = 512 * 1024**2


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        while raw := stream.read(1024**2):
            h.update(raw)
    return h.hexdigest()


def read(path: Path, cap: int = 1024**2) -> Any:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > cap:
        raise ValueError("stockout_remote_invalid_control_file")
    with path.open("rb") as stream:
        return json.load(stream)


def write(path: Path, value: Any) -> None:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() + b"\n"
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o600)


def head(root: Path) -> str:
    return subprocess.check_output(  # noqa: S603 - fixed read-only git invocation
        [git_binary(), "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()  # noqa: S603 - pinned read-only git invocation


def git_binary() -> str:
    git = shutil.which("git")
    if git is None:
        raise ValueError("stockout_remote_git_unavailable")
    return git


def plan(control: Path, consumer: Path, source: Path, seed: int) -> dict[str, Any]:
    if seed not in (42, 137, 2026) or head(consumer) != CONSUMER or head(source) != PRODUCER:
        raise ValueError("stockout_remote_commit_or_seed_boundary")
    profile_path = control / f"docs/reference/stockout-resource-pilot-1.7-remote-seed{seed}.json"
    profile = read(profile_path)
    baseline_path = control / "docs/reference/stockout-resource-baseline-30.json"
    if (
        profile["schema_version"] != "stockout-resource-pilot-1.7.0"
        or profile["execution_scope"] != "isolated_github_hosted_runner"
        or profile["generation"]["seed"] != seed
        or {k: profile["generation"][k] for k in ("days", "products", "stores", "warehouses")}
        != dict(days=102, products=30, stores=3, warehouses=2)
        or profile["producer_commit"] != PRODUCER
        or profile["consumer_baseline_commit"] != CONSUMER
        or profile["budgets"]
        != dict(
            scratch_bytes=576 * 1024**2,
            tree_rss_bytes=1280 * 1024**2,
            wall_seconds=2700,
            minimum_free_bytes=RESERVE,
        )
        or sha(baseline_path) != profile["measured_resource_baseline"]["sha256"]
    ):
        raise ValueError("stockout_remote_frozen_profile_or_baseline_changed")
    subprocess.check_call([git_binary(), "-C", str(source), "diff", "--quiet", PRODUCER])  # noqa: S603 - pinned read-only git invocation
    subprocess.check_call(  # noqa: S603 - pinned consumer paths, read-only git invocation
        [
            git_binary(),
            "-C",
            str(consumer),
            "diff",
            "--quiet",
            CONSUMER,
            "--",
            "src",
            "contracts",
            "pyproject.toml",
            "uv.lock",
        ]
    )  # noqa: S603
    return dict(
        schema_version="stockout-remote-preparation-plan-1.0.0",
        seed=seed,
        control_commit=head(control),
        consumer_commit=CONSUMER,
        producer_commit=PRODUCER,
        profile_sha256=sha(profile_path),
        baseline_sha256=sha(baseline_path),
        measurement_code_sha256=sha(control / "scripts/measure_stockout_pipeline.py"),
        preparation_code_sha256=sha(Path(__file__).resolve()),
        development_selection_permitted=seed == 42,
        final_test_outcomes_evaluated=False,
        promotion_permitted=False,
        ai08_ready=False,
    )


def worker(args: argparse.Namespace) -> None:
    if args.implementation is None or args.retained is None:
        raise ValueError("stockout_remote_worker_arguments_required")
    sys.path.insert(0, str(args.implementation))
    from retailops_ai.source_snapshot.files import canonical_json
    from retailops_ai.stockout.artifacts import write_artifact
    from retailops_ai.stockout_policy.contract import PolicySpec
    from retailops_ai.stockout_policy.selection import build_selected_proposal
    from retailops_ai.stockout_runtime.export import scoring_artifacts
    from retailops_ai.stockout_selection.bundle import build_selection
    from retailops_ai.stockout_selection.contract import SelectionModelPin, SelectionPolicy
    from retailops_ai.stockout_temporal_storage.store import PartitionInputs, capture

    if args.seed != 42:
        raise ValueError("stockout_remote_development_seed42_only")
    paths = read(args.retained / "consumer.json")["paths"]
    inputs = PartitionInputs(
        Path(paths["curated"]),
        Path(paths["private_import"]) / "snapshot",
        args.retained / "features",
        args.retained / "upstream",
        args.retained / "labels",
    )
    roots = {**inputs.roots(), "temporal": args.retained / "temporal"}
    before = {name: capture(root) for name, root in roots.items()}
    policy = SelectionPolicy.model_validate_json(
        (args.control / "docs/reference/stockout-later-selection-policy.json").read_bytes()
    )
    if args.worker == "selection":
        result = build_selection(
            args.retained / "temporal", inputs, policy, allow_evaluation_truth=True
        )
        identity = result["selection"]["selection_id"]
        details = dict(
            selection_id=identity,
            card_id=result["model_card"]["card_id"],
            selected=result["selection"]["content"]["selected"],
            development_selection_gates=result["selection"]["content"][
                "selected_development_gates"
            ],
        )
    else:
        spec = PolicySpec.model_validate_json(
            (
                args.control / "docs/reference/stockout-operator-development-proposal.json"
            ).read_bytes()
        )
        result = build_selected_proposal(
            args.retained / "temporal", inputs, policy, spec, allow_evaluation_truth=True
        )
        recipe, scoring_policy = scoring_artifacts(result)
        if not isinstance(recipe.pin, SelectionModelPin):
            raise ValueError("stockout_remote_selected_recipe_required")
        write_artifact(
            recipe.model_dump(mode="json"), args.output / "recipe.json", max_bytes=16 * 1024**2
        )
        write_artifact(
            scoring_policy.model_dump(mode="json"),
            args.output / "scoring-policy.json",
            max_bytes=1024**2,
        )
        details = dict(
            proposal_id=result["proposal_id"],
            policy_id=scoring_policy.policy_id,
            selection_id=recipe.pin.selection_id,
        )
    if before != {name: capture(root) for name, root in roots.items()}:
        raise ValueError("stockout_remote_parent_changed_during_development")
    final_evaluated = (
        result["final_test_outcomes_evaluated"]
        if args.worker == "selection"
        else result["content"]["final_test_outcomes_evaluated"]
    )
    if final_evaluated is not False:
        raise ValueError("stockout_remote_final_test_boundary")
    for name, module in tuple(sys.modules.items()):
        module_file = getattr(module, "__file__", None)
        if name.startswith("retailops_ai") and isinstance(module_file, str):
            if not Path(module_file).resolve().is_relative_to(args.implementation.resolve()):
                raise ValueError("stockout_remote_wrong_implementation_import")
    for name in ("data", "ml", "retailops"):
        if importlib.util.find_spec(name) is not None:
            raise ValueError("stockout_remote_producer_importable_in_consumer")
    output = args.output / "capsule.json"
    if write_artifact(result, output, max_bytes=16 * 1024**2) != "published":
        raise ValueError("stockout_remote_output_preexisted")
    if write_artifact(result, output, max_bytes=16 * 1024**2) != "reused":
        raise ValueError("stockout_remote_immutable_reuse_failed")
    write(
        args.output / "proof.json",
        dict(
            status="artifact_passed",
            phase=args.worker,
            **details,
            capsule_sha256=sha(output),
            capsule_bytes=output.stat().st_size,
            implementation_root=str(args.implementation),
            source_parents_unchanged=True,
            parent_seal_sha256=hashlib.sha256(canonical_json(before)).hexdigest(),
            independent_quality_accepted=False,
            final_test_outcomes_evaluated=False,
            thresholds_approved=False,
            model_promoted=False,
            ai08_ready=False,
        ),
    )


def monitored_worker(
    args: argparse.Namespace, retained: Path, implementation: Path, name: str
) -> dict[str, Any]:
    import psutil  # type: ignore[import-untyped]

    phase = "policy" if name == "policy-native" else "selection"
    target = args.output / name
    target.mkdir(mode=0o700)
    (target / "tmp").mkdir(mode=0o700)
    scratch_cap = 512 * 1024**2
    if shutil.disk_usage(target).free < RESERVE + scratch_cap:
        raise ValueError("stockout_remote_development_disk_preflight")
    argv = [
        sys.executable,
        "-I",
        str(Path(__file__).resolve()),
        "--worker",
        phase,
        "--control",
        str(args.control),
        "--seed",
        "42",
        "--retained",
        str(retained),
        "--implementation",
        str(implementation),
        "--output",
        str(target),
    ]
    env = {
        **os.environ,
        "TMPDIR": str(target / "tmp"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
    }
    env.pop("PYTHONPATH", None)
    log = target / "worker.log"
    start = time.perf_counter()
    peak = allocated = logical = 0
    minimum = shutil.disk_usage(target).free
    failure = None
    samples = 0
    spec = importlib.util.spec_from_file_location(
        "remote_probe", args.control / "scripts/measure_stockout_pipeline.py"
    )
    if spec is None or spec.loader is None:
        raise ValueError("stockout_remote_resource_monitor_missing")
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    own = psutil.Process()
    with log.open("w") as stream:
        child = subprocess.Popen(  # noqa: S603 - own fixed script; argv never passed through a shell
            argv,
            cwd=target,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )  # noqa: S603
        try:
            while True:
                rss = 0
                for process in [own, *own.children(recursive=True)]:
                    try:
                        rss += process.memory_info().rss
                    except psutil.NoSuchProcess:
                        pass
                size = probe.tree_size(target)
                free = shutil.disk_usage(target).free
                peak = max(peak, rss)
                allocated = max(allocated, size["allocated_bytes"])
                logical = max(logical, size["logical_bytes"])
                minimum = min(minimum, free)
                samples += 1
                if (
                    rss > 1024**3
                    or max(allocated, logical) > scratch_cap
                    or free < RESERVE
                    or time.perf_counter() - start > 900
                ):
                    failure = "development_live_resource_limit"
                    probe.stop_owned(child)
                    break
                if child.poll() is not None:
                    break
                time.sleep(0.2)
        finally:
            if child.poll() is None:
                probe.stop_owned(child)
        child.wait()
    passed = child.returncode == 0 and failure is None and (target / "proof.json").is_file()
    measurement = dict(
        status="passed" if passed else "failed",
        exit_code=child.returncode,
        failure=failure,
        wall_seconds=time.perf_counter() - start,
        peak_tree_rss_bytes=peak,
        peak_logical_scratch_bytes=logical,
        peak_allocated_scratch_bytes=allocated,
        minimum_free_bytes=minimum,
        samples=samples,
        sample_seconds=0.2,
        budgets=dict(
            tree_rss_bytes=1024**3,
            scratch_bytes=scratch_cap,
            wall_seconds=900,
            minimum_free_bytes=RESERVE,
        ),
        log_sha256=sha(log),
        proof=read(target / "proof.json", 16 * 1024**2)
        if (target / "proof.json").is_file()
        else None,
        final_test_outcomes_evaluated=False,
        ai08_ready=False,
    )
    write(args.output / (name + ".json"), measurement)
    if not passed:
        raise ValueError("stockout_remote_development_proof_failed")
    return measurement


def archive_inputs(retained: Path, output: Path) -> dict[str, Any]:
    """Archive only accepted consumer parents, not code/git/dependencies/credentials."""
    allowed = (
        "facts_import",
        "private_import",
        "curated",
        "features",
        "upstream",
        "labels",
        "temporal",
    )
    files: dict[str, dict[str, Any]] = {}
    total = 0
    for name in allowed:
        root = retained / name
        if root.is_symlink() or not root.is_dir():
            raise ValueError("stockout_remote_missing_parent_tree")
        for p in sorted(root.rglob("*")):
            if p.is_symlink() or not (p.is_file() or p.is_dir()):
                raise ValueError("stockout_remote_parent_symlink_or_special_file")
            if p.is_file():
                s = p.stat()
                relative = p.relative_to(retained).as_posix()
                total += s.st_size
                if len(files) >= 4096 or total > MAX_ARCHIVE:
                    raise ValueError("stockout_remote_archive_input_limit")
                files[relative] = dict(bytes=s.st_size, sha256=sha(p), mode=s.st_mode & 0o777)
    archive = output / "parents.tar.gz"
    fd, temporary = tempfile.mkstemp(prefix=".parents-", dir=output)
    os.close(fd)
    staged = Path(temporary)
    try:
        with tarfile.open(staged, "w:gz", compresslevel=6) as tar:
            for relative in files:
                tar.add(retained / relative, arcname=relative, recursive=False)
                if staged.stat().st_size > MAX_ARCHIVE or shutil.disk_usage(output).free < RESERVE:
                    raise ValueError("stockout_remote_archive_resource_limit")
        with tarfile.open(staged, "r:gz") as tar:
            members = tar.getmembers()
            if len(members) != len(files) or {m.name for m in members} != set(files):
                raise ValueError("stockout_remote_archive_member_inventory")
            for m in members:
                ref = files[m.name]
                if not m.isfile() or m.size != ref["bytes"] or m.mode != ref["mode"]:
                    raise ValueError("stockout_remote_archive_file_metadata")
                stream = tar.extractfile(m)
                if stream is None:
                    raise ValueError("stockout_remote_archive_file_unavailable")
                h = hashlib.sha256()
                while raw := stream.read(1024**2):
                    h.update(raw)
                if h.hexdigest() != ref["sha256"] or sha(retained / m.name) != ref["sha256"]:
                    raise ValueError("stockout_remote_archive_content_changed")
        os.replace(staged, archive)
        archive.chmod(0o600)
    finally:
        if staged.exists():
            staged.unlink()
    paths = read(retained / "consumer.json")["paths"]
    mapping = {
        n: Path(paths[n]).relative_to(retained).as_posix()
        for n in ("facts_import", "private_import", "curated")
    }
    return dict(
        archive_file=archive.name,
        archive_sha256=sha(archive),
        archive_bytes=archive.stat().st_size,
        unpacked_bytes=total,
        files=files,
        parent_roots=mapping,
        verified_lossless=True,
        classification="synthetic_evaluation_parent_package;final_outcomes_not_scored",
        final_test_outcomes_evaluated=False,
    )


def prepare(args: argparse.Namespace) -> None:
    frozen = plan(args.control, args.consumer, args.source, args.seed)
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    write(args.output / "plan.json", frozen)
    measurement = args.control / "scripts/measure_stockout_pipeline.py"
    copied = args.consumer / "scripts/measure_stockout_pipeline.py"
    if not copied.exists() or copied.read_bytes() != measurement.read_bytes():
        shutil.copyfile(measurement, copied)
    receipt = args.output / "resource.json"
    argv = [
        sys.executable,
        "-P",
        str(copied),
        "--producer",
        str(args.source),
        "--receipt",
        str(receipt),
        "--pilot-config",
        str(
            args.control / f"docs/reference/stockout-resource-pilot-1.7-remote-seed{args.seed}.json"
        ),
        "--baseline-resource",
        str(args.control / "docs/reference/stockout-resource-baseline-30.json"),
    ]
    with (args.output / "resource.log").open("w") as stream:
        subprocess.check_call(argv, cwd=args.consumer, stdout=stream, stderr=subprocess.STDOUT)  # noqa: S603
    result = read(receipt, 4 * 1024**2)
    if not (
        result["status"] == "passed"
        and result["whole_pilot_budget_passed"] is True
        and result["final_test_outcomes_evaluated"] is False
    ):
        raise ValueError("stockout_remote_resource_not_qualified")
    retained = Path(result["retained_root"])
    upload = args.output / "verified-upload"
    upload.mkdir(mode=0o700)
    parents = archive_inputs(retained, upload)
    for name in ("resource.json", "plan.json"):
        shutil.copyfile(args.output / name, upload / name)
    # Retain a verified preparation checkpoint even if a later model proof fails.
    # This is data preparation, with no development/final quality acceptance.
    write(
        upload / "source-checkpoint.json",
        dict(
            schema_version="stockout-remote-source-checkpoint-1.0.0",
            plan=frozen,
            parents=parents,
            resource_receipt_sha256=sha(receipt),
            development_proofs={},
            independent_quality_accepted=False,
            final_test_outcomes_evaluated=False,
            thresholds_approved=False,
            model_promoted=False,
            ai08_ready=False,
        ),
    )
    proofs = {}
    if args.seed == 42:
        native = monitored_worker(args, retained, args.control / "src", "selection-native")
        wheel_dir = args.output / "wheel"
        wheel_dir.mkdir(mode=0o700)
        uv = shutil.which("uv")
        if uv is None:
            raise ValueError("stockout_remote_uv_unavailable")
        subprocess.check_call(  # noqa: S603 - trusted UV binary; fixed build options
            [
                uv,
                "build",
                "--python",
                sys.executable,
                "--no-build-isolation",
                "--wheel",
                "--out-dir",
                str(wheel_dir),
            ],
            cwd=args.control,
        )  # noqa: S603
        wheels = list(wheel_dir.glob("*.whl"))
        if len(wheels) != 1:
            raise ValueError("stockout_remote_wheel_inventory")
        installed = args.output / "installed"
        subprocess.check_call(  # noqa: S603 - install exactly the wheel just built into an owned prefix
            [
                uv,
                "pip",
                "install",
                "--python",
                sys.executable,
                "--no-deps",
                "--target",
                str(installed),
                str(wheels[0]),
            ]
        )  # noqa: S603
        installed_proof = monitored_worker(args, retained, installed, "selection-wheel")
        if native["proof"]["capsule_sha256"] != installed_proof["proof"]["capsule_sha256"]:
            raise ValueError("stockout_remote_native_wheel_capsule_mismatch")
        proposed = monitored_worker(args, retained, args.control / "src", "policy-native")
        if proposed["proof"]["selection_id"] != native["proof"]["selection_id"]:
            raise ValueError("stockout_remote_policy_selection_pin_mismatch")
        proofs = dict(
            native=native,
            wheel=installed_proof,
            policy=proposed,
            wheel_file=wheels[0].name,
            wheel_sha256=sha(wheels[0]),
        )
    artifact_files = {"resource.json": receipt, "plan.json": args.output / "plan.json"}
    if args.seed == 42:
        for phase in ("selection-native", "selection-wheel", "policy-native"):
            artifact_files[phase + ".json"] = args.output / (phase + ".json")
        artifact_files.update(
            {
                "selection.json": args.output / "selection-native/capsule.json",
                "policy-proposal.json": args.output / "policy-native/capsule.json",
                "recipe.json": args.output / "policy-native/recipe.json",
                "scoring-policy.json": args.output / "policy-native/scoring-policy.json",
            }
        )
    for name, path in artifact_files.items():
        if name not in {"resource.json", "plan.json"}:
            shutil.copyfile(path, upload / name)
    package = dict(
        schema_version="stockout-remote-checkpoint-1.0.0",
        recorded_at_utc=datetime.now(UTC).isoformat(),
        plan=frozen,
        parents=parents,
        resource_receipt_sha256=sha(receipt),
        development_proofs=proofs,
        public_files={
            n: dict(sha256=sha(upload / n), bytes=(upload / n).stat().st_size)
            for n in artifact_files
        },
        independent_quality_accepted=False,
        final_test_outcomes_evaluated=False,
        thresholds_approved=False,
        model_promoted=False,
        ai08_ready=False,
    )
    write(upload / "checkpoint.json", package)
    print(
        json.dumps(
            dict(
                status="checkpoint_passed",
                seed=args.seed,
                checkpoint_sha256=sha(upload / "checkpoint.json"),
                parent_archive_bytes=parents["archive_bytes"],
                final_test_outcomes_evaluated=False,
                ai08_ready=False,
            )
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--consumer", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--seed", type=int, choices=(42, 137, 2026), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", choices=("selection", "policy"))
    parser.add_argument("--retained", type=Path)
    parser.add_argument("--implementation", type=Path)
    args = parser.parse_args()
    for key in ("control", "consumer", "source", "output", "retained", "implementation"):
        if getattr(args, key) is not None:
            setattr(args, key, getattr(args, key).resolve())
    try:
        if args.worker:
            worker(args)
        else:
            if args.consumer is None or args.source is None:
                parser.error("prepare requires consumer and source")
            prepare(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(
            json.dumps(
                dict(
                    status="failed",
                    reason="stockout_remote_preparation_failed",
                    failure_type=type(exc).__name__,
                    final_test_outcomes_evaluated=False,
                    ai08_ready=False,
                )
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
