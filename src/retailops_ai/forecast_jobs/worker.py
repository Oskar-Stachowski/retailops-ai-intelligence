"""Fenced, bounded attempts: qualified forecasts and a separate mechanics namespace."""

import argparse
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import time

from sqlalchemy import create_engine

from retailops_ai.config import load_settings
from retailops_ai.forecast_jobs.contracts import MechanicsOutput
from retailops_ai.forecast_jobs.execution_contracts import ExecutionLimits, RuntimeExecution
from retailops_ai.forecast_jobs.inputs import PreparedInputs, scoped_inputs
from retailops_ai.forecast_jobs.queue import Claim, LeaseLost, PostgresBatchQueue
from retailops_ai.forecast_jobs.runtime import RuntimePin
from retailops_ai.forecast_jobs.supervisor import supervise
from retailops_ai.source_snapshot.protocol import resource_bytes


def stop(child: subprocess.Popen[bytes]) -> None:
    if child.poll() is None:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    child.communicate(timeout=5)


def run_attempt(queue: PostgresBatchQueue, claim: Claim, *, compose: bool) -> str:
    if queue.environment != "test" or claim.run.purpose != "lifecycle_mechanics_only":
        raise ValueError("qualified_forecast_executor_not_enabled")
    payload: bytes | None = json.dumps(
        {
            "run": claim.run.model_dump(mode="json"),
            "profile": claim.profile.model_dump(mode="json"),
            "compose": compose,
        }
    ).encode()
    # Credentials and DB access are not inherited by the computation child.
    child_env = {
        k: v for k, v in os.environ.items() if k in {"PATH", "PYTHONPATH", "LANG", "LC_ALL"}
    }
    child = subprocess.Popen(  # noqa: S603 - fixed trusted child module
        [sys.executable, "-m", "retailops_ai.forecast_jobs.executor"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=child_env,
        start_new_session=True,
    )
    try:
        while True:
            try:
                stdout, _ = child.communicate(payload, timeout=claim.run.policy.heartbeat_seconds)
                break
            except subprocess.TimeoutExpired:
                payload = None
                queue.heartbeat(claim)
        if child.returncode or len(stdout) > 256 * 1024:
            queue.fail(claim, reason="executor_failed", retryable=True)
            return "failed"
        output = MechanicsOutput.model_validate_json(stdout)
        queue.complete(claim, output)
        return "succeeded"
    except LeaseLost:
        return "lease_lost"
    except Exception:
        # If DB ownership cannot be proven, leave the lease to expire; never force a completion.
        try:
            queue.fail(claim, reason="executor_failed", retryable=True)
        except Exception:
            return "lease_unconfirmed"
        return "failed"
    finally:
        stop(child)


def run_forecast_attempt(
    queue: PostgresBatchQueue, claim: Claim, *, compose: bool, image_digest: str
) -> str:
    try:
        if (
            queue.mechanics
            or claim.run.purpose != "qualified_forecast"
            or not isinstance(claim.profile, PreparedInputs)
            or claim.release is None
            or claim.release.release_id != claim.run.release_id
            or claim.release.binding != claim.run.resolved_model
            or claim.release.image_digest != claim.run.image_digest
        ):
            raise ValueError("qualified_attempt_pin_mismatch")
        inputs = scoped_inputs(
            claim.profile, claim.run.input_ref.scope, max(claim.run.input_ref.request.horizons_days)
        )
        budget = queue.execution_budget(claim) - 0.2
        if budget < 0.1:
            raise LeaseLost("batch_execution_budget_exhausted")
        request = RuntimeExecution(
            purpose="qualified_forecast_computation",
            environment=queue.environment,
            compose=compose,
            inputs=inputs,
            release=claim.release,
            runtime_pin=RuntimePin(
                image_digest=image_digest,
                dependency_lock_sha256=hashlib.sha256(
                    resource_bytes("dependencies.lock")
                ).hexdigest(),
            ),
            limits=ExecutionLimits(
                wall_seconds=budget, cpu_seconds=min(60, max(1, math.ceil(budget)))
            ),
        )
        last_heartbeat = 0.0

        def tick() -> None:
            nonlocal last_heartbeat
            now = time.monotonic()
            if now - last_heartbeat >= claim.run.policy.heartbeat_seconds:
                queue.heartbeat(claim)
                last_heartbeat = time.monotonic()

        result = supervise(request, on_tick=tick)
        queue.complete_forecast(claim, result)
        return "succeeded"
    except LeaseLost:
        return "lease_lost"
    except Exception:
        try:
            queue.fail(claim, reason="qualified_executor_failed", retryable=True)
        except Exception:
            return "lease_unconfirmed"
        return "failed"


def main() -> int:
    parser = argparse.ArgumentParser()
    operations = parser.add_mutually_exclusive_group(required=True)
    operations.add_argument("--once", action="store_true")
    operations.add_argument("--cancel", metavar="RUN_ID")
    operations.add_argument("--preflight", action="store_true")
    parser.add_argument("--mechanics", action="store_true")
    parser.add_argument("--profile-id")
    parser.add_argument("--release-id")
    args = parser.parse_args()
    settings = load_settings()
    if settings.database_url is None:
        parser.error("worker_requires_private_database_settings")
    if args.preflight:
        if (
            args.mechanics
            or not args.profile_id
            or not args.release_id
            or settings.image_digest is None
        ):
            parser.error("preflight_requires_registered_profile_release_and_runtime_image_pins")
    elif args.profile_id or args.release_id or (args.mechanics and settings.app_env != "test"):
        parser.error("queue_worker_requires_no_profile_overrides_and_mechanics_requires_test")
    elif args.once and not args.mechanics and settings.image_digest is None:
        parser.error("qualified_worker_requires_runtime_image_digest")
    engine = create_engine(
        settings.database_url.get_secret_value(), connect_args={"connect_timeout": 3}
    )
    try:
        if args.preflight:
            from retailops_ai.forecast_jobs.input_store import PostgresInputStore
            from retailops_ai.forecast_jobs.preflight import result_report, run_preflight

            if settings.image_digest is None:
                raise ValueError("runtime_image_pin_missing")
            inputs = PostgresInputStore(engine, settings.app_env).get(args.profile_id).inputs
            result = run_preflight(
                engine,
                inputs,
                release_id=args.release_id,
                image_digest=settings.image_digest,
                environment=settings.app_env,
                compose=settings.network_mode == "compose",
            )
            print(json.dumps(result_report(result)))
            return 0
        queue = PostgresBatchQueue(engine, settings.app_env, mechanics=args.mechanics)
        if args.cancel:
            if re.fullmatch(r"run-[0-9a-f]{32}", args.cancel) is None:
                parser.error("invalid run ID")
            queue.cancel(args.cancel)
            print(json.dumps({"run_id": args.cancel, "status": "cancelled"}))
            return 0
        claim = queue.claim()
        if claim is None:
            print(json.dumps({"status": "idle"}))
            return 0
        if args.mechanics:
            status = run_attempt(queue, claim, compose=settings.network_mode == "compose")
        else:
            if settings.image_digest is None:
                raise ValueError("runtime_image_pin_missing")
            status = run_forecast_attempt(
                queue,
                claim,
                compose=settings.network_mode == "compose",
                image_digest=settings.image_digest,
            )
        print(
            json.dumps(
                {
                    "run_id": claim.run.run_id,
                    "attempt": claim.run.attempt,
                    "status": status,
                    "purpose": claim.run.purpose,
                }
            )
        )
        return 0 if status == "succeeded" else 1
    except Exception:
        print(json.dumps({"error": "forecast_worker_unavailable"}))
        return 2
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
