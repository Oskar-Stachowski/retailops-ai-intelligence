"""Validate the published model before leasing; heartbeat and replay before atomic completion."""

import argparse
import hashlib
import json
import math
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, create_engine

from retailops_ai.config import load_settings
from retailops_ai.forecast_jobs.execution_contracts import ExecutionLimits
from retailops_ai.forecast_jobs.queue import LeaseLost
from retailops_ai.source_snapshot.protocol import resource_bytes
from retailops_ai.stockout_jobs.execution import StockoutExecution, supervise
from retailops_ai.stockout_jobs.queue import PostgresStockoutQueue, StockoutClaim
from retailops_ai.stockout_lifecycle.contract import StockoutModelRelease
from retailops_ai.stockout_lifecycle.journal import PostgresStockoutJournal
from retailops_ai.stockout_lifecycle.registry import MLflowStockoutRegistry


def registry_guard(
    engine: Engine, registry: MLflowStockoutRegistry, release: StockoutModelRelease, *, full: bool
) -> None:
    journal = PostgresStockoutJournal(engine)
    model = release.binding.model_name
    with journal.locked(model):
        if journal.pending(model) or journal.rejected(model, release.binding.model_version):
            raise ValueError("stockout_worker_model_pending_or_rejected")
        active = journal.active(model)
        if active is None:
            raise ValueError("stockout_worker_no_active_model")
        aliases = registry.aliases(model)
        if (
            aliases.get("champion") != active.binding.model_version
            or aliases.get("rollback") != active.previous_version
        ):
            raise ValueError("stockout_worker_registry_head_disagreement")
        approval = release.binding.approval
        if not approval.reviewed_at <= datetime.now(UTC) < approval.qualification.valid_until:
            raise ValueError("stockout_worker_approval_expired_or_future")
        if full:
            registry.validate(release.binding)


def preload(
    engine: Engine,
    registry: MLflowStockoutRegistry,
    queue: PostgresStockoutQueue,
    *,
    release_id: str,
    image_digest: str,
) -> StockoutModelRelease:
    journal = PostgresStockoutJournal(engine)
    with journal.locked(queue.model):
        release = journal.release(release_id)
    if release.binding.model_name != queue.model or release.image_digest != image_digest:
        raise ValueError("stockout_worker_local_image_or_namespace_pin")
    # Registry validation downloads/verifies the whole capsule before a lease is held.
    registry_guard(engine, registry, release, full=True)
    return release


def run_attempt(
    queue: PostgresStockoutQueue,
    claim: StockoutClaim,
    *,
    image_digest: str,
    guard: Callable[[bool], None],
) -> str:
    last_heartbeat = 0.0
    deadline = 0.0

    def tick() -> None:
        nonlocal last_heartbeat
        if deadline and time.monotonic() >= deadline:
            raise LeaseLost("stockout_worker_attempt_budget")
        if time.monotonic() - last_heartbeat >= claim.run.policy.heartbeat_seconds:
            queue.heartbeat(claim)
            guard(False)
            queue.heartbeat(claim)
            last_heartbeat = time.monotonic()

    try:
        budget = queue.execution_budget(claim) - 0.2
        if budget < 0.1:
            raise LeaseLost("stockout_worker_attempt_budget")
        deadline = time.monotonic() + budget
        request = StockoutExecution(
            environment=queue.environment,
            run=claim.run,
            inputs=claim.profile,
            release=claim.release,
            image_digest=image_digest,
            dependency_lock_sha256=hashlib.sha256(resource_bytes("dependencies.lock")).hexdigest(),
            generated_at=datetime.now(UTC),
            limits=ExecutionLimits(
                wall_seconds=min(120.0, budget), cpu_seconds=min(60, max(1, math.ceil(budget)))
            ),
        )
        tick()
        result = supervise(request, on_tick=tick)
        tick()
        guard(True)
        queue.heartbeat(claim)
        queue.complete(claim, result.output, tick=tick)
        return "succeeded"
    except LeaseLost:
        return "lease_lost"
    except Exception:
        try:
            queue.fail(claim, reason="stockout_executor_failed", retryable=True)
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
    parser.add_argument("--release-id")
    args = parser.parse_args()
    engine = None
    try:
        settings = load_settings(args.env_file)
        if settings.database_url is None:
            raise ValueError("stockout_worker_database_required")
        engine = create_engine(
            settings.database_url.get_secret_value(),
            hide_parameters=True,
            connect_args={"connect_timeout": 3},
        )
        queue = PostgresStockoutQueue(engine, settings.app_env, mechanics=args.mechanics)
        if args.cancel:
            if args.release_id:
                raise ValueError("stockout_cancel_no_release_override")
            queue.cancel(args.cancel)
            result = dict(status="cancelled", run_id=args.cancel)
        else:
            if not args.release_id or settings.image_digest is None:
                raise ValueError("stockout_worker_explicit_release_and_runtime_image_required")
            registry = MLflowStockoutRegistry(
                compose=settings.network_mode == "compose", environment=settings.app_env
            )
            release = preload(
                engine,
                registry,
                queue,
                release_id=args.release_id,
                image_digest=settings.image_digest,
            )
            claim = queue.claim(release_id=release.release_id)
            if claim is None:
                result = dict(status="idle")
            else:
                status = run_attempt(
                    queue,
                    claim,
                    image_digest=settings.image_digest,
                    guard=lambda full: registry_guard(engine, registry, claim.release, full=full),
                )
                result = dict(status=status, run_id=claim.run.run_id, attempt=claim.run.attempt)
        print(json.dumps(result))
        return 0 if result["status"] in {"idle", "succeeded", "cancelled"} else 1
    except Exception:
        print(json.dumps(dict(error="stockout_worker_unavailable")))
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
