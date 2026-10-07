"""Reviewable Assistant wire schemas and OpenAPI, with no provider or credentials."""

import argparse
import json
from pathlib import Path

from pydantic import BaseModel

from retailops_ai.api.app import create_app
from retailops_ai.api.schema import contract_openapi
from retailops_ai.assistant.contracts import (
    AssistantAnswer,
    AssistantQuery,
    AssistantRun,
    PersistedSuggestion,
)
from retailops_ai.assistant.routes import QuestionRoutes
from retailops_ai.assistant.runtime import DocumentRuntimeConfig
from retailops_ai.assistant.service import AdmissionPolicy
from retailops_ai.config import Settings

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts/assistant/v1"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=CONTRACTS)
    args = parser.parse_args()
    models: dict[str, type[BaseModel]] = {
        "query": AssistantQuery,
        "answer": AssistantAnswer,
        "run": AssistantRun,
        "suggestion": PersistedSuggestion,
        "admission-policy": AdmissionPolicy,
        "document-runtime": DocumentRuntimeConfig,
        "question-routes": QuestionRoutes,
    }
    artifacts: dict[str, object] = {}
    for name, model in models.items():
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:assistant:{name}:1.0"
        artifacts[f"{name}.v1.schema.json"] = schema
    artifacts["query.v1.example.json"] = {
        "question": "What evidence is available?",
        "scope": {
            "product_ids": ["22222222-2222-4222-8222-222222222222"],
            "store_ids": ["33333333-3333-4333-8333-333333333333"],
            "from": "2026-04-01",
            "to": "2026-04-07",
        },
        "conversation_id": None,
    }
    artifacts["admission-policy.v1.example.json"] = AdmissionPolicy().model_dump(mode="json")
    AssistantQuery.model_validate_json(json.dumps(artifacts["query.v1.example.json"]))
    app = create_app(Settings(APP_ENV="test", ARTIFACT_ROOT="./artifacts", API_AUTH_FILE=None))
    artifacts["assistant.openapi.json"] = contract_openapi(
        app.openapi(), access=True, assistant=True
    )
    stale = []
    for name, value in artifacts.items():
        content = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        path = args.output / name
        if args.check:
            if not path.is_file() or path.read_text() != content:
                stale.append(name)
        else:
            args.output.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    if stale:
        print("Assistant snapshots differ: " + ", ".join(sorted(stale)))
        return 1
    print("Assistant snapshots checked." if args.check else "Assistant contracts generated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
