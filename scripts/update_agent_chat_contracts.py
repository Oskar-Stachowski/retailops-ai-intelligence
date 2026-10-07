"""Generate chat snapshots; never rewrite the reviewed model/prompt bindings."""

import argparse
import json
from pathlib import Path

from pydantic import BaseModel

from retailops_ai.agent.chat_config import AgentChatConfig, load_chat_config
from retailops_ai.agent.chat_contracts import AnswerDraft, PlanDraft, ProviderReply

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts/agent/v1"


def artifacts() -> dict[str, object]:
    resolved = load_chat_config(ROOT / "agent/chat.fake.native-v12.v1.json")
    config = resolved.config
    plan = PlanDraft(kind="tool_plan", tools=[])
    answer = AnswerDraft.model_validate_json(
        json.dumps(
            {
                "kind": "answer",
                "outcome": "insufficient_evidence",
                "summary": "No authorized business data was retrieved for this fixture.",
                "evidence": [],
                "recommended_actions": [],
                "confidence": "low",
                "data_freshness": {
                    name: {"as_of": None, "status": "not_requested"}
                    for name in ("sales", "inventory", "predictions")
                },
                "citations": [],
                "limitations": ["Synthetic response; no business source acceptance."],
            }
        )
    )
    reply = ProviderReply.model_validate_json(
        json.dumps(
            {
                "body": plan.model_dump_json(),
                "usage": {"input_tokens": 100, "output_tokens": 20},
            }
        )
    )
    examples: dict[str, BaseModel] = {
        "chat-plan": plan,
        "chat-answer": answer,
        "chat-config": config,
        "provider-reply": reply,
    }
    models: dict[str, type[BaseModel]] = {
        "chat-plan": PlanDraft,
        "chat-answer": AnswerDraft,
        "chat-config": AgentChatConfig,
        "provider-reply": ProviderReply,
    }
    results: dict[str, object] = {}
    for name, model in models.items():
        results[f"{name}.v1.schema.json"] = model.model_json_schema() | {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"urn:retailops:agent:{name}:1.0",
        }
        results[f"{name}.v1.example.json"] = examples[name].model_dump(mode="json")
    results["chat-bindings.v1.json"] = {
        "schema_version": "1.0",
        "config_id": resolved.config_id,
        "fixture_only": True,
        "prompt_version": config.prompt_version,
        "graph_version": config.graph_version,
        "tool_schemas_sha256": config.tool_schemas_sha256,
        "response_schema_sha256": config.response_schema_sha256,
        "knowledge_index_id": config.knowledge_index_id,
        "prompts": [p.model_dump(mode="json") for p in config.prompts],
    }
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        values = artifacts()
    except ValueError:
        print("Agent chat configuration bindings are invalid.")
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
        print("Agent chat snapshots differ: " + ", ".join(sorted(stale)))
        return 1
    print("Agent chat contracts checked." if args.check else "Agent chat contracts generated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
