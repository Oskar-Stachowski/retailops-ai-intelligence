"""Regenerate reviewed diagnostic schemas and examples; tests enforce the snapshots."""

import json
from importlib.metadata import version
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel

from retailops_ai.api.app import create_app
from retailops_ai.api.models import DependencyStatus, Health, Problem, Ready, ServiceVersion
from retailops_ai.api.schema import contract_openapi
from retailops_ai.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def main() -> None:
    models: list[tuple[str, BaseModel]] = [
        ("health", Health()),
        (
            "readiness",
            Ready(
                status="ready",
                dependencies=[DependencyStatus(name="startup", required=True, status="up")],
            ),
        ),
        ("service-version", ServiceVersion(version=version("retailops-ai-intelligence"))),
        (
            "problem",
            Problem(
                title="Service Unavailable",
                status=503,
                detail="A required dependency is unavailable.",
                instance="urn:uuid:11111111-1111-4111-8111-111111111111",
                correlation_id=UUID("11111111-1111-4111-8111-111111111111"),
                readiness=Ready(
                    status="not_ready",
                    dependencies=[DependencyStatus(name="startup", required=True, status="down")],
                ),
            ),
        ),
    ]
    for name, example in models:
        write_json(ROOT / f"contracts/{name}.v1.schema.json", type(example).model_json_schema())
        write_json(ROOT / f"contracts/{name}.v1.example.json", example.model_dump(mode="json"))
    app = create_app(Settings(APP_ENV="test", ARTIFACT_ROOT="./artifacts"))
    write_json(
        ROOT / "contracts/diagnostics.openapi.json", contract_openapi(app.openapi(), access=False)
    )


if __name__ == "__main__":
    main()
