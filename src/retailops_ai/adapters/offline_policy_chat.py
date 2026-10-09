"""Deterministic policy rendering for test-only integration; no language model or network."""

import json
from typing import Literal

from retailops_ai.agent.chat import ChatRequest, ProviderFailure
from retailops_ai.agent.chat_config import ChatModelConfig
from retailops_ai.agent.chat_contracts import ProviderReply


class OfflinePolicyChat:
    source_kind: Literal["fixture", "runtime"] = "fixture"

    def __init__(self, model: ChatModelConfig) -> None:
        if model.provider != "fake":
            raise ValueError("offline_chat_requires_fake_model")
        self.model = ChatModelConfig.model_validate_json(model.model_dump_json())

    def input_token_bound(self, request: ChatRequest) -> int:
        # Fixed synthetic protocol units; this is not a tokenizer measurement.
        return 100

    async def generate(self, request: ChatRequest) -> ProviderReply:
        references = json.loads(request.references_json)
        policy = references["server_evidence_policy"]
        if request.expected_kind == "tool_plan":
            body = {"kind": "tool_plan", "tools": policy["permitted_calls"]}
        elif request.expected_kind == "answer":
            facts = policy["facts"]
            required = set(policy["required_document_ids"])
            selected = []
            # Preserve every required document requirement before adding other facts.
            for fact in facts:
                if required & set(fact["document_requirement_ids"]):
                    selected.append(fact)
                    required -= set(fact["document_requirement_ids"])
            if len(selected) > 5 or required:
                raise ProviderFailure("schema")
            for fact in facts:
                if len(selected) >= 5:
                    break
                if fact not in selected:
                    selected.append(fact)
            claims = [fact["evidence"] for fact in selected]
            document_refs = {c["source_ref"] for c in claims if c["source_type"] == "document"}
            actions = []
            for candidate in policy["suggestion_candidates"]:
                grain = f"product={candidate['product_id']}; selling_location={candidate['selling_location_id']}; channel={candidate['channel']}"
                refs = {c["source_ref"] for c in claims if grain in c["claim"]}
                if set(candidate["evidence_refs"]) <= refs:
                    actions.append(
                        {
                            k: candidate[k]
                            for k in (
                                "action",
                                "priority",
                                "rationale",
                                "evidence_refs",
                                "requires_human_review",
                            )
                        }
                    )
            outcome = policy["expected_outcome"]
            summary = (
                "\n".join(c["claim"] for c in claims)
                if outcome == "answered"
                else "The authorized sources do not provide sufficient consistent evidence for this bounded request."
            )
            body = dict(
                kind="answer",
                outcome=outcome,
                summary=summary,
                evidence=claims,
                recommended_actions=actions,
                confidence="medium" if outcome == "answered" else "low",
                data_freshness=policy["data_freshness"],
                limitations=policy["limitations"],
                citations=[
                    c for c in references["citation_candidates"] if c["source_ref"] in document_refs
                ],
            )
        else:
            raise ProviderFailure("schema")
        return ProviderReply.model_validate_json(
            json.dumps(dict(body=json.dumps(body), usage=dict(input_tokens=100, output_tokens=20)))
        )
