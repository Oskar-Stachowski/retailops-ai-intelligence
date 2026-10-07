"""Bound one owned phase process group and preserve observed costs on failure."""

import os
import shutil
import signal
import stat
import subprocess
from pathlib import Path
from time import perf_counter, sleep
from typing import Any

import psutil  # type: ignore[import-untyped]

from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationResources,
)


def scratch_bytes(roots: tuple[Path, ...]) -> int:
    logical = allocated = 0
    seen: set[tuple[int, int]] = set()
    for root in roots:
        if not root.exists():
            continue
        for directory, folders, files in os.walk(root, followlinks=False):
            for name in (*folders, *files):
                try:
                    info = (Path(directory) / name).lstat()
                except FileNotFoundError:
                    continue
                if stat.S_ISLNK(info.st_mode) or not (
                    stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)
                ):
                    raise ValueError("campaign_generation_invalid_scratch_entry")
                key = (info.st_dev, info.st_ino)
                if key not in seen:
                    seen.add(key)
                    logical += info.st_size
                    allocated += info.st_blocks * 512
    return max(logical, allocated)


def monitor(
    command: list[str],
    *,
    root: Path,
    log: Path,
    env: dict[str, str],
    scratch: tuple[Path, ...],
    resources: CampaignGenerationResources,
    deadline: float,
) -> dict[str, Any]:
    started = perf_counter()
    peak = scratch_peak = samples = 0
    reason: str | None = None
    observed_cpu: dict[tuple[int, float], float] = {}
    descendants: dict[tuple[int, float], Any] = {}
    minimum_disk, minimum_memory = shutil.disk_usage(root).free, psutil.virtual_memory().available
    if (
        minimum_disk < resources.scratch_bytes + resources.minimum_free_disk_bytes
        or minimum_memory < resources.tree_rss_bytes + resources.minimum_available_memory_bytes
    ):
        return {
            "status": "failed",
            "reason": "preflight_reserve",
            "wall_seconds": perf_counter() - started,
            "sampled_tree_peak_rss_bytes": None,
            "samples": 0,
        }
    descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        child = subprocess.Popen(  # noqa: S603 -- fixed phase worker, owned interpreter, no shell
            command, cwd=root, env=env, stdout=stream, stderr=stream, start_new_session=True
        )
        try:
            while True:
                rss = psutil.Process().memory_info().rss
                try:
                    parent = psutil.Process(child.pid)
                    processes = [parent, *parent.children(recursive=True)]
                except psutil.NoSuchProcess:
                    processes = []
                for process in processes:
                    try:
                        key = (process.pid, process.create_time())
                        rss += process.memory_info().rss
                        cpu = process.cpu_times()
                        observed_cpu[key] = max(observed_cpu.get(key, 0.0), cpu.user + cpu.system)
                        if process.pid != child.pid:
                            descendants[key] = process
                    except psutil.NoSuchProcess:
                        pass
                scratch_peak = max(scratch_peak, scratch_bytes(scratch))
                disk, memory = shutil.disk_usage(root).free, psutil.virtual_memory().available
                minimum_disk, minimum_memory = min(minimum_disk, disk), min(minimum_memory, memory)
                samples += 1
                peak = max(peak, rss)
                if perf_counter() >= deadline:
                    reason = "wall_limit"
                elif peak > resources.tree_rss_bytes:
                    reason = "tree_rss_limit"
                elif scratch_peak > resources.scratch_bytes:
                    reason = "scratch_limit"
                elif disk < resources.minimum_free_disk_bytes:
                    reason = "free_disk_reserve"
                elif memory < resources.minimum_available_memory_bytes:
                    reason = "available_memory_reserve"
                elif log.stat().st_size > resources.max_log_bytes:
                    reason = "log_limit"
                if reason is not None or child.poll() is not None:
                    break
                sleep(resources.sample_seconds)
            if reason is None and child.returncode != 0:
                reason = "worker_exit"
        except (OSError, ValueError, psutil.Error):
            reason = "monitor_error"
        finally:
            # This new session belongs exclusively to this attempt. No other
            # session/worktree process is eligible for termination.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            for process in descendants.values():
                try:
                    process.kill()
                except psutil.NoSuchProcess:
                    pass
            child.wait(timeout=30)
            _, remaining = psutil.wait_procs(list(descendants.values()), timeout=2)
            if any(p.is_running() and p.status() != psutil.STATUS_ZOMBIE for p in remaining):
                reason = "worker_descendants_unresolved"
    return {
        "status": "passed" if reason is None else "failed",
        "reason": reason,
        "exit_code": child.returncode,
        "wall_seconds": perf_counter() - started,
        "sampled_tree_peak_rss_bytes": peak,
        "sampled_scratch_peak_bytes": scratch_peak,
        "sampled_worker_cpu_seconds": sum(observed_cpu.values()),
        "cpu_measurement": "worker_tree_sampled_lower_bound_excludes_supervisor",
        "memory_measurement": "sampled_supervisor_and_owned_worker_tree_not_continuous_peak",
        "minimum_free_disk_bytes": minimum_disk,
        "minimum_available_memory_bytes": minimum_memory,
        "samples": samples,
        "sample_seconds": resources.sample_seconds,
    }
