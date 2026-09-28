"""Generate/check access schemas, without generating usable credentials."""

import argparse
import json
from pathlib import Path

from pydantic import BaseModel

from retailops_ai.api.access import (
    AccessDecision,
    ForecastCheckRequest,
    IdentityResponse,
    PolicyMetadata,
)
from retailops_ai.api.app import create_app
from retailops_ai.api.schema import contract_openapi
from retailops_ai.config import Settings
from retailops_ai.security.models import AccessPolicy, GrantTemplate

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = ROOT / "contracts/access/v1"


def artifacts() -> dict[str, object]:
    grant = {
        "principal_id": "local-viewer",
        "roles": ["viewer"],
        "capabilities": ["forecast:read"],
        "scope": {
            "product_ids": ["p-101"],
            "selling_location_ids": ["s-03"],
            "channels": ["store"],
        },
    }
    request = {
        "schema_version": "1.0",
        "product_ids": ["p-101"],
        "selling_location_ids": ["s-03"],
        "channel": "store",
    }
    models: dict[str, type[BaseModel]] = {
        "grant-template": GrantTemplate,
        "policy": AccessPolicy,
        "identity": IdentityResponse,
        "forecast-check": ForecastCheckRequest,
        "access-decision": AccessDecision,
        "policy-metadata": PolicyMetadata,
    }
    examples = {
        "grant-template": {
            "schema_version": "1.0",
            "policy_id": "local-example-v1",
            "grants": [
                grant,
                {
                    "principal_id": "local-admin",
                    "roles": ["admin"],
                    "capabilities": ["access:admin"],
                    "scope": None,
                },
            ],
        },
        "identity": {"schema_version": "1.0", **grant},
        "forecast-check": request,
        "access-decision": {
            "schema_version": "1.0",
            "principal_id": "local-viewer",
            "capability": "forecast:read",
            "allowed": True,
            "scope": request,
        },
        "policy-metadata": {
            "schema_version": "1.0",
            "policy_id": "local-example-v1",
            "principal_count": 2,
            "credential_count": 2,
        },
    }
    result: dict[str, object] = {}
    for name, model in models.items():
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:access:{name}:1.0"
        result[f"{name}.v1.schema.json"] = schema
        if name in examples:
            model.model_validate_json(json.dumps(examples[name]))
            result[f"{name}.v1.example.json"] = examples[name]
    app = create_app(Settings(APP_ENV="test", ARTIFACT_ROOT="./artifacts", API_AUTH_FILE=None))
    result["access.openapi.json"] = contract_openapi(app.openapi(), access=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=CONTRACT_ROOT)
    args = parser.parse_args()
    stale = []
    for name, value in artifacts().items():
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        path = args.output / name
        if args.check:
            if not path.is_file() or path.read_text() != text:
                stale.append(name)
        else:
            args.output.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    if stale:
        print("Access snapshots differ: " + ", ".join(sorted(stale)))
        return 1
    print("Access snapshots checked." if args.check else "Access contracts generated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
