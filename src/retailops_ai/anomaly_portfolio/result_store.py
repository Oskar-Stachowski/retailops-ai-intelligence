"""Transactional PostgreSQL publication and authorized reads of complete batches."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import Engine, text

from retailops_ai.anomaly_detectors.protocol import Scope, Window, series_key
from retailops_ai.anomaly_evaluation.contract import Decision, dates
from retailops_ai.anomaly_portfolio.lifecycle_contract import Release
from retailops_ai.anomaly_portfolio.serving_contract import ErrorCode, Item, Page, Pagination, Query
from retailops_ai.domain.access import Principal
from retailops_ai.source_snapshot.files import canonical_json, json_sha256


class ReadError(ValueError):
    def __init__(self, status: int, code: ErrorCode) -> None:
        self.status, self.code = status, code
        super().__init__(code)


class Reader(Protocol):
    def read(self, query: Query, actor: Principal) -> Page: ...


def authorized(actor: Principal, query: Query, capability: str = "anomaly:read") -> None:
    if (
        capability not in actor.capabilities
        or not actor.product_ids
        or not actor.selling_location_ids
        or not actor.channels
        or (query.product_id is not None and query.product_id not in actor.product_ids)
        or (
            query.selling_location_id is not None
            and query.selling_location_id not in actor.selling_location_ids
        )
        or (query.channel is not None and query.channel not in actor.channels)
    ):
        raise ReadError(403, "anomaly-scope-denied")


def logical(items: list[Item]) -> list[dict[str, Any]]:
    return [
        i.model_dump(mode="json", exclude={"generated_at", "detected_at", "freshness_status"})
        for i in items
    ]


class PostgresResults:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def publish(
        self,
        request_id: str,
        actor: Principal,
        release: Release,
        manifest: dict[str, Any],
        items: list[Item],
    ) -> dict[str, Any]:
        release = Release.model_validate_json(release.model_dump_json())
        items = [Item.model_validate_json(i.model_dump_json()) for i in items]
        desc = manifest["descriptor"]
        batch_id = manifest["batch_id"]
        if "pipeline" not in actor.roles or "anomaly:run" not in actor.capabilities:
            raise ReadError(403, "anomaly-pipeline-denied")
        if not 1 <= len(request_id) <= 128 or not 1 <= len(items) <= 10000:
            raise ValueError("anomaly_publication_request_or_census_budget")
        raw = b"".join(canonical_json(i.model_dump(mode="json")) + b"\n" for i in items)
        scopes = [Scope.model_validate_json(canonical_json(s)) for s in desc["scopes"]]
        window = Window.model_validate_json(canonical_json(desc["window"]))
        if len(scopes) * len(dates(window)) > 10000:
            raise ValueError("anomaly_publication_census_budget")
        required = {(*series_key(s), day) for s in scopes for day in dates(window)}
        decisions = [
            {k: v for k, v in i.model_dump(mode="json").items() if k in Decision.model_fields}
            for i in items
        ]
        if (
            batch_id != "anomaly-batch-sha256-" + json_sha256(desc)
            or manifest["rows_sha256"] != hashlib.sha256(raw).hexdigest()
            or desc["row_count"] != len(items)
            or len({i.anomaly_id for i in items}) != len(items)
            or len(required) != len(items)
            or {(*series_key(i), i.business_date) for i in items} != required
            or json_sha256(decisions) != desc["decisions_sha256"]
            or desc["release_id"] != release.release_id
            or desc["model_version"] != release.binding.model_version
            or desc["model_sha256"] != release.binding.qualification.model.sha256
            or any(g.status != "passed" for g in release.binding.qualification.gates.values())
        ):
            raise ValueError("anomaly_publication_manifest_binding")
        for item in items:
            authorized(
                actor,
                Query(
                    product_id=item.product_id,
                    selling_location_id=item.selling_location_id,
                    channel=item.channel,
                ),
                "anomaly:run",
            )
            if (
                item.batch_id != batch_id
                or item.release_id != release.release_id
                or item.detector_version != release.binding.model_version
                or item.source_dataset_id != desc["source_dataset_id"]
                or item.qualified_anomaly_input_id != desc["feature_id"]
                or item.family != release.binding.qualification.model_family
                or item.as_of.isoformat() != desc["as_of"]
                or item.role != "batch"
                or item.anomaly_id
                != "anomaly-sha256-"
                + json_sha256(
                    {
                        "batch_id": batch_id,
                        "decision": {
                            k: v
                            for k, v in item.model_dump(mode="json").items()
                            if k in Decision.model_fields
                        },
                    }
                )
            ):
                raise ValueError("anomaly_publication_row_binding")
        digest = json_sha256({"descriptor": desc, "principal": actor.principal_id})
        lock = int.from_bytes(
            hashlib.sha256((actor.principal_id + ":" + request_id).encode()).digest()[:8],
            signed=True,
        )
        with self.engine.begin() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
            existing = (
                conn.execute(
                    text(
                        "SELECT request_sha256,batch_id FROM ai.anomaly_requests WHERE principal_id=:principal AND request_id=:request"
                    ),
                    {"principal": actor.principal_id, "request": request_id},
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                if dict(existing) != {"request_sha256": digest, "batch_id": batch_id}:
                    raise ReadError(409, "anomaly-idempotency-conflict")
                return {"status": "replayed", "batch_id": batch_id}
            enrolled = conn.execute(
                text("SELECT release FROM ai.anomaly_model_releases WHERE release_id=:id"),
                {"id": release.release_id},
            ).scalar_one_or_none()
            if enrolled != release.model_dump(mode="json"):
                raise ValueError("anomaly_publication_release_not_enrolled")
            # Serialize identical inputs across different request keys as well.
            conn.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {
                    "key": int.from_bytes(
                        hashlib.sha256(batch_id.encode()).digest()[:8], signed=True
                    )
                },
            )
            previous = conn.execute(
                text("SELECT manifest FROM ai.anomaly_batches WHERE batch_id=:id"), {"id": batch_id}
            ).scalar_one_or_none()
            if previous is not None:
                stored = [
                    Item.model_validate_json(json.dumps(v))
                    for v in conn.execute(
                        text(
                            "SELECT record FROM ai.anomaly_results WHERE batch_id=:id ORDER BY position"
                        ),
                        {"id": batch_id},
                    ).scalars()
                ]
                if previous["descriptor"] != desc or logical(stored) != logical(items):
                    raise ValueError("anomaly_publication_immutable_conflict")
            else:
                conn.execute(
                    text(
                        "INSERT INTO ai.anomaly_batches(batch_id,release_id,as_of,row_count,manifest) VALUES(:id,:release,:as_of,:count,CAST(:manifest AS jsonb))"
                    ),
                    {
                        "id": batch_id,
                        "release": release.release_id,
                        "as_of": items[0].as_of,
                        "count": len(items),
                        "manifest": json.dumps(manifest),
                    },
                )
                conn.execute(
                    text(
                        "INSERT INTO ai.anomaly_results(anomaly_id,batch_id,position,event_type,product_id,selling_location_id,channel,currency,business_date,scoring_origin,record) VALUES(:id,:batch,:position,:event,:product,:location,:channel,:currency,:day,:origin,CAST(:record AS jsonb))"
                    ),
                    [
                        {
                            "id": i.anomaly_id,
                            "batch": batch_id,
                            "position": n,
                            "event": i.event_type,
                            "product": i.product_id,
                            "location": i.selling_location_id,
                            "channel": i.channel,
                            "currency": i.currency,
                            "day": i.business_date,
                            "origin": i.scoring_origin,
                            "record": i.model_dump_json(),
                        }
                        for n, i in enumerate(items)
                    ],
                )
            conn.execute(
                text(
                    "INSERT INTO ai.anomaly_requests(principal_id,request_id,request_sha256,batch_id) VALUES(:principal,:request,:sha,:id)"
                ),
                {
                    "principal": actor.principal_id,
                    "request": request_id,
                    "sha": digest,
                    "id": batch_id,
                },
            )
            return {
                "status": "reused" if previous is not None else "published",
                "batch_id": batch_id,
                "row_count": len(items),
            }

    def read(self, query: Query, actor: Principal) -> Page:
        query = Query.model_validate_json(query.model_dump_json())
        authorized(actor, query)
        clauses = [
            "r.product_id=ANY(:products)",
            "r.selling_location_id=ANY(:locations)",
            "r.channel=ANY(:channels)",
        ]
        params: dict[str, Any] = {
            "products": sorted(actor.product_ids),
            "locations": sorted(actor.selling_location_ids),
            "channels": sorted(actor.channels),
        }
        for field in (
            "product_id",
            "selling_location_id",
            "channel",
            "event_type",
            "batch_id",
            "anomaly_id",
        ):
            value = getattr(query, field)
            if value is not None:
                clauses.append("r." + field + "=:" + field)
                params[field] = value
        if query.business_from is not None:
            clauses.append("r.business_date BETWEEN :first AND :last")
            params.update(first=query.business_from, last=query.business_to)
        if query.status is not None:
            clauses.append("r.record->>'status'=:status")
            params["status"] = query.status
        if query.severity is not None:
            clauses.append("r.record->>'severity'=:severity")
            params["severity"] = query.severity
        if query.anomaly_type is not None:
            clauses.append("r.record->>'anomaly_type'=:anomaly_type")
            params["anomaly_type"] = query.anomaly_type
        where = " AND ".join(clauses)
        with self.engine.connect() as conn:
            if query.batch_id is None and query.anomaly_id is None:
                chosen = conn.execute(
                    text(
                        "SELECT r.batch_id FROM ai.anomaly_results r JOIN ai.anomaly_batches b USING(batch_id) WHERE "  # noqa: S608 - fixed allowlisted clauses; every value is bound
                        + where
                        + " ORDER BY b.as_of DESC,r.batch_id DESC LIMIT 1"
                    ),
                    params,
                ).scalar_one_or_none()
                if chosen is not None:
                    where += " AND r.batch_id=:chosen"
                    params["chosen"] = chosen
            values: list[dict[str, Any]] = list(
                conn.execute(
                    text(
                        "SELECT r.record FROM ai.anomaly_results r WHERE "  # noqa: S608 - fixed allowlisted clauses; every value is bound
                        + where
                        + " ORDER BY r.event_type,r.product_id,r.selling_location_id,r.channel,r.currency,r.business_date,r.anomaly_id LIMIT 10001"
                    ),
                    params,
                ).scalars()
            )
        if len(values) > 10000:
            raise ReadError(429, "anomaly-read-budget")
        now = datetime.now(UTC)
        items = []
        for value in values:
            item = Item.model_validate_json(json.dumps(value))
            freshness = (
                "unknown"
                if item.status == "insufficient_data"
                else "stale"
                if now - item.scoring_origin > timedelta(days=7)
                else "current"
            )
            items.append(
                Item.model_validate_json(
                    item.model_copy(update={"freshness_status": freshness}).model_dump_json()
                )
            )
        digest = json_sha256(
            {
                "query": query.model_dump(mode="json", exclude={"limit", "offset", "view_sha256"}),
                "principal": actor.principal_id,
                "rows": logical(items),
            }
        )
        if query.view_sha256 is not None and query.view_sha256 != digest:
            raise ReadError(409, "anomaly-view-changed")
        if query.anomaly_id is not None and not items:
            raise ReadError(404, "anomaly-not-found")
        return Page(
            items=tuple(items[query.offset : query.offset + query.limit]),
            pagination=Pagination(limit=query.limit, offset=query.offset, total=len(items)),
            generated_at=now,
            view_sha256=digest,
            selection="anomaly_id"
            if query.anomaly_id
            else "pinned_batch"
            if query.batch_id
            else "latest_complete_batch",
            data_status="available" if items else "no_data",
        )
