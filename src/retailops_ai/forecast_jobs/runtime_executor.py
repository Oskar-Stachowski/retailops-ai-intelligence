"""Credential-free, resource-bounded numeric child. It never accesses the queue database."""

import math
import resource
import signal
import sys


def main() -> int:
    # Limits precede imports of numeric libraries and JSON parsing.
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
        if sys.platform == "linux":
            resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
        elif sys.platform != "darwin":
            raise ValueError("runtime_executor_platform_unsupported")
        # macOS rejects RLIMIT_AS; the supervisor RSS guard and post-compute peak apply there.
        resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024, 256 * 1024))
        signal.alarm(121)
        import time

        from retailops_ai.data_contracts.identity import canonical_sha256
        from retailops_ai.forecast_jobs.execution_contracts import (
            MAX_EXECUTION_BYTES,
            RuntimeExecution,
            RuntimeResult,
        )
        from retailops_ai.forecast_jobs.runtime import load_release
        from retailops_ai.forecast_jobs.v12_executor import peak_rss_bytes
        from retailops_ai.model_lifecycle.mlflow import MLflowRegistry
        from retailops_ai.security.local import strict_json
        from retailops_ai.source_snapshot.protocol import resource_bytes

        raw = sys.stdin.buffer.read(MAX_EXECUTION_BYTES + 1)
        if len(raw) > MAX_EXECUTION_BYTES:
            raise ValueError("runtime_execution_input_limit")
        try:
            strict_json(raw)
            request = RuntimeExecution.model_validate_json(raw)
        except ValueError:
            print("forecast_runtime_input_invalid", file=sys.stderr)
            return 2
        resource.setrlimit(resource.RLIMIT_CPU, (request.limits.cpu_seconds,) * 2)
        signal.alarm(math.ceil(request.limits.wall_seconds) + 1)
        import hashlib

        if (
            hashlib.sha256(resource_bytes("dependencies.lock")).hexdigest()
            != request.runtime_pin.dependency_lock_sha256
        ):
            print("forecast_runtime_dependency_lock_mismatch", file=sys.stderr)
            return 2
        cold = time.monotonic()
        model = load_release(
            request.release,
            request.runtime_pin,
            MLflowRegistry(compose=request.compose, environment=request.environment),
        )
        cold_seconds = time.monotonic() - cold
        started = time.monotonic()
        quantities = model.predict(request.inputs)
        peak = peak_rss_bytes()
        if peak > request.limits.rss_bytes:
            raise ValueError("runtime_executor_memory_limit")
        result = RuntimeResult(
            purpose=request.purpose,
            profile_id=request.inputs.profile_id,
            release_id=request.release.release_id,
            quantities=quantities,
            quantities_sha256=canonical_sha256(quantities),
            cold_load_seconds=cold_seconds,
            compute_seconds=time.monotonic() - started,
            peak_rss_bytes=peak,
        )
        print(result.model_dump_json())
        return 0
    except Exception:
        print("forecast_runtime_executor_failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
