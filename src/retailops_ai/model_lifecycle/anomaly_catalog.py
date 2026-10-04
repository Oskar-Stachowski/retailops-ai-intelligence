"""Safe model metadata from complete scoped anomaly publications, never live aliases."""

import json
from datetime import UTC, datetime

from sqlalchemy import Engine, text

from retailops_ai.anomaly_portfolio.lifecycle_contract import MODEL, Binding, Release
from retailops_ai.anomaly_portfolio.result_store import ReadError, authorized
from retailops_ai.anomaly_portfolio.serving_contract import Query
from retailops_ai.domain.access import Principal
from retailops_ai.model_lifecycle.read_contracts import (
    ApprovedModelRelease,
    CatalogFreshness,
    CatalogModel,
    CatalogPagination,
    CatalogQuery,
    CatalogScope,
    CatalogVersion,
    ModelPage,
    VersionPage,
)
from retailops_ai.model_lifecycle.reader import CatalogError, ModelCatalog
from retailops_ai.source_snapshot.files import json_sha256


def page(
    items: tuple[CatalogModel, ...] | tuple[CatalogVersion, ...],
    query: CatalogQuery,
    actor: Principal,
    *,
    models: bool,
) -> ModelPage | VersionPage:
    stamp = datetime.now(UTC)
    digest = json_sha256(
        {
            "principal": actor.principal_id,
            "scope": query.model_dump(mode="json", exclude={"limit", "offset", "view_sha256"}),
            "items": [
                i.model_dump(mode="json", exclude={"freshness", "generated_at"}) for i in items
            ],
        }
    )
    if query.offset and query.view_sha256 is None:
        raise CatalogError(409, "model-view-required")
    if query.view_sha256 is not None and query.view_sha256 != digest:
        raise CatalogError(409, "model-view-changed")
    end = query.offset + query.limit
    cls = ModelPage if models else VersionPage
    return cls.model_validate_json(
        json.dumps(
            {
                "items": [i.model_dump(mode="json") for i in items[query.offset : end]],
                "pagination": CatalogPagination(
                    limit=query.limit,
                    offset=query.offset,
                    total=len(items),
                    next_offset=end if end < len(items) else None,
                ).model_dump(mode="json"),
                "generated_at": stamp.isoformat(),
                "data_status": "available" if items else "no_data",
                "view_sha256": digest,
            }
        )
    )


