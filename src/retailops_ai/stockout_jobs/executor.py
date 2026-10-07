"""Private numeric child has no DB/MLflow/cloud access and cannot publish results."""

import hashlib
import math
import resource
import signal
import sys
import time


def main() -> int:
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
        if sys.platform == "linux":
            resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
        elif sys.platform != "darwin":
            raise ValueError("stockout_executor_platform")
        resource.setrlimit(resource.RLIMIT_FSIZE, (5 * 1024**2, 5 * 1024**2))
        signal.alarm(121)
        cold = time.monotonic()
        from retailops_ai.forecast_jobs.v12_executor import peak_rss_bytes
        from retailops_ai.security.local import strict_json
        from retailops_ai.source_snapshot.protocol import resource_bytes
        from retailops_ai.stockout_jobs.batch import compute
        from retailops_ai.stockout_jobs.execution import (
            MAX_REQUEST_BYTES,
            StockoutExecution,
            StockoutExecutionResult,
        )

        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("stockout_executor_input_limit")
        strict_json(raw)
        request = StockoutExecution.model_validate_json(raw)
        if (
            hashlib.sha256(resource_bytes("dependencies.lock")).hexdigest()
            != request.dependency_lock_sha256
        ):
            raise ValueError("stockout_executor_dependency_lock_pin")
        resource.setrlimit(resource.RLIMIT_CPU, (request.limits.cpu_seconds,) * 2)
        signal.alarm(math.ceil(request.limits.wall_seconds) + 1)
        cold_seconds = time.monotonic() - cold
        started = time.monotonic()
        output = compute(
            request.run, request.inputs, request.release, generated_at=request.generated_at
        )
        peak = peak_rss_bytes()
        if peak > request.limits.rss_bytes:
            raise ValueError("stockout_executor_memory_limit")
        result = StockoutExecutionResult(
            output=output,
            cold_load_seconds=cold_seconds,
            compute_seconds=time.monotonic() - started,
            peak_rss_bytes=peak,
        )
        print(result.model_dump_json())
        return 0
    except Exception:
        print("stockout_numeric_executor_failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
