"""Private v12 worker: preload verified assets before leasing, then fenced small computations."""

import argparse
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import Engine, create_engine

from retailops_ai.config import load_settings
from retailops_ai.forecast_jobs.queue import LeaseLost
from retailops_ai.forecast_jobs.v12_batch import chunks, computation_receipt
from retailops_ai.forecast_jobs.v12_contracts import V12ExecutionLimits
from retailops_ai.forecast_jobs.v12_inference import LoadedV12Inference
from retailops_ai.forecast_jobs.v12_queue import PostgresV12Queue, V12Claim
from retailops_ai.model_lifecycle.v12_journal import PostgresV12Journal
from retailops_ai.model_lifecycle.v12_lifecycle_contracts import V12ModelRelease
from retailops_ai.model_lifecycle.v12_registry import MLflowV12Registry, current_approval
from retailops_ai.model_lifecycle.v12_release import load_approved_v12


def registry_guard(
    engine: Engine, registry: MLflowV12Registry, release: V12ModelRelease, *, full: bool
) -> None:
    journal = PostgresV12Journal(engine)
    with journal.locked(release.binding.model_name):
        if journal.pending(release.binding.model_name) or journal.rejected(
            release.binding.model_name, release.binding.model_version
        ):
            raise ValueError("v12_worker_model_decision_pending_or_rejected")
        active = journal.active(release.binding.model_name)
        if active is None:
            raise ValueError("v12_worker_no_active_model")
        aliases = registry.aliases(release.binding.model_name)
        if (
            aliases.get("champion") != active.binding.model_version
            or aliases.get("rollback") != active.previous_version
        ):
            raise ValueError("v12_worker_registry_head_disagreement")
        current_approval(release.binding.approval)
        if full:
            registry.validate(release.binding)


def preload(
    engine: Engine,
    registry: MLflowV12Registry,
    queue: PostgresV12Queue,
    *,
    release_id: str,
    approval_dir: Path,
    run_dir: Path,
    verifier_python: Path,
    image_digest: str,
    verify_timeout_seconds: int = 3600,
) -> tuple[V12ModelRelease, LoadedV12Inference]:
    journal = PostgresV12Journal(engine)
    with journal.locked(queue.model):
        release = journal.release(release_id)
        if release.binding.model_name != queue.model:
            raise ValueError("v12_worker_release_namespace")
    registry_guard(engine, registry, release, full=True)
    # The full archive can take longer than a lease. No task is owned while it is verified.
    loaded = load_approved_v12(
        approval_dir,
        run_dir,
        verifier_python,
        release_id=release.binding.approval.release_id,
        image_digest=image_digest,
        verify_timeout_seconds=verify_timeout_seconds,
    )
    if (
        loaded.release != release.binding.approval
        or loaded.export.pin != release.binding.approval.qualification.pin
        or image_digest != release.image_digest
    ):
        raise ValueError("v12_worker_local_assets_pin")
    registry_guard(engine, registry, release, full=True)
    return release, loaded


def run_attempt(
    queue: PostgresV12Queue,
    claim: V12Claim,
    loaded: LoadedV12Inference,
    *,
    guard: Callable[[bool], None],
) -> str:
    last_heartbeat = 0.0
    budget_deadline = 0.0

    def tick() -> None:
        nonlocal last_heartbeat
        if budget_deadline and time.monotonic() >= budget_deadline:
            raise LeaseLost("v12_worker_attempt_budget")
        if time.monotonic() - last_heartbeat >= claim.run.policy.heartbeat_seconds:
            # Prove DB ownership before and after the bounded metadata read.
            queue.heartbeat(claim)
            guard(False)
            queue.heartbeat(claim)
            last_heartbeat = time.monotonic()

    try:
        if (
            loaded.release != claim.release.binding.approval
            or loaded.image_digest != claim.run.image_digest
            or loaded.export.pin != claim.run.resolved_model.approval.qualification.pin
            or claim.release.binding != claim.run.resolved_model
        ):
            raise ValueError("v12_worker_claim_assets_mismatch")
        budget_deadline = time.monotonic() + queue.execution_budget(claim) - 0.2
        tick()
        results = []
        for part in chunks(
            claim.profile,
            claim.run.input_ref.scope,
            max(claim.run.input_ref.request.horizons_days),
            claim.release,
            tick=tick,
        ):
            tick()
            remaining = min(queue.execution_budget(claim), budget_deadline - time.monotonic()) - 0.2
            if remaining <= 0:
                raise LeaseLost("v12_worker_attempt_budget")
            approved = loaded.release.qualification.limits
            results.append(
                loaded.predict(
                    part,
                    limits=V12ExecutionLimits(
                        wall_seconds=min(approved.wall_seconds, remaining),
                        rss_bytes=approved.rss_bytes,
                    ),
                    tick=tick,
                )
            )
        tick()
        guard(True)
        queue.heartbeat(claim)
        output = computation_receipt(
            claim.run, claim.profile, claim.release, tuple(results), tick=tick
        )
        queue.complete(claim, output, tick=tick)
        return "succeeded"
    except LeaseLost:
        return "lease_lost"
    except Exception:
        try:
            queue.fail(claim, reason="v12_executor_failed", retryable=True)
        except Exception:
            return "lease_unconfirmed"
        return "failed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--once", action="store_true")
    operation.add_argument("--cancel")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--mechanics", action="store_true")
    parser.add_argument("--development", action="store_true")
    parser.add_argument("--release-id")
    parser.add_argument("--approval-dir", type=Path)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--verifier-python", type=Path)
    parser.add_argument("--verify-timeout-seconds", type=int, default=3600)
    args = parser.parse_args()
    engine = None
    try:
        settings = load_settings(args.env_file)
        if settings.database_url is None:
            raise ValueError("v12_worker_database_required")
        engine = create_engine(
            settings.database_url.get_secret_value(),
            connect_args={"connect_timeout": 3},
            hide_parameters=True,
        )
        queue = PostgresV12Queue(
            engine, settings.app_env, mechanics=args.mechanics, development=args.development
        )
        if args.cancel:
            if any((args.release_id, args.approval_dir, args.run_dir, args.verifier_python)):
                raise ValueError("v12_cancel_no_assets")
            queue.cancel(args.cancel)
            result = dict(status="cancelled", run_id=args.cancel)
        else:
            if (
                not all((args.release_id, args.approval_dir, args.run_dir, args.verifier_python))
                or settings.image_digest is None
            ):
                raise ValueError("v12_worker_explicit_local_assets_and_image_required")
            registry = MLflowV12Registry(
                compose=settings.network_mode == "compose", environment=settings.app_env
            )
            release, loaded = preload(
                engine,
                registry,
                queue,
                release_id=args.release_id,
                approval_dir=args.approval_dir,
                run_dir=args.run_dir,
                verifier_python=args.verifier_python,
                image_digest=settings.image_digest,
                verify_timeout_seconds=args.verify_timeout_seconds,
            )
            claim = queue.claim(release_id=release.release_id)
            if claim is None:
                result = dict(status="idle")
            else:
                status = run_attempt(
                    queue,
                    claim,
                    loaded,
                    guard=lambda full: registry_guard(engine, registry, claim.release, full=full),
                )
                result = dict(
                    status=status,
                    run_id=claim.run.run_id,
                    attempt=claim.run.attempt,
                    published_forecast_outputs=0,
                )
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] in {"succeeded", "idle", "cancelled"} else 1
    except Exception:
        sys.stderr.write('{"error":"v12_worker_unavailable"}\n')
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
