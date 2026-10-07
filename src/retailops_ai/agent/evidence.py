"""Closed factual language: exact typed measurements, registered arithmetic and source quotes."""

import json
import re
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Literal, cast

from retailops_ai.agent.chat_context import (
    EvidenceSnapshot,
    InvalidEvidence,
    result_as_of,
    tool_result_ref,
)
from retailops_ai.agent.chat_contracts import (
    AnswerDraft,
    AnswerFreshness,
    DomainFreshness,
    EvidenceClaim,
)
from retailops_ai.agent.document_evidence import question_key
from retailops_ai.agent.execution import ToolSession
from retailops_ai.agent.graph_contracts import GraphPolicy, GraphRequest
from retailops_ai.agent.suggestions import SuggestionCandidate, candidates
from retailops_ai.agent.tools import (
    INPUT,
    AnomalyItem,
    ForecastResult,
    InventoryItem,
    KnowledgeResult,
    ModelStatusItem,
    OperationsItem,
    RiskItem,
    SalesItem,
    SalesResult,
    ToolInput,
    ToolName,
)
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.data_contracts.prediction import PredictionRecord
from retailops_ai.forecast_jobs.v12_read_contracts import V12ForecastItem

POLICY_SPEC = {
    "version": "typed-facts-v2",
    "numeric_policy": "exact-decimal-from-typed-values-v1",
    "language": "canonical-claims-and-summary-only-v1",
    "document_policy": "question-requirements-and-exact-source-quotes-v1",
    "deployment_policy": "deployed-release-from-model-status-only-v1",
    "calculation": "sales-period-difference-v1: current minus previous; same grain, unit, equal disjoint periods",
    "conflicts": "same-measurement-different-value-blocks-answer-v1",
    "suggestions": "read-only-review-v1: server candidates, exact actions, selected grain evidence, expiry",
    "refusal_gate": "explicit-write-execution-or-secret-request-patterns-v1",
}

DENIED_QUESTION = re.compile(
    r"(?:\b(?:execute|run|wykonaj|uruchom)\s+(?:sql|shell|python|code|kod)|\b(?:change|set)\s+(?:the\s+)?price|\b(?:zmień|zmien|ustaw)\s+cen|\b(?:place|create)\s+(?:an?\s+)?(?:order|purchase)|\b(?:zamów|zamow)\b|\b(?:promote|promuj)\s+(?:the\s+)?model|\b(?:reveal|show|ujawnij|pokaż|pokaz)\s+(?:the\s+)?(?:secret|password|token|hasło|sekret))",
    re.IGNORECASE,
)


