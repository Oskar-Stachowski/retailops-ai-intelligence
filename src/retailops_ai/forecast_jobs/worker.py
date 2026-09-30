"""One fenced attempt per supervised child; only test mechanics are enabled at AI 05.4."""

import argparse
import json
import os
import re
import signal
import subprocess
import sys

from sqlalchemy import create_engine

from retailops_ai.config import load_settings
from retailops_ai.forecast_jobs.contracts import MechanicsOutput
from retailops_ai.forecast_jobs.queue import Claim, LeaseLost, PostgresBatchQueue


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


def main() -> int:
    parser = argparse.ArgumentParser()
    operations = parser.add_mutually_exclusive_group(required=True)
    operations.add_argument("--once", action="store_true")
    operations.add_argument("--cancel", metavar="RUN_ID")
    parser.add_argument("--mechanics", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    if not args.mechanics or settings.app_env != "test" or settings.database_url is None:
        parser.error(
            "AI05.4 enables only --once --mechanics with APP_ENV=test; forecast execution requires AI05.5"
        )
    engine = create_engine(
        settings.database_url.get_secret_value(), connect_args={"connect_timeout": 3}
    )
    try:
        queue = PostgresBatchQueue(engine, settings.app_env)
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
        status = run_attempt(queue, claim, compose=settings.network_mode == "compose")
        print(
            json.dumps(
                {
                    "run_id": claim.run.run_id,
                    "attempt": claim.run.attempt,
                    "status": status,
                    "purpose": "lifecycle_mechanics_only",
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
