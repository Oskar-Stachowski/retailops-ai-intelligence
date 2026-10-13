"""Snapshot the counted transport/circuit/smoke contracts without AWS or rebinding releases."""

import argparse
import json
from pathlib import Path

from retailops_ai.adapters.bedrock_chat import TRANSPORT_VERSION, CircuitPolicy
from retailops_ai.agent.bedrock_smoke import BedrockSmokeProfile
from retailops_ai.agent.graph_config import load_graph_config

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    config = load_graph_config(ROOT / "agent/graph.bedrock-smoke.prepaid.v5.json")
    profile = BedrockSmokeProfile.model_validate_json(
        (ROOT / "agent/bedrock-smoke.prepaid.v5.json").read_bytes()
    )
    comparison = load_graph_config(ROOT / "agent/graph.sonnet-smoke.prepaid.v5.json")
    comparison_profile = BedrockSmokeProfile.model_validate_json(
        (ROOT / "agent/sonnet-smoke.prepaid.v5.json").read_bytes()
    )
    documents = load_graph_config(ROOT / "agent/graph.document-smoke.prepaid.v5.json")
    document_profile = BedrockSmokeProfile.model_validate_json(
        (ROOT / "agent/document-smoke.prepaid.v5.json").read_bytes()
    )
    artifacts = {
        "circuit-policy.v1.schema.json": CircuitPolicy.model_json_schema(),
        "bedrock-smoke-profile.v1.schema.json": BedrockSmokeProfile.model_json_schema(),
        "bedrock-bindings.v1.json": {
            "schema_version": "1.0",
            "transport_version": TRANSPORT_VERSION,
            "graph_config_id": config.config_id,
            "profile_id": profile.profile_id(),
            "offline_release_id": profile.offline_release_id,
            "provider_invoked": False,
            "fixture_sources": True,
            "comparison": {
                "graph_config_id": comparison.config_id,
                "profile_id": comparison_profile.profile_id(),
                "offline_release_id": comparison_profile.offline_release_id,
            },
            "documents": {
                "graph_config_id": documents.config_id,
                "profile_id": document_profile.profile_id(),
                "offline_release_id": document_profile.offline_release_id,
            },
        },
    }
    for name, artifact in artifacts.items():
        path = ROOT / "contracts/agent/v1" / name
        content = json.dumps(artifact, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != content:
                raise SystemExit("agent_bedrock_contract_snapshot_mismatch")
        else:
            path.write_text(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
