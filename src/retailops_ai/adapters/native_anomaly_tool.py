"""Causal complete anomaly batches, verified privately before scope projection."""

import asyncio
import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol

from sqlalchemy import Engine, text

from retailops_ai.adapters.native_read_tools import environment, scoped_actor
from retailops_ai.agent.execution import ToolFailure
from retailops_ai.agent.tools import AnomalyRequest, AnomalyResult, NativeAnomalyEvidence, ToolInput
from retailops_ai.anomaly_detectors.protocol import Scope, Window, series_key
from retailops_ai.anomaly_evaluation.contract import Decision, dates
from retailops_ai.anomaly_portfolio.lifecycle_contract import Release
from retailops_ai.anomaly_portfolio.result_store import logical
from retailops_ai.anomaly_portfolio.serving_contract import Item, Page, Pagination
from retailops_ai.domain.access import Principal
from retailops_ai.knowledge.releases import IndexPin
from retailops_ai.source_snapshot.files import canonical_json, json_sha256

MAX_BYTES = 16 * 1024**2
MAX_ROWS = 10000


def verify_publication(manifest: dict[str, Any], release: Release, items: list[Item]) -> None:
    """Recheck the native publisher's complete census and immutable release pins."""
    desc, batch_id = manifest["descriptor"], manifest["batch_id"]
    if not 1 <= len(desc["scopes"]) <= MAX_ROWS:
        raise ValueError("native_anomaly_census_budget")
    scopes = [Scope.model_validate_json(canonical_json(s)) for s in desc["scopes"]]
    window = Window.model_validate_json(canonical_json(desc["window"]))
    if (
        not 1 <= len(items) <= MAX_ROWS
        or len(scopes) * ((window.end - window.start).days + 1) > MAX_ROWS
    ):
        raise ValueError("native_anomaly_census_budget")
    required = {(*series_key(s), d) for s in scopes for d in dates(window)}
    decisions = [
        {k: v for k, v in i.model_dump(mode="json").items() if k in Decision.model_fields}
        for i in items
    ]
    raw = b"".join(canonical_json(i.model_dump(mode="json")) + b"\n" for i in items)
    if (
        batch_id != "anomaly-batch-sha256-" + json_sha256(desc)
        or manifest["rows_sha256"] != hashlib.sha256(raw).hexdigest()
        or desc["row_count"] != len(items)
        or len(required) != len(items)
        or len({i.anomaly_id for i in items}) != len(items)
        or {(*series_key(i), i.business_date) for i in items} != required
        or json_sha256(decisions) != desc["decisions_sha256"]
        or desc["release_id"] != release.release_id
        or desc["model_version"] != release.binding.model_version
        or desc["model_sha256"] != release.binding.qualification.model.sha256
        or desc["source_dataset_id"] != release.binding.qualification.source_dataset_id
        or desc["feature_id"] != release.binding.qualification.qualified_anomaly_input_id
        or any(g.status != "passed" for g in release.binding.qualification.gates.values())
    ):
        raise ValueError("native_anomaly_publication_binding")
    for i, d in zip(items, decisions, strict=True):
        if (
            i.batch_id != batch_id
            or i.release_id != release.release_id
            or i.detector_version != release.binding.model_version
            or i.source_dataset_id != desc["source_dataset_id"]
            or i.qualified_anomaly_input_id != desc["feature_id"]
            or i.family != release.binding.qualification.model_family
            or i.as_of.isoformat() != desc["as_of"]
            or i.role != "batch"
            or i.anomaly_id
            != "anomaly-sha256-" + json_sha256({"batch_id": batch_id, "decision": d})
        ):
            raise ValueError("native_anomaly_row_binding")


class NativeAnomalyReader(Protocol):
    environment: Literal["local", "test"]

    def read(self, request: AnomalyRequest, principal: Principal) -> Page: ...


