"""Keep local operator/evidence contracts aligned with strict runtime validators."""

import argparse
import json
from pathlib import Path

from retailops_ai.model_lifecycle.contracts import Binding, Qualification, Release, Request
from retailops_ai.model_lifecycle.evaluation_contracts import (
    EvaluationDetail,
    EvaluationEvidence,
    EvaluationPage,
    EvaluationQuery,
)
from retailops_ai.model_lifecycle.read_contracts import (
    CatalogModel,
    CatalogQuery,
    ModelPage,
    VersionPage,
)

ROOT = Path(__file__).resolve().parents[1] / "contracts/model_lifecycle/v1"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for name, model in (
        ("qualification", Qualification),
        ("binding", Binding),
        ("decision_request", Request),
        ("release", Release),
        ("catalog_query", CatalogQuery),
        ("catalog_model", CatalogModel),
        ("catalog_models", ModelPage),
        ("catalog_versions", VersionPage),
        ("evaluation_evidence", EvaluationEvidence),
        ("evaluation_query", EvaluationQuery),
        ("evaluations", EvaluationPage),
        ("evaluation", EvaluationDetail),
    ):
        expected = json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"
        path = ROOT / (name + ".schema.json")
        if args.check:
            if not path.is_file() or path.read_text() != expected:
                print("model_lifecycle_contract_snapshot_mismatch: " + name)
                return 1
        else:
            ROOT.mkdir(parents=True, exist_ok=True)
            path.write_text(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
