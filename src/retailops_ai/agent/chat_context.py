"""Reference snapshots come from authorized tool execution, never from caller payloads."""

from dataclasses import dataclass

from retailops_ai.agent.chat_config import ResolvedChatConfig
from retailops_ai.agent.chat_contracts import (
    AnswerDraft,
    AnswerFreshness,
    DomainFreshness,
    DraftCitation,
)
from retailops_ai.agent.tools import ForecastResult, KnowledgeResult, ToolOutput
from retailops_ai.data_contracts.common import UtcTime
from retailops_ai.data_contracts.identity import canonical_sha256


class InvalidEvidence(ValueError):
    pass


def tool_result_ref(output: ToolOutput) -> str:
    return "tool-result-sha256-" + canonical_sha256(output.model_dump(mode="json"))


def result_as_of(output: ToolOutput) -> UtcTime | None:
    if isinstance(output, KnowledgeResult):
        return None
    return output.result.as_of if isinstance(output, ForecastResult) else output.as_of


@dataclass(frozen=True)
class EvidenceSnapshot:
    outputs_json: tuple[str, ...]

    @classmethod
    def build(
        cls, outputs: tuple[ToolOutput, ...], config: ResolvedChatConfig
    ) -> "EvidenceSnapshot":
        for output in outputs:
            if isinstance(output, KnowledgeResult) and (
                output.index_id != config.config.knowledge_index_id
                or output.retrieval_config_id != config.config.retrieval.config_id()
            ):
                raise InvalidEvidence("knowledge_config_binding_mismatch")
        return cls(tuple(output.model_dump_json() for output in outputs))

    @property
    def outputs(self) -> tuple[ToolOutput, ...]:
        from retailops_ai.agent.tools import OUTPUT

        return tuple(OUTPUT.validate_json(raw) for raw in self.outputs_json)

    def citations(self) -> dict[str, DraftCitation]:
        citations = {}
        for output in self.outputs:
            if not isinstance(output, KnowledgeResult):
                continue
            for hit in output.items:
                chunk = hit.chunk
                for location in chunk.occurrences:
                    citation = DraftCitation(
                        repository=chunk.repository,
                        commit_sha=chunk.commit_sha,
                        path=chunk.path,
                        heading=" / ".join(h.title for h in chunk.heading_path),
                        chunk_id=chunk.chunk_id,
                        document_status=chunk.document_status,
                        source_ref=location.source_ref,
                    )
                    citations[location.source_ref] = citation
        return citations

    def freshness(self) -> AnswerFreshness:
        groups: dict[str, list[ToolOutput]] = {"sales": [], "inventory": [], "predictions": []}
        for output in self.outputs:
            domain = {
                "get_sales_summary": "sales",
                "get_inventory_status": "inventory",
                "get_demand_forecast": "predictions",
                "get_stockout_risk": "predictions",
                "get_detected_anomalies": "predictions",
                "get_model_status": "predictions",
            }.get(output.tool)
            if domain:
                groups[domain].append(output)
        values = {}
        for name, outputs in groups.items():
            timestamps = [result_as_of(output) for output in outputs]
            if not outputs:
                values[name] = DomainFreshness(as_of=None, status="not_requested")
            elif any(
                (output.result.status if isinstance(output, ForecastResult) else output.status)
                != "ok"
                for output in outputs
            ):
                values[name] = DomainFreshness(as_of=None, status="missing")
            else:
                available = [value for value in timestamps if value is not None]
                values[name] = DomainFreshness(as_of=min(available), status="current")
        return AnswerFreshness(**values)

    def payload(self) -> dict[str, object]:
        return {
            "content_trust": "untrusted_reference",
            "tool_results": [
                {"ref": tool_result_ref(output), "result": output.model_dump(mode="json")}
                for output in self.outputs
            ],
            "citation_candidates": [c.model_dump(mode="json") for c in self.citations().values()],
            "data_freshness": self.freshness().model_dump(mode="json"),
        }

    def validate_answer(self, answer: AnswerDraft) -> None:
        tools = {tool_result_ref(output): output for output in self.outputs}
        citations = self.citations()
        refs = set(tools) | set(citations)
        provided_citations = {citation.source_ref for citation in answer.citations}
        for citation in answer.citations:
            if citations.get(citation.source_ref) != citation:
                raise InvalidEvidence("unretrieved_or_modified_citation")
        for claim in answer.evidence:
            if not set(claim.supporting_refs) <= refs:
                raise InvalidEvidence("unknown_supporting_ref")
            if claim.source_type == "tool":
                source = tools.get(claim.source_ref)
                if source is None or result_as_of(source) != claim.as_of:
                    raise InvalidEvidence("unknown_tool_ref_or_as_of")
                status = (
                    source.result.status if isinstance(source, ForecastResult) else source.status
                )
                if status != "ok":
                    raise InvalidEvidence("missing_data_is_not_a_business_fact")
            elif claim.source_type == "document":
                if claim.source_ref not in provided_citations:
                    raise InvalidEvidence("document_claim_requires_retrieved_citation")
            else:
                # Approved formula registry belongs to the graph/evidence policy scope.
                raise InvalidEvidence("calculation_policy_not_implemented")
        if answer.data_freshness != self.freshness():
            raise InvalidEvidence("invented_data_freshness")
        if answer.recommended_actions:
            # Later server policy may create suggestions; model proposals cannot authorize them.
            raise InvalidEvidence("suggestion_policy_not_implemented")
