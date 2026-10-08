"""Measure canonical development preparation in an isolated runner, without fits.

This diagnostic generates an exposed development world, not a campaign source or
an untouched final holdout. Each phase runs in its own process group. Its failed
attempts, sampled tree RSS and scratch costs remain in the receipt. No legacy
limit, campaign budget or source provenance is rewritten.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import resource
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from datetime import UTC, date, datetime
from pathlib import Path
from types import FrameType
from typing import Any, TextIO

PHASES = ("generation", "qualification", "export", "import", "curation")
PLAN_PATH = (
    Path(__file__).resolve().parents[1] / "docs/reference/ai09-development-capacity-v1.8.json"
)


def read(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024**2:
        raise ValueError("capacity_invalid_metadata_file")
    result = json.loads(path.read_bytes())
    if not isinstance(result, dict):
        raise ValueError("capacity_metadata_object_required")
    return result


def write(path: Path, document: dict[str, Any]) -> None:
    raw = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(root: Path, *arguments: str) -> str:
    executable = shutil.which("git")
    if executable is None:
        raise ValueError("capacity_git_unavailable")
    return subprocess.check_output(  # noqa: S603 - fixed Git operations; no shell
        [executable, "-C", str(root), *arguments], text=True, timeout=30
    ).strip()


def clean_pin(root: Path, expected: str) -> None:
    if git(root, "rev-parse", "HEAD") != expected or git(root, "diff", "HEAD", "--name-only"):
        raise ValueError("capacity_dirty_or_wrong_revision")


def scratch_size(roots: tuple[Path, ...]) -> tuple[int, int]:
    """Count both allocation and logical size, reject links and special files."""
    logical = allocated = 0
    seen: set[tuple[int, int]] = set()
    for root in roots:
        if not root.exists():
            continue
        for directory, names, files in os.walk(root, followlinks=False):
            for name in (*names, *files):
                path = Path(directory) / name
                try:
                    info = path.lstat()
                except FileNotFoundError:  # A worker can remove its own temporary file.
                    continue
                if stat.S_ISLNK(info.st_mode) or not (
                    stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)
                ):
                    raise ValueError("capacity_invalid_scratch_entry")
                identity = (info.st_dev, info.st_ino)
                if identity in seen:
                    continue
                seen.add(identity)
                logical += info.st_size
                allocated += info.st_blocks * 512
    return logical, allocated


def tree_sample(pid: int) -> tuple[int, dict[tuple[int, float], float]]:
    import psutil  # type: ignore[import-untyped]

    try:
        parent = psutil.Process(pid)
        children = [parent, *parent.children(recursive=True)]
    except psutil.NoSuchProcess:
        return 0, {}
    rss, cpu = 0, {}
    for child in children:
        try:
            rss += child.memory_info().rss
            timing = child.cpu_times()
            cpu[(child.pid, child.create_time())] = timing.user + timing.system
        except psutil.NoSuchProcess:
            pass
    return rss, cpu


def monitor(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log: Path,
    roots: tuple[Path, ...],
    budgets: dict[str, Any],
    deadline: float,
) -> dict[str, Any]:
    """Start only our worker; kill and reap its entire group on any budget/error."""
    import psutil

    started = time.perf_counter()
    peak = logical_peak = allocated_peak = samples = 0
    minimum_disk = shutil.disk_usage(log.parent).free
    minimum_memory = psutil.virtual_memory().available
    observed_cpu: dict[tuple[int, float], float] = {}
    reason: str | None = None
    with log.open("xb") as stream:
        child = subprocess.Popen(  # noqa: S603 - owned interpreter/script/paths, no shell
            command, cwd=cwd, env=env, stdout=stream, stderr=stream, start_new_session=True
        )
        try:
            while True:
                rss, cpu = tree_sample(child.pid)
                # Include the supervisor itself, not only its source/consumer child.
                rss += psutil.Process().memory_info().rss
                observed_cpu.update({k: max(v, observed_cpu.get(k, 0)) for k, v in cpu.items()})
                logical, allocated = scratch_size(roots)
                disk, memory = shutil.disk_usage(log.parent).free, psutil.virtual_memory().available
                samples += 1
                peak = max(peak, rss)
                logical_peak, allocated_peak = (
                    max(logical_peak, logical),
                    max(allocated_peak, allocated),
                )
                minimum_disk, minimum_memory = min(minimum_disk, disk), min(minimum_memory, memory)
                now = time.perf_counter()
                if now >= deadline:
                    reason = "wall_limit"
                elif peak > budgets["tree_rss_bytes"]:
                    reason = "tree_rss_limit"
                elif max(logical_peak, allocated_peak) > budgets["scratch_bytes"]:
                    reason = "scratch_limit"
                elif disk < budgets["minimum_free_disk_bytes"]:
                    reason = "free_disk_reserve"
                elif memory < budgets["minimum_available_memory_bytes"]:
                    reason = "available_memory_reserve"
                elif log.stat().st_size > 16 * 1024**2:
                    reason = "log_limit"
                if reason is not None or child.poll() is not None:
                    break
                time.sleep(budgets["sample_seconds"])
            if reason is None and child.returncode != 0:
                reason = "worker_exit"
        except (OSError, ValueError, psutil.Error) as error:
            reason = "monitor_error_" + type(error).__name__
        finally:
            # Even a successful parent must not leave detached children behind.
            try:
                descendants = psutil.Process(child.pid).children(recursive=True)
            except psutil.NoSuchProcess:
                descendants = []
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=30)
            _, remaining = psutil.wait_procs(descendants, timeout=2)
            if any(p.is_running() and p.status() != psutil.STATUS_ZOMBIE for p in remaining):
                reason = "worker_descendants_unresolved"
    return {
        "status": "passed" if reason is None else "failed",
        "reason": reason,
        "exit_code": child.returncode,
        "wall_seconds": time.perf_counter() - started,
        "sampled_tree_peak_rss_bytes": peak,
        "sampled_worker_cpu_seconds": sum(observed_cpu.values()),
        "cpu_measurement": "sampled_worker_tree_lower_bound_excludes_supervisor_and_unsampled_children",
        "sampled_peak_scratch_logical_bytes": logical_peak,
        "sampled_peak_scratch_allocated_bytes": allocated_peak,
        "minimum_free_disk_bytes": minimum_disk,
        "minimum_available_memory_bytes": minimum_memory,
        "samples": samples,
        "sample_seconds": budgets["sample_seconds"],
        "memory_measurement": "sampled_supervisor_plus_worker_tree_not_continuous_peak",
    }


def producer_worker(args: argparse.Namespace, plan: dict[str, Any]) -> dict[str, Any]:
    sys.path.insert(0, str(args.source))
    configuration = importlib.import_module("data.generator.configuration")
    io = importlib.import_module("data.inventory.source_dataset_io")
    if io.fingerprint()["dependency_sha256"] != plan["producer_lock_sha256"]:
        raise ValueError("capacity_producer_lock_mismatch")
    raw = args.output / "raw"
    if args.worker == "generation":
        configuration_values = {
            k: date.fromisoformat(v) if k in {"start_date", "end_date"} else v
            for k, v in plan["generation"].items()
        }
        generation = configuration.DatasetGenerationConfig(**configuration_values)
        effective = configuration.resolve_generation_config(generation).parameters()
        runner = importlib.import_module("data.inventory.source_cohort_batch_v2")
        result = runner.run(generation, raw)
        if result["status"] != "passed" or not result["facts_ready"]:
            raise ValueError("capacity_generation_facts_not_ready")
        source = read(Path(result["directory"]) / "dataset_manifest.v2.json")
        if (
            source["descriptor"]["resolved_parameters"] != effective
            or source["provenance"]["git_commit"] != plan["producer_commit"]
            or source["provenance"]["code_state"] != "clean"
        ):
            raise ValueError("capacity_generated_provenance_or_configuration_mismatch")
        return {
            "directory": result["directory"],
            "source_dataset_id": result["dataset_id"],
            "resolved_parameters": effective,
            "rows": sum(t["row_count"] for t in result["tables"].values()),
            "tables": result["table_count"],
            "schema_version": source["schema_version"],
        }
    generated = read(args.output / "generation.json")
    if args.worker == "qualification":
        module = importlib.import_module("data.inventory.qualification_io")
        path = module.write_qualification(
            Path(generated["directory"]), args.output / "qualification"
        )
        return {"directory": str(path)}
    module = importlib.import_module("data.export.inventory_snapshot")
    qualified = read(args.output / "qualification.json")
    result = module.export_inventory_snapshot(
        Path(generated["directory"]),
        generated["source_dataset_id"],
        Path(qualified["directory"]),
        args.source / "data/generated/ai09-capacity-snapshot",
        **{**plan["snapshot"], "required_use_cases": tuple(plan["snapshot"]["required_use_cases"])},
    )
    return {"directory": str(result["path"]), "publication": result["publication"]}


def consumer_worker(args: argparse.Namespace, plan: dict[str, Any]) -> dict[str, Any]:
    from retailops_ai.curated.builder import build_curated
    from retailops_ai.source_snapshot.importer import import_snapshot
    from retailops_ai.source_snapshot.protocol import Limits

    limits = Limits(**plan["parent_limits"])
    if args.worker == "import":
        exported = read(args.output / "export.json")
        result = import_snapshot(
            Path(exported["directory"]), args.output / "input/data/generated", limits=limits
        )
        if result.snapshot.manifest["schema_version"] != plan["expected_snapshot_schema_version"]:
            raise ValueError("capacity_expected_snapshot_schema_version")
        return result.summary()
    imported = read(args.output / "import.json")
    curated = build_curated(
        Path(imported["destination"]), args.output / "curated/data/generated", limits=limits
    )
    if curated.manifest["readiness"]["forecast_source"] != "passed":
        raise ValueError("capacity_curated_not_ready")
    return curated.summary()


def require_remote() -> None:
    if (
        sys.platform != "linux"
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_OS") != "Linux"
        or os.environ.get("GITHUB_REPOSITORY") != "Oskar-Stachowski/retailops-ai-intelligence"
    ):
        raise ValueError("capacity_full_profile_requires_isolated_github_runner")


def validate_plan(plan: dict[str, Any]) -> None:
    """Do not let a smaller or final profile inherit this diagnostic's name."""
    previous_path = PLAN_PATH.with_name("ai09-development-capacity-v1.7.json")
    previous = read(previous_path)
    previous_result = read(
        PLAN_PATH.parents[1] / "evidence/09-51-development-capacity-seventh-run.json"
    )
    revision_keys = {
        "version",
        "producer_commit",
        "producer_lock_sha256",
        "generation_entrypoint",
        "previous_attempt",
        "previous_preparation",
        "budgets",
        "revision_reason",
        "worker_stack_observation",
    }
    if {k: v for k, v in plan.items() if k not in revision_keys} != {
        k: v for k, v in previous.items() if k not in revision_keys
    }:
        raise ValueError("capacity_frozen_diagnostic_scope_mismatch")
    if (
        plan["version"] != "ai09-development-capacity-probe-1.8.0"
        or sha(previous_path) != "6d76eb1aebde101a9723697c5190d6254d680b200a2df812faaaccc1dd223668"
        or sha(PLAN_PATH.parents[1] / "evidence/09-51-development-capacity-seventh-run.json")
        != "387ddc47d324992f89a5e51b46bc006eebb4f67dd4c62fffc4c2974a950d4436"
        or plan["producer_commit"] != "4dacf0403add494f74e4b4f22497f6206de753d9"
        or plan["producer_lock_sha256"]
        != "ea389b45f75dec8d4ce476d813ce9cbf8217bb9d5d0bfcfd0cdce5f217975c02"
        or plan.get("previous_preparation")
        != {
            "version": "ai09-development-capacity-probe-1.6.0",
            "recipe": "docs/reference/ai09-development-capacity-v1.6.json",
            "recipe_sha256": "ee655388302c75d564c46cc95ff341d86695fe4b25cf2a6e687ea1cbaddf2956",
            "evidence": "docs/evidence/09-48-audited-capacity-preparation.json",
            "evidence_sha256": "8bbaac6c6a2a703103b89c87dd574aecfd166f2634c221e82b2be0f998f125b3",
            "workflow_dispatched": False,
            "superseded_before_execution": True,
        }
        or sha(PLAN_PATH.with_name("ai09-development-capacity-v1.6.json"))
        != "ee655388302c75d564c46cc95ff341d86695fe4b25cf2a6e687ea1cbaddf2956"
        or sha(PLAN_PATH.parents[1] / "evidence/09-48-audited-capacity-preparation.json")
        != "8bbaac6c6a2a703103b89c87dd574aecfd166f2634c221e82b2be0f998f125b3"
        or plan["worker_stack_observation"]
        != {
            "phases": ["generation"],
            "interval_seconds": 120,
            "repeat": True,
            "locals_dumped": False,
            "periodic_observation_not_allocation_measurement": True,
            "implementation": "python_current_frames_owned_references_no_native_watchdog",
            "maximum_threads": 64,
            "maximum_frames_per_thread": 64,
            "maximum_snapshot_bytes": 65536,
            "maximum_filename_characters": 256,
            "maximum_function_characters": 96,
            "join_timeout_seconds": 5,
            "may_be_delayed_without_GIL": True,
            "truncation_explicit": True,
        }
        or plan["generation_entrypoint"] != "data.inventory.source_cohort_batch_v2.run"
        or plan["scope"] != "isolated_resource_diagnostic_on_previously_exposed_development_dates"
        or plan["expected_snapshot_schema_version"] != "1.1.0"
        or plan["budgets"]
        != {
            "tree_rss_bytes": 12 * 1024**3,
            "scratch_bytes": 8 * 1024**3,
            "wall_seconds": 3600,
            "minimum_free_disk_bytes": 6 * 1024**3,
            "minimum_available_memory_bytes": 1024**3,
            "sample_seconds": 0.2,
        }
        or plan["previous_attempt"]
        != {
            "version": previous["version"],
            "workflow_run": 37807749014,
            "plan_sha256": sha(previous_path),
            "resource_receipt_sha256": previous_result["resource_receipt_sha256"],
            "reason": "tree_rss_limit",
            "exit_code": -9,
            "completed_phases": 0,
            "wall_seconds": previous_result["resource_receipt"]["wall_seconds"],
            "sampled_tree_peak_rss_bytes": previous_result["resource_receipt"]["phases"][0][
                "sampled_tree_peak_rss_bytes"
            ],
            "previous_failure_preserved": True,
        }
        or plan["generation"]
        != {
            "profile": "ai-dev",
            "seed": 42,
            "days": 365,
            "products": 100,
            "stores": 5,
            "warehouses": 3,
            "start_date": "2025-08-01",
            "end_date": "2026-07-31",
            "max_daily_rows": 182500,
            "forecast_plan_days": 14,
        }
        or any(
            plan[k] is not False
            for k in (
                "project_campaign_authorized",
                "model_fit_authorized",
                "final_generation_authorized",
                "quality_qualified",
                "stage_ready",
            )
        )
        or plan["snapshot"]
        != {
            "include_truth": False,
            "chunk_rows": 256,
            "partition_by_day": False,
            "required_use_cases": ["forecast_source", "inventory_source"],
        }
    ):
        raise ValueError("capacity_frozen_diagnostic_scope_mismatch")


