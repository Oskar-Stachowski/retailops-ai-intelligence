"""Graph schema snapshots and manifest checks; approved runtime checksums are never rewritten."""

import argparse
import json
from pathlib import Path

from pydantic import BaseModel

from retailops_ai.agent.evaluation_contracts import (
    AgentEvaluationRelease,
    AgentEvaluationReport,
    AgentGoldenSet,
)
from retailops_ai.agent.graph_config import AgentGraphConfig, load_graph_config
from retailops_ai.agent.graph_contracts import GraphPolicy, GraphRequest, GraphResult, SafeTrace
from retailops_ai.agent.suggestions import SuggestionCandidate, SuggestionPolicy
from retailops_ai.data_contracts.identity import canonical_sha256

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts/agent/v1"


def artifacts() -> dict[str, object]:
    resolved = load_graph_config(ROOT / "agent/graph.fake.native-v12.v1.json")
    request = GraphRequest.model_validate_json(
        json.dumps(
            {
                "schema_version": "1.0",
                "question": "What evidence is available?",
                "intent": "sales",
                "scope": {
                    "product_ids": ["p-101"],
                    "selling_location_ids": ["s-03"],
                    "channel": "store",
                },
                "as_of": "2026-08-22T23:59:59Z",
                "window": {"start": "2026-08-16", "end": "2026-08-22"},
                "comparison_window": None,
                "limit": 5,
            }
        )
    )
    trace = SafeTrace.model_validate_json(
        json.dumps(
            {
                "schema_version": "1.0",
                "trace_id": "trace-" + "a" * 32,
                "correlation_id": "correlation-" + "b" * 32,
                "owner_id": "fixture-agent-operator",
                "scope": request.scope.model_dump(mode="json"),
                "config_id": resolved.config_id,
                "chat_config_id": resolved.chat.config_id,
                "request_sha256": canonical_sha256(request.model_dump(mode="json")),
                "index_id": None,
                "release_refs": [],
                "source_refs": [],
                "release_refs_total": 0,
                "source_refs_total": 0,
                "status": "failed",
                "error_code": "dependency_unavailable",
                "nodes": [],
                "tool_calls": 0,
                "model_calls": 0,
                "extra_evidence_rounds": 0,
                "repairs": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "estimated_cost": "0",
                "fixture_only": True,
                "created_at": "2026-08-23T00:00:00Z",
            }
        )
    )
    result = GraphResult(
        status="failed", error_code="dependency_unavailable", answer=None, trace=trace
    )
    examples: dict[str, BaseModel] = {
        "graph-request": request,
        "graph-config": resolved.config,
        "graph-result": result,
        "graph-policy": resolved.config.policy,
        "safe-trace": trace,
    }
    models: dict[str, type[BaseModel]] = {
        "graph-request": GraphRequest,
        "graph-config": AgentGraphConfig,
        "graph-result": GraphResult,
        "graph-policy": GraphPolicy,
        "safe-trace": SafeTrace,
    }
    artifacts: dict[str, object] = {}
    for name, model in models.items():
        artifacts[f"{name}.v1.schema.json"] = model.model_json_schema() | {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"urn:retailops:agent:{name}:1.0",
        }
        artifacts[f"{name}.v1.example.json"] = examples[name].model_dump(mode="json")
    artifacts["graph-bindings.v1.json"] = {
        "schema_version": "1.0",
        "config_id": resolved.config_id,
        "chat_config_id": resolved.chat.config_id,
        "graph_version": resolved.config.graph_version,
        "evidence_policy_version": resolved.config.evidence_policy_version,
        "code_sha256": resolved.config.code_sha256,
        "schemas_sha256": resolved.config.schemas_sha256,
        "fixture_only": True,
        "examples_are_synthetic_metadata": True,
    }
    evaluation_models: dict[str, type[BaseModel]] = {
        "suggestion-candidate": SuggestionCandidate,
        "suggestion-policy": SuggestionPolicy,
        "agent-golden-set": AgentGoldenSet,
        "agent-evaluation-release": AgentEvaluationRelease,
        "agent-evaluation-report": AgentEvaluationReport,
    }
    for name, model in evaluation_models.items():
        artifacts[f"{name}.v1.schema.json"] = model.model_json_schema() | {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"urn:retailops:agent:{name}:1.0",
        }
    return artifacts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        values = artifacts()
    except ValueError:
        print("Agent graph configuration bindings are invalid.")
        return 1
    stale = []
    for name, value in values.items():
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        path = CONTRACTS / name
        if args.check:
            if not path.is_file() or path.read_text() != text:
                stale.append(name)
        else:
            CONTRACTS.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    if stale:
        print("Agent graph snapshots differ: " + ", ".join(sorted(stale)))
        return 1
    print("Agent graph contracts checked." if args.check else "Agent graph contracts generated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
