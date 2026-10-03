"""Private preparation/preflight commands; no queue admission, prediction publication or model promotion."""

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from retailops_ai.forecast_jobs.contracts import BatchScope
from retailops_ai.forecast_jobs.inputs import build_inputs_package, verify_inputs_package


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("inputs-build")
    build.add_argument("--feature-dir", type=Path, required=True)
    build.add_argument("--curated-dir", type=Path, required=True)
    build.add_argument("--as-of", required=True)
    build.add_argument("--product", action="append", required=True)
    build.add_argument("--location", action="append", required=True)
    build.add_argument("--channel", choices=("store", "online"), required=True)
    build.add_argument("--horizon", type=int, choices=(7, 14), default=14)
    build.add_argument("--output-root", type=Path, default=Path("data/generated/inference-inputs"))
    verify = commands.add_parser("inputs-verify")
    verify.add_argument("--inputs-dir", type=Path, required=True)
    register = commands.add_parser("inputs-register")
    register.add_argument("--inputs-dir", type=Path, required=True)
    check = commands.add_parser("release-check")
    check.add_argument("--inputs-dir", type=Path, required=True)
    check.add_argument("--release-id", required=True)
    args = parser.parse_args()
    try:
        started = time.monotonic()
        if args.command == "inputs-build":
            scope = BatchScope.model_validate_json(
                json.dumps(
                    {
                        "product_ids": sorted(set(args.product)),
                        "selling_location_ids": sorted(set(args.location)),
                        "channel": args.channel,
                    }
                )
            )
            directory = build_inputs_package(
                args.feature_dir,
                args.curated_dir,
                args.output_root,
                as_of=datetime.fromisoformat(args.as_of),
                scope=scope,
                horizon_days=args.horizon,
            )
        else:
            directory = args.inputs_dir
        inputs = verify_inputs_package(directory)
        report: dict[str, object] = {
            "status": "passed",
            "purpose": "runtime_preflight_only",
            "profile_id": inputs.profile_id,
            "feature_set_id": inputs.feature_manifest.feature_set_id,
            "rows": len(inputs.rows),
            "histories": len(inputs.histories),
            "published_forecast_outputs": 0,
        }
        if args.command in {"release-check", "inputs-register"}:
            from sqlalchemy import create_engine

            from retailops_ai.config import load_settings
            from retailops_ai.forecast_jobs.input_store import PostgresInputStore
            from retailops_ai.forecast_jobs.preflight import result_report, run_preflight

            settings = load_settings()
            if settings.database_url is None or (
                args.command == "release-check" and settings.image_digest is None
            ):
                raise ValueError("runtime_check_requires_database_image_and_release_pin")
            engine = create_engine(
                settings.database_url.get_secret_value(), connect_args={"connect_timeout": 3}
            )
            try:
                if args.command == "inputs-register":
                    stored = PostgresInputStore(engine, settings.app_env).register(inputs)
                    report.update(
                        purpose="verified_inputs_only",
                        environment=stored.environment,
                        registered_at=stored.registered_at.isoformat(),
                        profile_sha256=stored.profile_sha256,
                        storage_bytes=stored.storage_bytes,
                    )
                else:
                    if settings.image_digest is None:
                        raise ValueError("runtime_image_pin_missing")
                    result = run_preflight(
                        engine,
                        inputs,
                        release_id=args.release_id,
                        image_digest=settings.image_digest,
                        environment=settings.app_env,
                        compose=settings.network_mode == "compose",
                    )
                    report.update(result_report(result))
            finally:
                engine.dispose()
        report["duration_seconds"] = time.monotonic() - started
        print(json.dumps(report, sort_keys=True))
        return 0
    except Exception:
        print(
            json.dumps(
                {"error": "forecast_runtime_preflight_failed", "published_forecast_outputs": 0}
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