def run(args: argparse.Namespace) -> None:  # noqa: PLR0915 - ordered probe evidence and gates
    import psutil

    require_remote()
    plan = read(PLAN_PATH)
    validate_plan(plan)
    source, output = args.source, args.output
    if not source.is_absolute() or not output.is_absolute():
        raise ValueError("capacity_absolute_paths_required")
    if any(p.is_symlink() for p in (source, output, *source.parents, *output.parents)):
        raise ValueError("capacity_no_symlink_paths")
    clean_pin(source, plan["producer_commit"])
    control = PLAN_PATH.parents[2]
    head = git(control, "rev-parse", "HEAD")
    clean_pin(control, head)
    if (
        sha(control / "uv.lock") != plan["consumer_lock_sha256"]
        or sha(source / "data/requirements-parquet.txt") != plan["exporter_lock_sha256"]
    ):
        raise ValueError("capacity_dependency_pin_mismatch")
    snapshot_root = source / "data/generated/ai09-capacity-snapshot"
    if output.exists() or snapshot_root.exists() or source.is_relative_to(output):
        raise ValueError("capacity_fresh_owned_output_required")
    output.mkdir(mode=0o700, parents=True)
    budgets = plan["budgets"]
    preflight = {
        "disk_free": shutil.disk_usage(output).free,
        "available_memory": psutil.virtual_memory().available,
    }
    write(
        output / "plan.json",
        {
            **plan,
            "control_commit": head,
            "plan_sha256": sha(PLAN_PATH),
            "runner_code_sha256": sha(Path(__file__)),
            "preflight": preflight,
            "recorded_at": datetime.now(UTC).isoformat(),
        },
    )
    phases: list[dict[str, Any]] = []
    reason = None
    if preflight["disk_free"] < budgets["scratch_bytes"] + budgets["minimum_free_disk_bytes"]:
        reason = "preflight_disk"
    elif (
        preflight["available_memory"]
        < budgets["tree_rss_bytes"] + budgets["minimum_available_memory_bytes"]
    ):
        reason = "preflight_memory"
    started = time.perf_counter()
    try:
        for phase in PHASES if reason is None else ():
            interpreter = (
                source / ".venv/bin/python" if phase in PHASES[:3] else Path(sys.executable)
            )
            env = {
                **os.environ,
                "OMP_NUM_THREADS": "1",
                "OPENBLAS_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
                "PYTHONNOUSERSITE": "1",
                "PYTHONHASHSEED": "0",
                "TMPDIR": str(output / "temporary"),
            }
            Path(env["TMPDIR"]).mkdir(exist_ok=True, mode=0o700)
            measurement = monitor(
                [
                    str(interpreter),
                    str(Path(__file__).resolve()),
                    "--source",
                    str(source),
                    "--output",
                    str(output),
                    "--worker",
                    phase,
                ],
                cwd=source if phase in PHASES[:3] else control,
                env=env,
                log=output / (phase + ".log"),
                roots=(output, snapshot_root),
                budgets=budgets,
                deadline=started + budgets["wall_seconds"],
            )
            phases.append({"phase": phase, **measurement})
            if measurement["status"] != "passed":
                reason = measurement["reason"]
                break
            result = read(output / (phase + ".json"))
            self_peak = result["worker_peak_self_rss_bytes"]
            if type(self_peak) is not int or self_peak < 1:
                reason = "worker_self_rss_measurement_invalid"
            else:
                measurement["worker_peak_self_rss_bytes"] = self_peak
                phases[-1]["worker_peak_self_rss_bytes"] = self_peak
                if self_peak > budgets["tree_rss_bytes"]:
                    reason = "worker_self_rss_limit"
            if reason is not None:
                phases[-1].update(status="failed", reason=reason)
                break
        clean_pin(source, plan["producer_commit"])
        clean_pin(control, head)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        reason = "control_error_" + type(error).__name__
    receipt = {
        "status": "passed" if reason is None else "failed",
        "reason": reason,
        "scope": plan["scope"],
        "control_commit": head,
        "producer_commit": plan["producer_commit"],
        "plan_sha256": sha(PLAN_PATH),
        "wall_seconds": time.perf_counter() - started,
        "phases": phases,
        "completed_phases": sum(p["status"] == "passed" for p in phases),
        "budgets": budgets,
        "failed_outputs_retained": reason is not None,
        "project_journal_initialized": False,
        "new_project_fits": 0,
        "final_generation_attempted": False,
        "final_test_opened": False,
        "canonical_ai_training_qualified": False,
        "quality_qualified": False,
        "stage_ready": False,
    }
    write(output / "resource.json", receipt)
    print(
        json.dumps(
            {
                "status": receipt["status"],
                "reason": reason,
                "completed_phases": receipt["completed_phases"],
            }
        )
    )  # noqa: T201 - bounded CLI receipt
    if reason is not None:
        raise SystemExit(1)


