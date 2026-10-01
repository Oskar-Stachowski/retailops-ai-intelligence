"""Private original-verifier import of immutable v12 evaluations; never promotes models."""

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine

from retailops_ai.config import load_settings
from retailops_ai.model_lifecycle.v12_evaluation_importer import project_evaluation
from retailops_ai.model_lifecycle.v12_evaluation_store import PostgresV12Evaluations
from retailops_ai.model_lifecycle.v12_evidence import load_evidence
from retailops_ai.security.model_operator import model_operator


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("run-dir", "verifier-python", "policy-file", "credentials-file"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    engine = None
    try:
        actor = model_operator(args.policy_file, args.credentials_file)
        settings = load_settings(args.env_file)
        if settings.database_url is None:
            raise ValueError("v12_evaluation_database_required")
        evidence = project_evaluation(load_evidence(args.run_dir, args.verifier_python))
        engine = create_engine(
            settings.database_url.get_secret_value(),
            connect_args={"connect_timeout": 3},
            hide_parameters=True,
        )
        result = PostgresV12Evaluations(engine, settings.app_env).register(evidence, actor)
        print(
            json.dumps(
                dict(
                    status="recorded",
                    evaluation_id=result.evaluation_id,
                    evidence_sha256=result.evidence_sha256,
                    quality_status=result.descriptor.quality_status,
                    serving_eligible=False,
                )
            )
        )
        return 0
    except Exception:
        sys.stderr.write('{"error":"v12_evaluation_registration_failed"}\n')
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