def number(value: float | Decimal) -> str:
    decimal = Decimal(str(value))
    if decimal == 0:
        return "0"
    text = format(decimal, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def call_id(request: ToolInput) -> str:
    return canonical_sha256(request.model_dump(mode="json"))


def required_calls(request: GraphRequest) -> tuple[ToolInput, ...]:
    tools = cast(
        tuple[ToolName, ...],
        {
            "sales": ("get_sales_summary",),
            "sales_comparison": ("get_sales_summary",),
            "inventory": ("get_inventory_status",),
            "forecast": ("get_demand_forecast",),
            "risk": ("get_stockout_risk",),
            "anomalies": ("get_detected_anomalies",),
            "operations": ("get_live_operations",),
            "model": ("get_model_status",),
            "documentation": ("search_knowledge",),
            "verified_state": ("search_knowledge",),
            "investigation": (
                "get_sales_summary",
                "get_inventory_status",
                "get_detected_anomalies",
            ),
            "recommendations": (
                "get_stockout_risk",
                "get_inventory_status",
                "get_demand_forecast",
                "get_model_status",
            ),
            "refuse": (),
        }[request.intent],
    )
    calls = []
    for tool in tools:
        windows = [request.window]
        if request.intent == "sales_comparison" and request.comparison_window is not None:
            windows.append(request.comparison_window)
        for window in windows:
            raw: dict[str, object] = {
                "schema_version": "1.0",
                "contract_type": "tool_request",
                "tool": tool,
                "scope": request.scope.model_dump(mode="json"),
                "as_of": request.as_of.isoformat(),
                "limit": request.limit,
            }
            if tool == "search_knowledge":
                raw = {
                    "schema_version": "1.0",
                    "contract_type": "tool_request",
                    "tool": tool,
                    "retrieval": {
                        "schema_version": "1.0",
                        "question": request.question,
                        "purpose": "verified_state"
                        if request.intent == "verified_state"
                        else "documentation",
                    },
                }
            elif tool == "get_demand_forecast":
                raw["scope"] = request.scope.model_dump(mode="json") | {
                    "target_from": window.start.isoformat(),
                    "target_to": window.end.isoformat(),
                }
            elif tool in {"get_sales_summary", "get_detected_anomalies", "get_stockout_risk"}:
                raw["window"] = window.model_dump(mode="json")
                if tool == "get_sales_summary":
                    raw["grain"] = "product_selling_location_channel_period"
            calls.append(INPUT.validate_json(json.dumps(raw)))
    return tuple(calls)


@dataclass(frozen=True)
class Fact:
    measurement: str
    value: str
    claim_json: str
    call_ids: tuple[str, ...]
    document_requirement_ids: tuple[str, ...] = ()

    @property
    def claim(self) -> EvidenceClaim:
        return EvidenceClaim.model_validate_json(self.claim_json)

    @property
    def fact_id(self) -> str:
        return "fact-sha256-" + canonical_sha256(
            {
                "measurement": self.measurement,
                "value": self.value,
                "claim": self.claim.model_dump(mode="json"),
                "calls": self.call_ids,
                "document_requirements": self.document_requirement_ids,
            }
        )

    def payload(self) -> dict[str, object]:
        return {
            "fact_id": self.fact_id,
            "evidence": self.claim.model_dump(mode="json"),
            "document_requirement_ids": self.document_requirement_ids,
        }


@dataclass(frozen=True)
class Catalogue:
    facts: tuple[Fact, ...]
    expected_outcome: Literal["answered", "insufficient_evidence", "refused"]
    limitations: tuple[str, ...]
    freshness_json: str
    required_ids: tuple[str, ...]
    blocking: bool
    candidates_json: tuple[str, ...] = ()
    required_document_ids: tuple[str, ...] = ()

    def suggestions(self, facts: tuple[Fact, ...]) -> tuple[SuggestionCandidate, ...]:
        if self.expected_outcome != "answered":
            return ()
        authorized = []
        for raw in self.candidates_json:
            candidate = SuggestionCandidate.model_validate_json(raw)
            grain = f"product={candidate.product_id}; selling_location={candidate.selling_location_id}; channel={candidate.channel}"
            refs = {fact.claim.source_ref for fact in facts if grain in fact.claim.claim}
            if set(candidate.evidence_refs) <= refs:
                authorized.append(candidate)
        return tuple(authorized)

    def payload(self) -> dict[str, object]:
        return {
            "policy": POLICY_SPEC,
            "facts": [fact.payload() for fact in self.facts],
            "expected_outcome": self.expected_outcome,
            "required_document_ids": self.required_document_ids,
            "limitations": self.limitations,
            "data_freshness": json.loads(self.freshness_json),
            "suggestion_candidates": [json.loads(raw) for raw in self.candidates_json],
            "action_rule": "Copy all candidate draft actions whose evidence_refs are covered by selected claims for that candidate grain; do not change any field.",
            "summary_rule": "Join selected evidence.claim strings with a newline, in the same order; no other assertions.",
        }

    def render(self, facts: tuple[Fact, ...]) -> AnswerDraft:
        if self.expected_outcome == "refused":
            summary = "This request is refused. No operational action is authorized."
        elif self.expected_outcome == "insufficient_evidence":
            summary = "The authorized sources do not provide sufficient consistent evidence for this bounded request."
        else:
            summary = "\n".join(fact.claim.claim for fact in facts)
        return AnswerDraft.model_validate_json(
            json.dumps(
                {
                    "kind": "answer",
                    "outcome": self.expected_outcome,
                    "summary": summary,
                    "evidence": [fact.claim.model_dump(mode="json") for fact in facts],
                    "recommended_actions": [
                        candidate.draft_action().model_dump(mode="json")
                        for candidate in self.suggestions(facts)
                    ],
                    "confidence": "medium" if self.expected_outcome == "answered" else "low",
                    "data_freshness": json.loads(self.freshness_json),
                    "citations": [],
                    "limitations": self.limitations,
                }
            )
        )


class EvidencePolicy:
    def __init__(self, request: GraphRequest, policy: GraphPolicy) -> None:
        self.request = GraphRequest.model_validate_json(request.model_dump_json())
        self.policy = policy
        self.refused = (
            request.intent == "refuse" or DENIED_QUESTION.search(request.question) is not None
        )
        self.calls = () if self.refused else required_calls(self.request)
        self.failures: dict[str, str] = {}
        self.document_rule = next(
            (
                row
                for row in self.policy.document_rules
                if row.intent == self.request.intent
                and question_key(row.question) == question_key(self.request.question)
            ),
            None,
        )

    def build(self, tools: ToolSession, snapshot: EvidenceSnapshot) -> Catalogue:
        facts: list[Fact] = []
        limitations: list[str] = []
        required = tuple(call_id(call) for call in self.calls)
        successful: set[str] = set()
        sales: list[tuple[SalesResult, str]] = []
        inventory_locations: dict[tuple[str, str, str], set[str]] = {}
        for call, output in tools.accepted_calls():
            cid = call_id(call)
            if cid not in required:
                raise InvalidEvidence("unrequested_tool_result")
            if isinstance(output, KnowledgeResult):
                for hit in output.items:
                    chunk = hit.chunk
                    if (
                        self.request.intent == "verified_state"
                        and chunk.document_status != "verified"
                    ):
                        continue
                    citation = next(
                        (c for c in snapshot.citations().values() if c.chunk_id == chunk.chunk_id),
                        None,
                    )
                    if citation is None:
                        continue
                    quotes = self.document_rule.matching_quotes(chunk) if self.document_rule else {}
                    prefix = {
                        "specified": "Planned specification",
                        "implemented": "Implementation documentation",
                        "verified": "Verified evidence for the declared scope and revision",
                        "historical": "Historical reference",
                        "deprecated": "Deprecated reference",
                    }[chunk.document_status]
                    for quote, requirement_ids in quotes.items():
                        text = f"{prefix}; scope={chunk.fact_scope}; revision={chunk.commit_sha}; literal quote={json.dumps(quote, ensure_ascii=False)}"
                        claim = EvidenceClaim(
                            claim=text,
                            source_type="document",
                            source_ref=citation.source_ref,
                            as_of=None,
                            supporting_refs=[],
                            calculation_id=None,
                        )
                        facts.append(
                            Fact(
                                "document:" + chunk.chunk_id + ":" + canonical_sha256(quote),
                                quote,
                                claim.model_dump_json(),
                                (cid,),
                                requirement_ids,
                            )
                        )
                if output.items and any(cid in fact.call_ids for fact in facts):
                    successful.add(cid)
                continue
            status = output.result.status if isinstance(output, ForecastResult) else output.status
            if status != "ok":
                if isinstance(output, SalesResult) and output.qualified_days is not None:
                    states = sorted(
                        {p.status for p in output.qualified_days.points if p.status != "qualified"}
                    )
                    limitations.append(
                        "The entire sales period is withheld because some days are not qualified: "
                        + ", ".join(states)
                        + ". No zero or partial period total is inferred."
                    )
                limitations.append(
                    f"{output.tool}: checked source returned no rows; this is not a measured zero."
                )
                continue
            source = tool_result_ref(output)
            as_of = result_as_of(output)
            rows = output.result.items if isinstance(output, ForecastResult) else output.items
            if isinstance(output, SalesResult):
                sales.append((output, cid))
            for item in rows:
                key = item.key if isinstance(item, PredictionRecord) else item
                grain = f"product={key.product_id}; selling_location={key.selling_location_id}; channel={key.channel}"
                value: str
                measurement: str
                if isinstance(item, SalesItem):
                    value = number(item.observed_sales_units)
                    measurement = f"sales:{grain}:{item.window.start}:{item.window.end}"
                    text = f"Observed sales={value} unit; {grain}; period={item.window.start}..{item.window.end}; as_of={as_of}."
                elif isinstance(item, InventoryItem):
                    inventory_locations.setdefault(
                        (item.product_id, item.selling_location_id, item.channel), set()
                    ).add(item.stock_location_id)
                    value = number(item.available_units)
                    measurement = f"inventory:{grain}:{item.stock_location_id}"
                    text = f"Available inventory={value} unit; physical={number(item.physical_units)} unit; reserved={number(item.reserved_units)} unit; {grain}; stock_location={item.stock_location_id}; mapping={item.mapping_ref}; as_of={as_of}."
                elif isinstance(item, PredictionRecord):
                    if item.quality_status != "passed":
                        limitations.append(
                            "Forecast quality gate is not passed; the forecast cannot support an answered outcome."
                        )
                        continue
                    value = number(item.predicted_units)
                    measurement = (
                        f"forecast:{grain}:{item.key.forecast_origin}:{item.key.target_date}"
                    )
                    text = f"Reported forecast of observed sales={value} unit; {grain}; origin={item.key.forecast_origin}; target_date={item.key.target_date}; model={item.model.model_id}; release={item.release_id}."
                elif isinstance(item, V12ForecastItem):
                    forecast = item.prediction.candidate
                    if forecast.mean is None:
                        limitations.append(
                            f"Native forecast has no reported mean; {grain}; target_date={item.target_date}; exclusion={item.prediction.exclusion_reason}. No zero is inferred."
                        )
                        continue
                    value = number(forecast.mean)
                    measurement = f"forecast:{grain}:{item.forecast_origin}:{item.target_date}"
                    text = f"Reported forecast mean of observed sales={value} unit; {grain}; origin={item.forecast_origin}; target_date={item.target_date}; mean_source={item.prediction.metadata.mean_source}; quality=passed_at_publication; model={item.model_name}; version={item.model_version}; release={item.release_id}; prediction={item.prediction_id}; dataset={item.prediction_dataset_id}."
                elif isinstance(item, RiskItem):
                    value = number(item.probability)
                    measurement = f"risk:{grain}:{item.window.start}:{item.window.end}"
                    text = f"Calibrated stockout probability={value}; threshold={number(item.threshold)}; {grain}; horizon={item.window.start}..{item.window.end}; model={item.model_id}; release={item.model_release_ref}; inventory_as_of={item.inventory_as_of}; as_of={as_of}."
                elif isinstance(item, AnomalyItem):
                    value = number(item.observed_units) + "/" + number(item.expected_units)
                    measurement = f"anomaly:{grain}:{item.window.start}:{item.window.end}"
                    text = f"Anomaly observation={number(item.observed_units)} unit; detector expectation={number(item.expected_units)} unit; {grain}; period={item.window.start}..{item.window.end}; detector={item.detector_version}; release={item.model_release_ref}; as_of={as_of}."
                elif isinstance(item, OperationsItem):
                    value = item.stream_status + ":" + number(item.lag_seconds)
                    measurement = f"operations:{grain}"
                    text = f"Reported stream status={item.stream_status}; lag={number(item.lag_seconds)} seconds; {grain}; as_of={as_of}."
                elif isinstance(item, ModelStatusItem):
                    value = item.deployed_release_ref
                    measurement = f"model:{grain}:{item.deployment_environment}"
                    text = f"Read model reports approved deployed release={item.deployed_release_ref}; model={item.model_id}; environment={item.deployment_environment}; evaluation={item.evaluation_ref}; {grain}; as_of={as_of}. An alias alone is not deployment evidence."
                else:
                    raise InvalidEvidence("unsupported_fact_source")
                claim = EvidenceClaim(
                    claim=text,
                    source_type="tool",
                    source_ref=source,
                    as_of=as_of,
                    supporting_refs=[],
                    calculation_id=None,
                )
                facts.append(Fact(measurement, value, claim.model_dump_json(), (cid,)))
                successful.add(cid)
        if self.request.intent == "sales_comparison":
            facts.extend(self._differences(sales))
        by_measurement: dict[str, set[str]] = {}
        for fact in facts:
            by_measurement.setdefault(fact.measurement, set()).add(fact.value)
        conflicts = {key for key, values in by_measurement.items() if len(values) > 1}
        if conflicts:
            limitations.append(
                "Conflicting values exist for the same measurement; no source is selected as truth."
            )
        facts = [fact for fact in facts if fact.measurement not in conflicts]
        if self.request.intent in {
            "sales",
            "sales_comparison",
            "forecast",
            "investigation",
            "recommendations",
        }:
            limitations.append("Observed sales do not identify uncensored demand.")
        if self.request.intent == "forecast":
            limitations.append(
                "A reported forecast alone does not prove deployment or release approval."
            )
        if self.request.intent == "investigation":
            limitations.append("Cross-signal observations do not establish a causal explanation.")
        if self.request.intent == "recommendations":
            limitations.append(
                "Review candidates do not compute replenishment quantities or authorize orders; the sales forecast does not identify uncensored demand."
            )
        for cid, code in self.failures.items():
            tool = next(call.tool for call in self.calls if call_id(call) == cid)
            limitations.append(f"{tool}: {code}; this source cannot support a current fact.")
        if len(facts) > self.policy.max_catalogue_facts:
            facts = facts[: self.policy.max_catalogue_facts]
            limitations.append("The fact catalogue is truncated by the configured bound.")
        complete = set(required) <= successful
        document_ids = (
            tuple(row.requirement_id for row in self.document_rule.requirements)
            if self.document_rule
            else ()
        )
        if self.request.intent in {"documentation", "verified_state"}:
            covered = {key for fact in facts for key in fact.document_requirement_ids}
            missing = sorted(set(document_ids) - covered)
            if not self.document_rule:
                complete = False
                limitations.append("No document evidence rule matches this question and purpose.")
            elif missing:
                complete = False
                limitations.append(
                    "Required document evidence is missing: " + ", ".join(missing) + "."
                )
        ambiguous_mapping = any(len(locations) > 1 for locations in inventory_locations.values())
        if ambiguous_mapping:
            limitations.append(
                "Multiple stock locations map to the selling grain; an approved aggregation policy is required."
            )
        blocking = bool(conflicts) or not complete or ambiguous_mapping
        if self.request.intent == "sales_comparison" and not any(
            f.claim.source_type == "calculation" for f in facts
        ):
            blocking = True
            limitations.append("Comparable observations for both source periods are required.")
        outcome: Literal["answered", "insufficient_evidence", "refused"] = (
            "refused" if self.refused else "insufficient_evidence" if blocking else "answered"
        )
        freshness = snapshot.freshness().model_dump(mode="json")
        domains = {
            "get_sales_summary": "sales",
            "get_inventory_status": "inventory",
            "get_demand_forecast": "predictions",
            "get_stockout_risk": "predictions",
            "get_detected_anomalies": "predictions",
            "get_model_status": "predictions",
        }
        for call in self.calls:
            domain = domains.get(call.tool)
            if domain and (call_id(call) not in successful or call_id(call) in self.failures):
                # A rejected stale result has no authorized timestamp to expose.
                freshness[domain] = DomainFreshness(as_of=None, status="missing").model_dump(
                    mode="json"
                )
        return Catalogue(
            tuple(facts),
            outcome,
            tuple(dict.fromkeys(limitations))[:10],
            AnswerFreshness.model_validate_json(json.dumps(freshness)).model_dump_json(),
            required,
            blocking,
            tuple(
                candidate.model_dump_json()
                for candidate in candidates(tools, self.policy.suggestions)
            )
            if outcome == "answered"
            else (),
            document_ids,
        )

    def _differences(self, sales: list[tuple[SalesResult, str]]) -> list[Fact]:
        current = [
            (output, cid)
            for output, cid in sales
            if any(item.window == self.request.window for item in output.items)
        ]
        previous = [
            (output, cid)
            for output, cid in sales
            if any(item.window == self.request.comparison_window for item in output.items)
        ]
        facts = []
        for newer, newer_id in current:
            for older, older_id in previous:
                for right in newer.items:
                    for left in older.items:
                        if (
                            right.product_id,
                            right.selling_location_id,
                            right.channel,
                            right.unit_of_measure,
                        ) != (
                            left.product_id,
                            left.selling_location_id,
                            left.channel,
                            left.unit_of_measure,
                        ):
                            continue
                        # 700 digits cover subtraction across the full finite IEEE-754 float range.
                        with localcontext() as context:
                            context.prec = 700
                            delta = Decimal(str(right.observed_sales_units)) - Decimal(
                                str(left.observed_sales_units)
                            )
                        refs = [tool_result_ref(newer), tool_result_ref(older)]
                        grain = f"product={right.product_id}; selling_location={right.selling_location_id}; channel={right.channel}"
                        text = f"Observed sales difference: {number(right.observed_sales_units)} unit - {number(left.observed_sales_units)} unit = {number(delta)} unit; {grain}; current_period={right.window.start}..{right.window.end}; previous_period={left.window.start}..{left.window.end}; source_as_of={newer.as_of},{older.as_of}."
                        ref = "calculation-sha256-" + canonical_sha256(
                            {"formula": "sales-period-difference-v1", "refs": refs, "claim": text}
                        )
                        claim = EvidenceClaim(
                            claim=text,
                            source_type="calculation",
                            source_ref=ref,
                            as_of=newer.as_of,
                            supporting_refs=refs,
                            calculation_id="sales-period-difference-v1",
                        )
                        facts.append(
                            Fact(
                                "difference:" + grain,
                                number(delta),
                                claim.model_dump_json(),
                                (newer_id, older_id),
                            )
                        )
        return facts

    def validate(self, tools: ToolSession, snapshot: EvidenceSnapshot, answer: AnswerDraft) -> None:
        catalogue = self.build(tools, snapshot)
        selected: list[Fact] = []
        for claim in answer.evidence:
            fact = next((fact for fact in catalogue.facts if fact.claim == claim), None)
            if fact is None:
                raise InvalidEvidence("noncanonical_numeric_or_semantic_claim")
            selected.append(fact)
        if len(selected) > self.policy.max_selected_facts or len(
            {fact.fact_id for fact in selected}
        ) != len(selected):
            raise InvalidEvidence("too_many_or_duplicate_facts")
        if catalogue.expected_outcome == "answered":
            covered = {cid for fact in selected for cid in fact.call_ids}
            if not set(catalogue.required_ids) <= covered:
                raise InvalidEvidence("selected_facts_omit_required_evidence")
            document_covered = {key for fact in selected for key in fact.document_requirement_ids}
            if not set(catalogue.required_document_ids) <= document_covered:
                raise InvalidEvidence("selected_facts_omit_document_requirements")
            if self.request.intent == "sales_comparison" and not any(
                f.claim.source_type == "calculation" for f in selected
            ):
                raise InvalidEvidence("comparison_requires_registered_difference")
        expected = catalogue.render(tuple(selected))
        citations = snapshot.citations()
        needed = list(
            {
                claim.source_ref: citations[claim.source_ref]
                for claim in expected.evidence
                if claim.source_type == "document"
            }.values()
        )
        expected = expected.model_copy(update={"citations": needed})
        if answer != expected:
            raise InvalidEvidence("noncanonical_summary_confidence_status_or_limitations")
        calculations = {
            fact.claim.source_ref: fact.claim
            for fact in catalogue.facts
            if fact.claim.source_type == "calculation"
        }
        snapshot.validate_answer(
            answer,
            calculations=calculations,
            expected_freshness=AnswerFreshness.model_validate_json(catalogue.freshness_json),
            authorized_actions=expected.recommended_actions,
        )