class StackObserver:
    """Own Python frame references briefly; release them before bounded log IO.

    The observer needs the GIL and can miss or delay observations during native
    work. It is neither a native-crash handler nor an allocation/continuous-stack
    profiler. The independent supervisor still enforces every resource budget.
    """

    def __init__(self, policy: dict[str, Any], stream: TextIO) -> None:
        self.policy, self.stream = dict(policy), stream
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._run, name="ai09-stack-observer", daemon=True)
        self.failed: str | None = None
        self.snapshots = self.maximum_bytes = self.truncated_snapshots = 0

    def _snapshot(self) -> str:
        frames = sys._current_frames()
        frame: FrameType | None = None
        emitted = 0
        truncated = False
        lines = ["AI09_PYTHON_STACK_SNAPSHOT\n"]
        size = len(lines[0].encode())
        # Reserve enough room for the explicit count/truncation footer.
        available = self.policy["maximum_snapshot_bytes"] - 256
        try:
            identifiers = sorted(k for k in frames if k != threading.get_ident())
            for identifier in identifiers[: self.policy["maximum_threads"]]:
                header = json.dumps({"thread": identifier}, separators=(",", ":")) + "\n"
                if size + len(header.encode()) > available:
                    truncated = True
                    break
                lines.append(header)
                size += len(header.encode())
                emitted += 1
                frame = frames[identifier]
                depth = 0
                while frame is not None and depth < self.policy["maximum_frames_per_thread"]:
                    # No source lines, arguments, locals, globals or reprs.
                    filename, function = frame.f_code.co_filename, frame.f_code.co_name
                    truncated |= (
                        len(filename) > self.policy["maximum_filename_characters"]
                        or len(function) > self.policy["maximum_function_characters"]
                    )
                    line = (
                        json.dumps(
                            {
                                "file": filename[: self.policy["maximum_filename_characters"]],
                                "line": frame.f_lineno,
                                "function": function[: self.policy["maximum_function_characters"]],
                            },
                            separators=(",", ":"),
                            ensure_ascii=True,
                        )
                        + "\n"
                    )
                    if size + len(line.encode()) > available:
                        truncated = True
                        break
                    lines.append(line)
                    size += len(line.encode())
                    frame, depth = frame.f_back, depth + 1
                truncated |= frame is not None
                if size >= available or (
                    frame is not None and depth < self.policy["maximum_frames_per_thread"]
                ):
                    break
            truncated |= emitted != len(identifiers)
            lines.append(
                json.dumps(
                    {
                        "threads_seen": len(identifiers),
                        "threads_emitted": emitted,
                        "truncated": truncated,
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )
        finally:
            frame = None
            frames.clear()
        snapshot = "".join(lines)
        length = len(snapshot.encode())
        if length > self.policy["maximum_snapshot_bytes"]:
            raise ValueError("capacity_stack_snapshot_resource_limit")
        self.maximum_bytes = max(self.maximum_bytes, length)
        self.truncated_snapshots += int(truncated)
        return snapshot

    def _run(self) -> None:
        try:
            while not self.done.wait(self.policy["interval_seconds"]):
                snapshot = self._snapshot()
                # Frame references have been released before potentially blocking IO.
                self.stream.write(snapshot)
                self.stream.flush()
                self.snapshots += 1
        except Exception as error:
            # Keep only the exception class, never arbitrary payload/locals.
            self.failed = type(error).__name__[:96]
            self.done.set()

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.done.set()
        self.thread.join(timeout=self.policy["join_timeout_seconds"])
        if self.thread.is_alive():
            raise ValueError("capacity_stack_observer_join_timeout")
        if self.failed is not None:
            raise ValueError("capacity_stack_observation_failure_" + self.failed)


def observed_worker(args: argparse.Namespace, plan: dict[str, Any]) -> dict[str, Any]:
    """Observe only our generation worker stack; no locals or foreign process signals."""
    observer = (
        StackObserver(plan["worker_stack_observation"], sys.stderr)
        if args.worker in plan["worker_stack_observation"]["phases"]
        else None
    )
    if observer is not None:
        observer.start()
    try:
        result = (
            producer_worker(args, plan)
            if args.worker in PHASES[:3]
            else consumer_worker(args, plan)
        )
    finally:
        if observer is not None:
            observer.stop()
    if observer is not None:
        result["worker_stack_observation"] = {
            "snapshots": observer.snapshots,
            "maximum_snapshot_bytes": observer.maximum_bytes,
            "truncated_snapshots": observer.truncated_snapshots,
            "not_continuous_or_allocation_measurement": True,
            "cancelled_and_joined": True,
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", choices=PHASES)
    args = parser.parse_args()
    if args.worker:
        require_remote()
        plan = read(PLAN_PATH)
        validate_plan(plan)
        result = observed_worker(args, plan)
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        result["worker_peak_self_rss_bytes"] = int(
            peak if sys.platform == "darwin" else peak * 1024
        )
        write(args.output / (args.worker + ".json"), result)
    else:
        run(args)


if __name__ == "__main__":
    main()
