"""Private operator entry point in the AI container, without HTTP mutation endpoints."""

import argparse
import json
import sys
from pathlib import Path

from pydantic import TypeAdapter
from sqlalchemy import create_engine

from retailops_ai.config import load_settings
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.model_lifecycle.contracts import MODEL, Request, Version
from retailops_ai.model_lifecycle.engine import Lifecycle
from retailops_ai.model_lifecycle.journal import PostgresJournal
from retailops_ai.model_lifecycle.mlflow import MLflowRegistry
from retailops_ai.security.local import strict_json
from retailops_ai.security.model_operator import model_operator


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-file", type=Path, required=True)
    parser.add_argument("--credentials-file", type=Path, required=True)
    parser.add_argument("--review", action="store_true")
    args = parser.parse_args()
    engine = None
    try:
        actor = model_operator(args.policy_file, args.credentials_file)
        raw = sys.stdin.buffer.read(16385)
        if len(raw) > 16384:
            raise ValueError("model_request_limit")
        strict_json(raw)
        settings = load_settings()
        if settings.database_url is None:
            raise ValueError("model_database_required")
        engine = create_engine(
            settings.database_url.get_secret_value(), connect_args={"connect_timeout": 3}
        )
        registry = MLflowRegistry(
            compose=settings.network_mode == "compose", environment=settings.app_env
        )
        journal = PostgresJournal(engine)
        if args.review:
            document = json.loads(raw)
            if set(document) != {"model_version"}:
                raise ValueError("immutable_review_version_required")
            version = TypeAdapter(Version).validate_json(json.dumps(document["model_version"]))
            with journal.locked(MODEL):
                binding = journal.binding(MODEL, version)
                registry.validate(binding)
                result = {
                    "review_id": "model-review-sha256-"
                    + canonical_sha256(binding.model_dump(mode="json")),
                    "model_name": MODEL,
                    "model_version": version,
                    "evidence_id": binding.qualification.evidence_id,
                    "qualification_sha256": binding.qualification_sha256,
                    "quality_and_load_checks": "passed",
                    "rejected": journal.rejected(MODEL, version),
                    "aliases": registry.aliases(MODEL),
                    "runtime_status": "not_integrated",
                }
        else:
            request = Request.model_validate_json(raw)
            result = Lifecycle(registry, journal, environment=settings.app_env).execute(
                request, actor
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        # Database/network/provider exceptions must not expose credentials or SQL parameters.
        print('{"error":"model_lifecycle_operation_failed"}', file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
