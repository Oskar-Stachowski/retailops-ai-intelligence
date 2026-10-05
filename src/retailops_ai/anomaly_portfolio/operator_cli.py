"""Private authenticated anomaly lifecycle operations and immutable version review."""

import argparse
import json
import sys
from pathlib import Path

from pydantic import TypeAdapter
from sqlalchemy import create_engine

from retailops_ai.anomaly_portfolio.journal import PostgresJournal
from retailops_ai.anomaly_portfolio.lifecycle import Lifecycle
from retailops_ai.anomaly_portfolio.lifecycle_contract import MODEL, Request
from retailops_ai.anomaly_portfolio.registry import AnomalyRegistry
from retailops_ai.config import load_settings
from retailops_ai.model_lifecycle.anomaly_evaluation_store import PostgresAnomalyEvaluations
from retailops_ai.model_lifecycle.contracts import Version
from retailops_ai.security.local import strict_json
from retailops_ai.security.model_operator import model_operator
from retailops_ai.source_snapshot.files import decode_json, json_sha256


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-file", type=Path, required=True)
    parser.add_argument("--credentials-file", type=Path, required=True)
    parser.add_argument("--registry-port", type=int)
    parser.add_argument("--review", action="store_true")
    args = parser.parse_args()
    engine = None
    try:
        actor = model_operator(args.policy_file, args.credentials_file)
        raw = sys.stdin.buffer.read(16385)
        if len(raw) > 16384:
            raise ValueError("anomaly_operator_request_budget")
        strict_json(raw)
        settings = load_settings()
        if settings.database_url is None:
            raise ValueError("anomaly_operator_database_required")
        engine = create_engine(
            settings.database_url.get_secret_value(), connect_args={"connect_timeout": 3}
        )
        registry = AnomalyRegistry(
            port=args.registry_port,
            compose=settings.network_mode == "compose",
            environment=settings.app_env,
        )
        journal = PostgresJournal(engine)
        if args.review:
            document = json.loads(raw)
            if set(document) != {"model_version"}:
                raise ValueError("anomaly_review_requires_immutable_version")
            version = TypeAdapter(Version).validate_json(json.dumps(document["model_version"]))
            with journal.locked(MODEL):
                binding = journal.binding(MODEL, version)
                registry.validate(binding)
                result = {
                    "review_id": "anomaly-review-sha256-"
                    + json_sha256(binding.model_dump(mode="json")),
                    "model_name": MODEL,
                    "model_version": version,
                    "qualification_sha256": binding.qualification_sha256,
                    "quality_and_saved_model_replay": "passed",
                    "rejected": journal.rejected(MODEL, version),
                    "aliases": registry.aliases(MODEL),
                    "transport_durability": "offline_only",
                }
        else:
            request = Request.model_validate_json(raw)
            result = Lifecycle(registry, journal, environment=settings.app_env).execute(
                request, actor
            )
            if request.action == "register":
                # A retry repairs projection after a completed enrollment; it
                # never creates another registry version or changes aliases.
                with journal.locked(MODEL):
                    version = TypeAdapter(Version).validate_json(
                        json.dumps(result["model_version"])
                    )
                    binding = journal.binding(MODEL, version)
                uri = binding.source_uri
                evidence = PostgresAnomalyEvaluations(engine).register(
                    binding,
                    decode_json(registry.artifact(uri, "gate_segments.json", limit=8 * 1024**2)),
                    decode_json(registry.artifact(uri, "config.json", limit=8 * 1024**2)),
                    lambda name: registry.artifact(uri, name, limit=8 * 1024**2),
                )
                result["evaluation_id"] = evidence.descriptor.evaluation_id
                result["evaluation_projection"] = "persisted_recomputed"
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        print('{"error":"anomaly_lifecycle_operation_failed"}', file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