class PostgresNativeAnomalyReader:
    def __init__(self, engine: Engine, env: Literal["local", "test"]) -> None:
        self.environment, self.engine = environment(env), engine

    def read(self, request: AnomalyRequest, principal: Principal) -> Page:
        actor = scoped_actor(request, principal, {"anomalies:read", "anomaly:read"})
        scope = request.scope
        if scope is None:
            raise ToolFailure("invalid_scope")
        count = (
            len(scope.product_ids)
            * len(scope.selling_location_ids)
            * ((request.window.end - request.window.start).days + 1)
        )
        if count > request.limit:
            raise ToolFailure("budget_exceeded")
        params = dict(
            products=sorted(actor.product_ids),
            locations=sorted(actor.selling_location_ids),
            channel=scope.channel,
            first=request.window.start,
            last=request.window.end,
            cutoff=request.as_of,
            maximum=MAX_BYTES,
        )
        with self.engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
            with conn.begin():
                conn.execute(text("SET TRANSACTION READ ONLY"))
                conn.execute(text("SET LOCAL statement_timeout='3s'"))
                now: datetime = conn.execute(text("SELECT clock_timestamp()")).scalar_one()
                chosen = conn.execute(
                    text("""
                    SELECT b.batch_id FROM ai.anomaly_batches b
                    WHERE b.as_of <= :cutoff AND EXISTS (
                      SELECT 1 FROM ai.anomaly_results r WHERE r.batch_id=b.batch_id
                      AND r.product_id=ANY(:products) AND r.selling_location_id=ANY(:locations)
                      AND r.channel=:channel AND r.event_type='sale_completed'
                      AND r.business_date BETWEEN :first AND :last)
                    ORDER BY b.as_of DESC,b.batch_id DESC LIMIT 1
                """),
                    params,
                ).scalar_one_or_none()
                items: list[Item] = []
                if chosen is not None:
                    header = (
                        conn.execute(
                            text("""
                        SELECT CASE WHEN octet_length(b.manifest::text)<=:maximum THEN b.manifest END manifest,
                          CASE WHEN octet_length(l.release::text)<=:maximum THEN l.release END release,
                          CASE WHEN octet_length(v.binding::text)<=:maximum THEN v.binding END binding,
                          EXISTS(SELECT 1 FROM ai.anomaly_model_steps s
                            WHERE s.decision_id=l.decision_id AND s.phase='completed') release_complete,
                          EXISTS(SELECT 1 FROM ai.anomaly_model_steps s
                            WHERE s.decision_id=v.decision_id AND s.phase='completed') enrollment_complete
                        FROM ai.anomaly_batches b JOIN ai.anomaly_model_releases l USING(release_id)
                        JOIN ai.anomaly_model_versions v ON v.model_name=l.model_name AND v.model_version=l.model_version
                        WHERE b.batch_id=:id
                    """),
                            {"id": chosen, "maximum": MAX_BYTES},
                        )
                        .mappings()
                        .one()
                    )
                    census = (
                        conn.execute(
                            text(
                                "SELECT count(*) row_count, coalesce(sum(octet_length(record::text)),0) bytes FROM ai.anomaly_results WHERE batch_id=:id"
                            ),
                            {"id": chosen},
                        )
                        .mappings()
                        .one()
                    )
                    if (
                        census["row_count"] > MAX_ROWS
                        or census["bytes"] + len(canonical_json(dict(header))) > MAX_BYTES
                    ):
                        raise ToolFailure("budget_exceeded")
                    values = list(
                        conn.execute(
                            text("""
                        SELECT CASE WHEN octet_length(record::text)<=:maximum THEN record END
                        FROM ai.anomaly_results WHERE batch_id=:id ORDER BY position LIMIT 10001
                    """),
                            {"id": chosen, "maximum": MAX_BYTES},
                        ).scalars()
                    )
                    if (
                        len(values) > MAX_ROWS
                        or len(canonical_json(values)) + len(canonical_json(dict(header)))
                        > MAX_BYTES
                    ):
                        raise ToolFailure("budget_exceeded")
                    release = Release.model_validate_json(json.dumps(header["release"]))
                    if header["manifest"]["batch_id"] != chosen:
                        raise ValueError("native_anomaly_selected_batch_mismatch")
                    if (
                        not header["release_complete"]
                        or not header["enrollment_complete"]
                        or header["binding"] != release.binding.model_dump(mode="json")
                    ):
                        raise ValueError("native_anomaly_release_not_enrolled")
                    stored = [Item.model_validate_json(json.dumps(v)) for v in values]
                    verify_publication(header["manifest"], release, stored)
                    if any(i.generated_at > now for i in stored):
                        raise ValueError("native_anomaly_future_publication")
                    for i in stored:
                        if (
                            i.product_id in actor.product_ids
                            and i.selling_location_id in actor.selling_location_ids
                            and i.channel == scope.channel
                            and i.event_type == "sale_completed"
                            and request.window.start <= i.business_date <= request.window.end
                        ):
                            state = (
                                "unknown"
                                if i.status == "insufficient_data"
                                else "stale"
                                if now - i.scoring_origin > timedelta(days=7)
                                else "current"
                            )
                            items.append(
                                Item.model_validate_json(
                                    i.model_copy(
                                        update={"freshness_status": state}
                                    ).model_dump_json()
                                )
                            )
        items.sort(
            key=lambda i: (
                i.product_id,
                i.selling_location_id,
                i.channel,
                i.business_date,
                i.currency,
            )
        )
        if len(items) > request.limit:
            raise ToolFailure("budget_exceeded")
        return Page(
            items=tuple(items),
            pagination=Pagination(limit=request.limit, offset=0, total=len(items)),
            generated_at=now,
            selection="latest_complete_batch",
            data_status="available" if items else "no_data",
            view_sha256=json_sha256(
                {
                    "request": request.model_dump(mode="json"),
                    "principal": actor.principal_id,
                    "rows": logical(items),
                }
            ),
        )


class NativeAnomalyTool:
    source_kind: Literal["runtime"] = "runtime"

    def __init__(self, reader: NativeAnomalyReader, env: Literal["local", "test"]) -> None:
        self.environment = environment(env)
        if reader.environment != env:
            raise ValueError("native_anomaly_environment_invalid")
        self.reader = reader

    async def execute(
        self, request: ToolInput, principal: Principal, pin: IndexPin | None
    ) -> AnomalyResult:
        if not isinstance(request, AnomalyRequest):
            raise ToolFailure("invalid_scope")
        request = AnomalyRequest.model_validate_json(request.model_dump_json())
        actor = scoped_actor(request, principal, {"anomalies:read", "anomaly:read"})
        if self.reader.environment != self.environment:
            raise ToolFailure("unavailable")
        if (
            len(actor.product_ids)
            * len(actor.selling_location_ids)
            * ((request.window.end - request.window.start).days + 1)
            > request.limit
        ):
            raise ToolFailure("budget_exceeded")
        try:
            page = await asyncio.to_thread(self.reader.read, request, actor)
            page = Page.model_validate_json(page.model_dump_json())
            proof = NativeAnomalyEvidence(environment=self.environment, request=request, page=page)
            return AnomalyResult(
                schema_version="1.0",
                contract_type="agent_tool_result",
                tool=request.tool,
                status="ok" if proof.complete else "no_data",
                as_of=request.as_of,
                freshness_status="current" if proof.complete else "missing",
                source_ref=proof.view_ref,
                items=[*proof.result_items()],
                error=None,
                source_kind="runtime",
                native_view=proof,
            )
        except ToolFailure:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ToolFailure("unavailable") from None
