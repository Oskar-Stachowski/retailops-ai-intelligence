"""Exact document routes and source IDs; no model may choose scope or tools here."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal

from retailops_ai.agent.document_evidence import DocumentEvidenceRule, question_key
from retailops_ai.agent.evidence import DENIED_QUESTION
from retailops_ai.agent.graph_contracts import GraphRequest, Intent
from retailops_ai.assistant.contracts import AssistantQuery
from retailops_ai.assistant.service import AssistantError, authorized
from retailops_ai.assistant.source_catalog import ChannelAssignment, SourceCatalog
from retailops_ai.data_contracts.common import DateWindow
from retailops_ai.domain.access import Principal


class DocumentPlanner:
    def __init__(
        self,
        rules: tuple[DocumentEvidenceRule, ...],
        catalog: SourceCatalog,
        channel: Literal["store", "online"],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.routes = {question_key(row.question): row.intent for row in rules}
        if not rules or len(self.routes) != len(rules):
            raise ValueError("document_routes_missing_or_ambiguous")
        self.catalog, self.channel, self.clock = catalog, channel, clock

    async def prepare(self, query: AssistantQuery, principal: Principal) -> GraphRequest:
        if not authorized(principal, query) or self.channel not in principal.channels:
            raise AssistantError(403)
        now = self.clock()
        intent: Intent | None = self.routes.get(question_key(query.question))
        if DENIED_QUESTION.search(query.question):
            intent = "refuse"
        if intent is None:
            raise AssistantError(422)
        if intent != "refuse" and "knowledge:read" not in principal.capabilities:
            raise AssistantError(403)
        if query.scope.to > now.date():
            raise AssistantError(422)
        validate_source_scope(
            query,
            self.catalog,
            self.channel,
            now,
            (DateWindow(start=query.scope.from_, end=query.scope.to),),
        )
        return GraphRequest.model_validate(
            {
                "schema_version": "1.0",
                "question": query.question,
                "intent": intent,
                "scope": {
                    "product_ids": [str(value) for value in query.scope.product_ids],
                    "selling_location_ids": [str(value) for value in query.scope.store_ids],
                    "channel": self.channel,
                },
                "as_of": now,
                "window": {"start": query.scope.from_, "end": query.scope.to},
                "comparison_window": None,
                "limit": 5,
            }
        )


def validate_source_scope(
    query: AssistantQuery,
    catalog: SourceCatalog,
    channel: Literal["store", "online"],
    now: datetime,
    windows: tuple[DateWindow, ...],
) -> None:
    products = {str(value) for value in query.scope.product_ids}
    stores = {str(value) for value in query.scope.store_ids}
    available = {row.product_id for row in catalog.products if row.available_at <= now}
    if not products <= available or not stores <= set(catalog.selling_locations):
        raise AssistantError(422)
    # Exact source IDs, not legacy store aliases. Each day must have one unambiguous
    # latest available assignment for the configured channel, using half-open dates.
    for window in windows:
        for store in stores:
            day = window.start
            while day <= window.end:
                latest: dict[str, list[ChannelAssignment]] = {}
                for row in catalog.assignments:
                    if (
                        row.selling_location_id == store
                        and row.channel == channel
                        and row.available_at <= now
                        and row.effective_from <= day < row.effective_to
                    ):
                        previous = latest.get(row.assignment_key, [])
                        if not previous or row.version > previous[0].version:
                            latest[row.assignment_key] = [row]
                        elif row.version == previous[0].version:
                            latest[row.assignment_key].append(row)
                if sum(len(rows) for rows in latest.values()) != 1:
                    raise AssistantError(422)
                day += timedelta(days=1)
