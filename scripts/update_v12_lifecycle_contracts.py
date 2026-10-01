"""Generate/check v12 registry and database release contracts, independent of lifecycle v1."""

import argparse
from pathlib import Path

from retailops_ai.data_contracts.common import Contract
from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.model_lifecycle.v12_lifecycle_contracts import (
    V12Binding,
    V12LifecycleRequest,
    V12ModelRelease,
    V12RegistrySource,
)
from retailops_ai.model_lifecycle.v12_metadata_contracts import (
    V12CatalogModel,
    V12CatalogVersion,
    V12EvaluationDetail,
    V12EvaluationEvidence,
    V12EvaluationPage,
    V12EvaluationQuery,
    V12ModelPage,
    V12VersionPage,
)

ROOT = Path(__file__).resolve().parents[1] / "contracts/model_lifecycle/v12"
SCHEMAS: dict[str, type[Contract]] = {
    "source": V12RegistrySource,
    "binding": V12Binding,
    "request": V12LifecycleRequest,
    "release": V12ModelRelease,
    "catalog-model": V12CatalogModel,
    "catalog-version": V12CatalogVersion,
    "model-page": V12ModelPage,
    "version-page": V12VersionPage,
    "evaluation-evidence": V12EvaluationEvidence,
    "evaluation-query": V12EvaluationQuery,
    "evaluation-page": V12EvaluationPage,
    "evaluation-detail": V12EvaluationDetail,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for name, model in SCHEMAS.items():
        path = ROOT / (name + ".schema.json")
        raw = canonical_bytes(model.model_json_schema()) + b"\n"
        if args.check:
            if not path.is_file() or path.read_bytes() != raw:
                print("v12_lifecycle_contract_snapshot_mismatch")
                return 1
        else:
            ROOT.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
    print("V12 lifecycle contracts passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
