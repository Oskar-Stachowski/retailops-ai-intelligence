"""Authenticated stockout capsule import and explicit resumable lifecycle decisions."""

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine

from retailops_ai.config import load_settings
from retailops_ai.security.local import strict_json
from retailops_ai.security.model_operator import model_operator
from retailops_ai.stockout_lifecycle.contract import MODEL, TEST_MODEL, StockoutLifecycleRequest
from retailops_ai.stockout_lifecycle.engine import StockoutLifecycle
from retailops_ai.stockout_lifecycle.journal import PostgresStockoutJournal
from retailops_ai.stockout_lifecycle.publish import publish_approval
from retailops_ai.stockout_lifecycle.registry import MLflowStockoutRegistry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-file", type=Path, required=True)
    parser.add_argument("--credentials-file", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    upload = commands.add_parser("upload")
    upload.add_argument("--capsule", type=Path, required=True)
    upload.add_argument("--approval-id", required=True)
    upload.add_argument("--model", choices=(MODEL, TEST_MODEL), default=MODEL)
    upload.add_argument("--work-dir", type=Path, required=True)
    commands.add_parser("decide")
    args = parser.parse_args()
    engine = None
    try:
        actor = model_operator(args.policy_file, args.credentials_file)
        settings = load_settings(args.env_file)
        registry = MLflowStockoutRegistry(
            compose=settings.network_mode == "compose", environment=settings.app_env
        )
        if args.command == "upload":
            result = publish_approval(
                args.capsule,
                registry,
                approval_id=args.approval_id,
                model=args.model,
                actor=actor,
                work=args.work_dir,
            )
        else:
            if settings.database_url is None:
                raise ValueError("stockout_lifecycle_database_required")
            raw = sys.stdin.buffer.read(16385)
            if len(raw) > 16384:
                raise ValueError("stockout_lifecycle_request_limit")
            strict_json(raw)
            request = StockoutLifecycleRequest.model_validate_json(raw)
            engine = create_engine(
                settings.database_url.get_secret_value(),
                connect_args={"connect_timeout": 3},
                hide_parameters=True,
            )
            result = StockoutLifecycle(
                registry, PostgresStockoutJournal(engine), environment=settings.app_env
            ).execute(request, actor)
        print(json.dumps(result))
        return 0
    except Exception as error:
        print(
            json.dumps(dict(error="stockout_lifecycle_operation_failed", kind=type(error).__name__))
        )
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
