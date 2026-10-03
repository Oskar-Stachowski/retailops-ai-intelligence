"""Private authenticated import of AI 04 evaluation evidence; no serving decision."""

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine

from retailops_ai.config import load_settings
from retailops_ai.model_lifecycle.evaluation_importer import import_evidence
from retailops_ai.model_lifecycle.evaluation_store import PostgresEvaluations
from retailops_ai.security.model_operator import model_operator


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--policy-file", type=Path, required=True)
    parser.add_argument("--credentials-file", type=Path, required=True)
    args = parser.parse_args()
    engine = None
    try:
        model_operator(args.policy_file, args.credentials_file)
        settings = load_settings()
        if settings.database_url is None:
            raise ValueError("evaluation_database_required")
        evidence = import_evidence(args.run_dir)
        engine = create_engine(
            settings.database_url.get_secret_value(), connect_args={"connect_timeout": 3}
        )
        registered = PostgresEvaluations(engine, settings.app_env).register(evidence)
        print(
            json.dumps(
                dict(
                    status="registered",
                    evaluation_id=registered.descriptor.evaluation_id,
                    evidence_sha256=registered.evidence_sha256,
                    serving_eligible=False,
                )
            )
        )
        return 0
    except Exception:
        print('{"error":"evaluation_import_failed"}', file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
