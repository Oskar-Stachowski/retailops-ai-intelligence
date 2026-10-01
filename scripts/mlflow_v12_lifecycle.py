"""Private v12 approval upload and resumable lifecycle commands; no HTTP mutation routes."""

import argparse
import json
import sys
from pathlib import Path

from pydantic import TypeAdapter
from sqlalchemy import create_engine

from retailops_ai.config import load_settings
from retailops_ai.model_lifecycle.contracts import Version
from retailops_ai.model_lifecycle.v12_journal import PostgresV12Journal
from retailops_ai.model_lifecycle.v12_lifecycle import V12Lifecycle
from retailops_ai.model_lifecycle.v12_lifecycle_contracts import (
    MODEL,
    TEST_MODEL,
    V12LifecycleRequest,
)
from retailops_ai.model_lifecycle.v12_registry import MLflowV12Registry, publish_approval
from retailops_ai.model_lifecycle.v12_release import load_approved_v12
from retailops_ai.security.local import strict_json
from retailops_ai.security.model_operator import model_operator


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-file", type=Path, required=True)
    parser.add_argument("--credentials-file", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    upload = commands.add_parser("upload")
    for name in ("release-dir", "run-dir", "verifier-python"):
        upload.add_argument("--" + name, type=Path, required=True)
    for name in ("release-id", "image-digest", "campaign-run-id"):
        upload.add_argument("--" + name, required=True)
    upload.add_argument("--model", choices=(MODEL, TEST_MODEL), default=MODEL)
    upload.add_argument("--work-dir", type=Path, default=Path(".local/mlflow-v12-approvals"))
    upload.add_argument("--verify-timeout-seconds", type=int, default=3600)
    commands.add_parser("decide")
    review = commands.add_parser("review")
    review.add_argument("--model", choices=(MODEL, TEST_MODEL), default=MODEL)
    args = parser.parse_args()
    engine = None
    try:
        actor = model_operator(args.policy_file, args.credentials_file)
        settings = load_settings(args.env_file)
        registry = MLflowV12Registry(
            compose=settings.network_mode == "compose", environment=settings.app_env
        )
        if args.command == "upload":
            loaded = load_approved_v12(
                args.release_dir,
                args.run_dir,
                args.verifier_python,
                release_id=args.release_id,
                image_digest=args.image_digest,
                verify_timeout_seconds=args.verify_timeout_seconds,
            )
            result = publish_approval(
                args.release_dir,
                loaded,
                registry,
                campaign_mlflow_run_id=args.campaign_run_id,
                model=args.model,
                actor=actor,
                work=args.work_dir,
            )
        else:
            if settings.database_url is None:
                raise ValueError("v12_lifecycle_database_required")
            raw = sys.stdin.buffer.read(16385)
            if len(raw) > 16384:
                raise ValueError("v12_lifecycle_request_limit")
            strict_json(raw)
            engine = create_engine(
                settings.database_url.get_secret_value(),
                connect_args={"connect_timeout": 3},
                hide_parameters=True,
            )
            journal = PostgresV12Journal(engine)
            if args.command == "decide":
                result = V12Lifecycle(registry, journal, environment=settings.app_env).execute(
                    V12LifecycleRequest.model_validate_json(raw), actor
                )
            else:
                registry.namespace(args.model)
                document = json.loads(raw)
                if not isinstance(document, dict) or set(document) != {"model_version"}:
                    raise ValueError("v12_review_requires_immutable_version")
                version = TypeAdapter(Version).validate_json(json.dumps(document["model_version"]))
                with journal.locked(args.model):
                    binding = journal.binding(args.model, version)
                    registry.validate(binding)
                    active = journal.active(args.model)
                    result = dict(
                        model_name=args.model,
                        model_version=version,
                        approval_id=binding.approval.release_id,
                        approval_sha256=binding.approval_sha256,
                        rejected=journal.rejected(args.model, version),
                        release_id=active.release_id if active else None,
                        aliases=registry.aliases(args.model),
                        pending_decisions=journal.pending(args.model),
                        runtime_status="not_integrated",
                    )
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        sys.stderr.write('{"error":"v12_model_lifecycle_failed"}\n')
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