class PostgresAnomalyCatalog:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def snapshot(
        self, scope: CatalogScope, actor: Principal
    ) -> tuple[tuple[CatalogVersion, ...], ApprovedModelRelease | None]:
        try:
            authorized(actor, Query(**scope.model_dump()))
        except ReadError as exc:
            raise CatalogError(
                exc.status,
                "model-read-denied"
                if "anomaly:read" not in actor.capabilities
                else "model-scope-invalid",
            ) from None
        params = {
            "products": sorted(actor.product_ids),
            "locations": sorted(actor.selling_location_ids),
            "channels": sorted(actor.channels),
        }
        clauses = [
            "r.product_id=ANY(:products)",
            "r.selling_location_id=ANY(:locations)",
            "r.channel=ANY(:channels)",
        ]
        for key in ("product_id", "selling_location_id", "channel"):
            value = getattr(scope, key)
            if value is not None:
                clauses.append("r." + key + "=:" + key)
                params[key] = value
        with self.engine.connect() as connection:
            records = (
                connection.execute(
                    text(
                        "SELECT v.model_version,v.binding,max(b.created_at) published FROM ai.anomaly_results r "  # noqa: S608 - fixed clauses, bound parameters
                        "JOIN ai.anomaly_batches b USING(batch_id) JOIN ai.anomaly_model_releases l USING(release_id) "
                        "JOIN ai.anomaly_model_versions v ON v.model_name=l.model_name AND v.model_version=l.model_version WHERE "
                        + " AND ".join(clauses)
                        + " GROUP BY v.model_version,v.binding ORDER BY length(v.model_version),v.model_version LIMIT 1001"  # noqa: S608 - fixed clauses, bound parameters
                    ),
                    params,
                )
                .mappings()
                .all()
            )
            active = connection.execute(
                text(
                    "SELECT l.release FROM ai.anomaly_model_heads h JOIN ai.anomaly_model_releases l USING(release_id) WHERE h.model_name=:model"
                ),
                {"model": MODEL},
            ).scalar_one_or_none()
        if len(records) > 1000:
            raise CatalogError(429, "model-read-budget")
        approved = Release.model_validate_json(json.dumps(active)) if active else None
        now = datetime.now(UTC)
        versions = []
        for record in records:
            binding = Binding.model_validate_json(json.dumps(record["binding"]))
            if binding.model_version != record["model_version"]:
                raise ValueError("anomaly_catalog_binding")
            qualification = binding.qualification
            versions.append(
                CatalogVersion(
                    model_name=MODEL,
                    model_version=binding.model_version,
                    status="approved_release_recorded"
                    if approved and approved.binding == binding
                    else "previously_published",
                    model_family=qualification.model_family,
                    flavor="anomaly-json-v1",
                    mlflow_run_id=binding.mlflow_run_id,
                    model_artifact=qualification.model,
                    model_card_report=qualification.gates["model_card"].report,
                    qualification_sha256=binding.qualification_sha256,
                    config_sha256=qualification.config.sha256,
                    feature_schema_version=qualification.feature_schema_version,
                    evaluation_id=qualification.evaluation_id,
                    visible_last_published_at=record["published"],
                    freshness=CatalogFreshness(evaluated_at=now),
                )
            )
        visible = {v.model_version for v in versions}
        release = (
            ApprovedModelRelease(
                release_id=approved.release_id,
                model_version=approved.binding.model_version,
                image_digest=approved.image_digest,
            )
            if approved and approved.binding.model_version in visible
            else None
        )
        return tuple(versions), release

    def model(self, name: str, scope: CatalogScope, actor: Principal) -> CatalogModel:
        if name != MODEL:
            raise CatalogError(404, "model-not-found")
        versions, release = self.snapshot(scope, actor)
        if not versions:
            raise CatalogError(404, "model-not-found")
        return self.summary(versions, release)

    @staticmethod
    def summary(
        versions: tuple[CatalogVersion, ...], release: ApprovedModelRelease | None
    ) -> CatalogModel:
        now = datetime.now(UTC)
        return CatalogModel(
            model_name=MODEL,
            visible_version_count=len(versions),
            approved_release=release,
            freshness=CatalogFreshness(evaluated_at=now),
            generated_at=now,
        )

    def models(self, query: CatalogQuery, actor: Principal) -> ModelPage:
        versions, release = self.snapshot(query, actor)
        result = page(
            (self.summary(versions, release),) if versions else (), query, actor, models=True
        )
        assert isinstance(result, ModelPage)  # noqa: S101 - fixed projection branch
        return result

    def versions(self, name: str, query: CatalogQuery, actor: Principal) -> VersionPage:
        if name != MODEL:
            raise CatalogError(404, "model-not-found")
        versions, _ = self.snapshot(query, actor)
        if not versions:
            raise CatalogError(404, "model-not-found")
        result = page(versions, query, actor, models=False)
        assert isinstance(result, VersionPage)  # noqa: S101 - fixed projection branch
        return result


class CombinedCatalog:
    def __init__(self, forecast: ModelCatalog | None, anomaly: ModelCatalog) -> None:
        self.forecast, self.anomaly = forecast, anomaly

    def models(self, query: CatalogQuery, actor: Principal) -> ModelPage:
        full = CatalogQuery(
            **query.model_dump(exclude={"limit", "offset", "view_sha256"}), limit=200
        )
        items: list[CatalogModel] = []
        if "forecast:read" in actor.capabilities:
            if self.forecast is None:
                raise ValueError("forecast_catalog_unavailable")
            items.extend(self.forecast.models(full, actor).items)
        if "anomaly:read" in actor.capabilities:
            items.extend(self.anomaly.models(full, actor).items)
        if not {"forecast:read", "anomaly:read"} & actor.capabilities:
            raise CatalogError(403, "model-read-denied")
        result = page(tuple(sorted(items, key=lambda i: i.model_name)), query, actor, models=True)
        assert isinstance(result, ModelPage)  # noqa: S101 - fixed projection branch
        return result

    def backend(self, name: str, actor: Principal) -> ModelCatalog:
        if name == MODEL:
            if "anomaly:read" not in actor.capabilities:
                raise CatalogError(403, "model-read-denied")
            return self.anomaly
        if name == "retailops-demand-forecast":
            if "forecast:read" not in actor.capabilities:
                raise CatalogError(403, "model-read-denied")
            if self.forecast is None:
                raise ValueError("forecast_catalog_unavailable")
            return self.forecast
        raise CatalogError(404, "model-not-found")

    def model(self, name: str, scope: CatalogScope, actor: Principal) -> CatalogModel:
        return self.backend(name, actor).model(name, scope, actor)

    def versions(self, name: str, query: CatalogQuery, actor: Principal) -> VersionPage:
        return self.backend(name, actor).versions(name, query, actor)
