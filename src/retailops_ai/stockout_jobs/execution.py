"""A credential-free bounded stockout computation, supervised under a fenced lease."""

import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, Sha256, UtcTime
from retailops_ai.forecast_jobs.execution_contracts import ExecutionLimits
from retailops_ai.forecast_jobs.supervisor import tree_rss
from retailops_ai.security.local import strict_json
from retailops_ai.stockout_jobs.batch import StockoutOutput, check_inputs, verify_output
from retailops_ai.stockout_jobs.contracts import StockoutRun
from retailops_ai.stockout_lifecycle.contract import StockoutModelRelease
from retailops_ai.stockout_runtime.inputs import PreparedStockoutInputs

MAX_REQUEST_BYTES = 24 * 1024**2
MAX_RESULT_BYTES = 4 * 1024**2 + 64 * 1024
MAX_STDERR_BYTES = 16 * 1024


class StockoutExecution(Contract):
    purpose: Literal["pinned_stockout_computation"] = "pinned_stockout_computation"
    environment: Literal["local", "test"]
    run: StockoutRun
    inputs: PreparedStockoutInputs
    release: StockoutModelRelease
    image_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    dependency_lock_sha256: Sha256
    generated_at: UtcTime
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)

    @model_validator(mode="after")
    def pins(self) -> Self:
        check_inputs(self.run, self.inputs, self.release)
        if (
            self.run.status != "running"
            or self.run.environment != self.environment
            or self.image_digest != self.release.image_digest
            or (
                self.release.binding.model_name.endswith("test-mechanics")
                and self.environment != "test"
            )
            or self.run.started_at is None
            or self.generated_at < self.run.started_at
        ):
            raise ValueError("stockout_execution_environment_image_or_attempt_pin")
        return self


class StockoutExecutionResult(Contract):
    purpose: Literal["pinned_stockout_computation"] = "pinned_stockout_computation"
    output: StockoutOutput
    cold_load_seconds: Annotated[float, Field(ge=0)]
    compute_seconds: Annotated[float, Field(ge=0)]
    peak_rss_bytes: Annotated[int, Field(ge=1)]


def supervise(
    request: StockoutExecution, *, on_tick: Callable[[], None] | None = None
) -> StockoutExecutionResult:
    request = StockoutExecution.model_validate_json(request.model_dump_json())
    payload = request.model_dump_json().encode()
    if len(payload) > MAX_REQUEST_BYTES:
        raise ValueError("stockout_execution_input_limit")
    environment = {k: v for k, v in os.environ.items() if k in {"PATH", "LANG", "LC_ALL"}}
    environment.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
    with tempfile.TemporaryDirectory(prefix="retailops-stockout-executor-") as temporary:
        root = Path(temporary)
        source, output, error = (root / name for name in ("request.json", "result.json", "stderr"))
        for path, raw in ((source, payload), (output, b""), (error, b"")):
            path.write_bytes(raw)
            path.chmod(0o600)
        with source.open("rb") as stdin, output.open("wb") as stdout, error.open("wb") as stderr:
            started = time.monotonic()
            child = subprocess.Popen(  # noqa: S603 - isolated interpreter, fixed module, no credentials
                [sys.executable, "-I", "-m", "retailops_ai.stockout_jobs.executor"],
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                cwd=root,
                env=environment,
                start_new_session=True,
            )
            try:
                while child.poll() is None:
                    if time.monotonic() - started > request.limits.wall_seconds:
                        raise ValueError("stockout_execution_wall_limit")
                    if tree_rss(child.pid) > request.limits.rss_bytes:
                        raise ValueError("stockout_execution_memory_limit")
                    if (
                        output.stat().st_size > MAX_RESULT_BYTES
                        or error.stat().st_size > MAX_STDERR_BYTES
                    ):
                        raise ValueError("stockout_execution_output_limit")
                    if on_tick:
                        on_tick()
                    try:
                        child.wait(timeout=0.1)
                    except subprocess.TimeoutExpired:
                        continue
                if on_tick:
                    on_tick()
                if child.returncode or time.monotonic() - started > request.limits.wall_seconds:
                    raise ValueError("stockout_execution_child_or_wall_limit")
                if (
                    output.stat().st_size > MAX_RESULT_BYTES
                    or error.stat().st_size > MAX_STDERR_BYTES
                ):
                    raise ValueError("stockout_execution_output_limit")
                with output.open("rb") as stream:
                    raw = stream.read(MAX_RESULT_BYTES + 1)
                strict_json(raw)
                result = StockoutExecutionResult.model_validate_json(raw)
                if (
                    result.peak_rss_bytes > request.limits.rss_bytes
                    or result.output.generated_at != request.generated_at
                ):
                    raise ValueError("stockout_execution_peak_or_result_pin")
                verify_output(
                    request.run, request.inputs, request.release, result.output, tick=on_tick
                )
                return result
            finally:
                if child.poll() is None:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                child.wait(timeout=5)
