"""Authenticated private v12 intake/read; public forecast endpoints remain separate."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine

from retailops_ai.config import load_settings
from retailops_ai.forecast_jobs.contracts import BatchRequest
from retailops_ai.forecast_jobs.v12_queue import PostgresV12Queue
from retailops_ai.security.local import strict_json
from retailops_ai.security.model_operator import private_principal


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--policy-file", type=Path, required=True)
    parser.add_argument("--credentials-file", type=Path, required=True)
    parser.add_argument("--mechanics", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    submit = commands.add_parser("submit")
    submit.add_argument("--idempotency-key", required=True)
    for name in ("get", "attempts", "receipt"):
        commands.add_parser(name).add_argument("--run-id", required=True)
    args = parser.parse_args()
    engine = None
    try:
        actor = private_principal(args.policy_file, args.credentials_file)
        settings = load_settings(args.env_file)
        if settings.database_url is None:
            raise ValueError("v12_queue_database_required")
        engine = create_engine(
            settings.database_url.get_secret_value(),
            hide_parameters=True,
            connect_args={"connect_timeout": 3},
        )
        queue = PostgresV12Queue(engine, settings.app_env, mechanics=args.mechanics)
        result: dict[str, Any] | list[dict[str, Any]]
        if args.command == "submit":
            raw = sys.stdin.buffer.read(16385)
            if len(raw) > 16384:
                raise ValueError("v12_request_byte_limit")
            strict_json(raw)
            result = queue.submit(
                BatchRequest.model_validate_json(raw), actor, args.idempotency_key
            ).model_dump(mode="json")
        elif args.command == "attempts":
            result = [run.model_dump(mode="json") for run in queue.attempts(args.run_id, actor)]
        elif args.command == "receipt":
            result = queue.output(args.run_id, actor).model_dump(mode="json")
        else:
            result = queue.get(args.run_id, actor).model_dump(mode="json")
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        sys.stderr.write('{"error":"v12_queue_operation_failed"}\n')
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
