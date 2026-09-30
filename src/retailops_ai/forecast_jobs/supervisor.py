"""Bounded private subprocess computation, reusable with a fenced heartbeat callback."""

import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import psutil  # type: ignore[import-untyped]

from retailops_ai.forecast_jobs.execution_contracts import (
    MAX_EXECUTION_BYTES,
    MAX_RESULT_BYTES,
    MAX_STDERR_BYTES,
    RuntimeExecution,
    RuntimeResult,
)
from retailops_ai.security.local import strict_json


class ExecutionError(ValueError):
    pass


def tree_rss(pid: int) -> int:
    try:
        root = psutil.Process(pid)
        processes = [root, *root.children(recursive=True)]
        total = 0
        for process in processes:
            try:
                total += process.memory_info().rss
            except psutil.NoSuchProcess:
                continue
        return total
    except psutil.NoSuchProcess:
        return 0


def supervise(
    request: RuntimeExecution, *, on_tick: Callable[[], None] | None = None
) -> RuntimeResult:
    request = RuntimeExecution.model_validate_json(request.model_dump_json())
    payload = request.model_dump_json().encode()
    if len(payload) > MAX_EXECUTION_BYTES:
        raise ExecutionError("runtime_execution_input_limit")
    # Isolated interpreter, no arbitrary caller path, DB/API/cloud credentials or proxy settings.
    child_env = {k: v for k, v in os.environ.items() if k in {"PATH", "LANG", "LC_ALL"}}
    child_env.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
    with tempfile.TemporaryDirectory(prefix="retailops-forecast-executor-") as temporary:
        root = Path(temporary)
        source, output, error = (root / name for name in ("request.json", "result.json", "stderr"))
        for path, raw in ((source, payload), (output, b""), (error, b"")):
            path.write_bytes(raw)
            path.chmod(0o600)
        with source.open("rb") as stdin, output.open("wb") as stdout, error.open("wb") as stderr:
            started = time.monotonic()
            child = subprocess.Popen(  # noqa: S603 - isolated interpreter and fixed trusted module
                [sys.executable, "-I", "-m", "retailops_ai.forecast_jobs.runtime_executor"],
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                cwd=root,
                env=child_env,
                start_new_session=True,
            )
            try:
                while child.poll() is None:
                    if time.monotonic() - started > request.limits.wall_seconds:
                        raise ExecutionError("runtime_execution_wall_limit")
                    if tree_rss(child.pid) > request.limits.rss_bytes:
                        raise ExecutionError("runtime_execution_memory_limit")
                    if (
                        output.stat().st_size > MAX_RESULT_BYTES
                        or error.stat().st_size > MAX_STDERR_BYTES
                    ):
                        raise ExecutionError("runtime_execution_output_limit")
                    if on_tick is not None:
                        on_tick()
                    try:
                        child.wait(timeout=0.1)
                    except subprocess.TimeoutExpired:
                        continue
                if on_tick is not None:
                    on_tick()
                if time.monotonic() - started > request.limits.wall_seconds:
                    raise ExecutionError("runtime_execution_wall_limit")
                if child.returncode:
                    raise ExecutionError("runtime_execution_child_failed")
                if (
                    output.stat().st_size > MAX_RESULT_BYTES
                    or error.stat().st_size > MAX_STDERR_BYTES
                ):
                    raise ExecutionError("runtime_execution_output_limit")
                with output.open("rb") as stream:
                    raw = stream.read(MAX_RESULT_BYTES + 1)
                strict_json(raw)
                result = RuntimeResult.model_validate_json(raw)
                if (
                    result.profile_id != request.inputs.profile_id
                    or result.release_id != request.release.release_id
                    or len(result.quantities) != len(request.inputs.rows)
                    or result.peak_rss_bytes > request.limits.rss_bytes
                ):
                    raise ExecutionError("runtime_execution_result_pin_or_count_mismatch")
                return result
            finally:
                if child.poll() is None:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                child.wait(timeout=5)
