"""Read-only scoped catalog from validated publication and enrollment metadata."""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from sqlalchemy import Engine, text

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs.publication import OutputManifest
from retailops_ai.forecast_jobs.queue import checked, record
from retailops_ai.forecast_jobs.read_contracts import ForecastQuery
from retailops_ai.forecast_jobs.reader import (
    ForecastReadError,
    ReadScope,
    resolve_scope,
    verify_manifest,
)
from retailops_ai.model_lifecycle.contracts import MODEL, Binding, Release
from retailops_ai.model_lifecycle.read_contracts import (
    ApprovedModelRelease,
    CatalogErrorCode,
    CatalogFreshness,
    CatalogModel,
    CatalogPagination,
    CatalogQuery,
    CatalogScope,
    CatalogVersion,
    ModelPage,
    VersionPage,
)

MAX_VERSIONS = 1000
MAX_METADATA_BYTES = 64 * 1024


class CatalogError(ValueError):
    def __init__(self, status: int, code: CatalogErrorCode) -> None:
        super().__init__(code)
        self.status, self.code = status, code


class ModelCatalog(Protocol):
    def models(self, query: CatalogQuery, actor: Principal) -> ModelPage: ...
    def model(self, name: str, scope: CatalogScope, actor: Principal) -> CatalogModel: ...
    def versions(self, name: str, query: CatalogQuery, actor: Principal) -> VersionPage: ...


def authorized(scope: CatalogScope, actor: Principal) -> ReadScope:
    try:
        return resolve_scope(
            ForecastQuery(
                product_id=scope.product_id,
                selling_location_id=scope.selling_location_id,
                channel=scope.channel,
            ),
            actor,
        )
    except ForecastReadError as exc:
        codes: dict[str, CatalogErrorCode] = {
            "forecast-read-denied": "model-read-denied",
            "forecast-scope-invalid": "model-scope-invalid",
            "forecast-scope-limit": "model-scope-limit",
        }
        raise CatalogError(exc.status, codes[exc.code]) from None


@dataclass(frozen=True)
class CatalogSnapshot:
    versions: tuple[CatalogVersion, ...]
    approved_release: ApprovedModelRelease | None
    generated_at: datetime

    def summary(self) -> CatalogModel:
        if not self.versions:
            raise CatalogError(404, "model-not-found")
        return CatalogModel(
            visible_version_count=len(self.versions),
            approved_release=self.approved_release,
            freshness=CatalogFreshness(evaluated_at=self.generated_at),
            generated_at=self.generated_at,
        )


def page_snapshot(
    snapshot: CatalogSnapshot, query: CatalogQuery, actor: Principal, *, models: bool
) -> ModelPage | VersionPage:
    scope = authorized(query, actor)
    items = (
        (snapshot.summary(),)
        if models and snapshot.versions
        else ()
        if models
        else snapshot.versions
    )
    view = canonical_sha256(
        {
            "projection": "model-catalog-v1",
            "list": "models" if models else "versions",
            "principal_id": actor.principal_id,
            "scope": {
                "products": scope.products,
                "locations": scope.locations,
                "channels": scope.channels,
            },
            "items": [
                v.model_dump(mode="json", exclude={"freshness", "generated_at"}) for v in items
            ],
        }
    )
    if query.offset and query.view_sha256 is None:
        raise CatalogError(409, "model-view-required")
    if query.view_sha256 is not None and query.view_sha256 != view:
        raise CatalogError(409, "model-view-changed")
    end = query.offset + query.limit
    pagination = CatalogPagination(
        limit=query.limit,
        offset=query.offset,
        total=len(items),
        next_offset=end if end < len(items) else None,
    )
    cls = ModelPage if models else VersionPage
    return cls.model_validate_json(
        json.dumps(
            dict(
                items=[v.model_dump(mode="json") for v in items[query.offset : end]],
                pagination=pagination.model_dump(mode="json"),
                generated_at=snapshot.generated_at.isoformat(),
                data_status="available" if items else "no_data",
                view_sha256=view,
            )
        )
    )


class PostgresModelCatalog:
    def __init__(self, engine: Engine, environment: Literal["local", "test"]) -> None:
        self.engine, self.environment = engine, environment

    def models(self, query: CatalogQuery, actor: Principal) -> ModelPage:
        result = page_snapshot(self._snapshot(query, actor), query, actor, models=True)
        if not isinstance(result, ModelPage):
            raise ValueError("model_catalog_page_type")
        return result

    def model(self, name: str, scope: CatalogScope, actor: Principal) -> CatalogModel:
        authorized(scope, actor)
        if name != MODEL:
            raise CatalogError(404, "model-not-found")
        return self._snapshot(scope, actor).summary()

    def versions(self, name: str, query: CatalogQuery, actor: Principal) -> VersionPage:
        authorized(query, actor)
        if name != MODEL:
            raise CatalogError(404, "model-not-found")
        snapshot = self._snapshot(query, actor)
        if not snapshot.versions:
            raise CatalogError(404, "model-not-found")
        result = page_snapshot(snapshot, query, actor, models=False)
        if not isinstance(result, VersionPage):
            raise ValueError("model_catalog_page_type")
        return result

    def _snapshot(self, request: CatalogScope, actor: Principal) -> CatalogSnapshot:
        request = CatalogScope.model_validate_json(
            request.model_dump_json(include=set(CatalogScope.model_fields))
        )
        scope = authorized(request, actor)
        params = {
            "env": self.environment,
            "model": MODEL,
            "products": list(scope.products),
            "locations": list(scope.locations),
            "channels": list(scope.channels),
            "cap": MAX_VERSIONS + 1,
            "bytes": MAX_METADATA_BYTES,
        }
        with self.engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
            with conn.begin():
                conn.execute(text("SET TRANSACTION READ ONLY"))
                now = checked(conn)
                conn.execute(text("SET LOCAL statement_timeout='3s'"))
                rows = conn.execute(
                    text("""
                    WITH visible AS (
                        SELECT DISTINCT ON (m.manifest->'resolved_model'->>'model_version')
                               m.manifest, r.record,
                               m.manifest->'resolved_model'->>'model_version' model_version
                        FROM ai.forecast_output_manifests m JOIN ai.forecast_batch_runs r ON r.run_id=m.run_id
                        WHERE m.environment=:env AND m.manifest->'resolved_model'->>'model_name'=:model
                          AND m.manifest->'scope'->>'channel'=ANY(:channels)
                          AND (m.manifest->'scope'->'product_ids') ?| CAST(:products AS text[])
                          AND (m.manifest->'scope'->'selling_location_ids') ?| CAST(:locations AS text[])
                        ORDER BY model_version,(m.manifest->>'generated_at')::timestamptz DESC,m.run_id DESC
                    )
                    SELECT visible.*, v.model_name enrolled_name, v.model_version enrolled_version,
                           CASE WHEN octet_length(v.binding::text)<=:bytes THEN v.binding END binding,
                           EXISTS(SELECT 1 FROM ai.model_steps s WHERE s.decision_id=v.decision_id AND s.phase='completed') enrollment_complete
                    FROM visible LEFT JOIN ai.model_versions v ON v.model_name=:model AND v.model_version=visible.model_version
                    ORDER BY visible.model_version::bigint LIMIT :cap
                """),
                    params,
                ).all()
                if len(rows) > MAX_VERSIONS:
                    raise CatalogError(429, "model-read-budget")
                bindings: dict[str, Binding] = {}
                publications: dict[str, datetime] = {}
                for row in rows:
                    manifest = OutputManifest.model_validate_json(json.dumps(row.manifest))
                    run = record(row.record)
                    verify_manifest(manifest, run, self.environment)
                    binding = Binding.model_validate_json(json.dumps(row.binding))
                    if (
                        not row.enrollment_complete
                        or manifest.generated_at > now
                        or binding.model_name != row.enrolled_name
                        or binding.model_version != row.enrolled_version
                        or binding != manifest.resolved_model
                    ):
                        raise ValueError("model_catalog_enrollment_pin_mismatch")
                    bindings[binding.model_version] = binding
                    publications[binding.model_version] = manifest.generated_at
                approved = None
                if bindings:
                    head = conn.execute(
                        text("""
                        SELECT h.release_id,r.model_name,r.model_version,
                               CASE WHEN octet_length(r.release::text)<=:bytes THEN r.release END release,
                               EXISTS(SELECT 1 FROM ai.model_steps s WHERE s.decision_id=r.decision_id AND s.phase='completed') complete
                        FROM ai.model_heads h LEFT JOIN ai.model_releases r USING(release_id) WHERE h.model_name=:model
                    """),
                        params,
                    ).one_or_none()
                    if head is not None and head.model_version in bindings:
                        release = Release.model_validate_json(json.dumps(head.release))
                        if (
                            not head.complete
                            or release.release_id != head.release_id
                            or release.binding != bindings[head.model_version]
                            or head.model_name != MODEL
                        ):
                            raise ValueError("model_catalog_approval_pin_mismatch")
                        approved = ApprovedModelRelease(
                            release_id=release.release_id,
                            model_version=release.binding.model_version,
                            image_digest=release.image_digest,
                        )
                versions = []
                for version, binding in bindings.items():
                    q = binding.qualification
                    versions.append(
                        CatalogVersion.model_validate_json(
                            json.dumps(
                                dict(
                                    model_version=version,
                                    status="approved_release_recorded"
                                    if approved and approved.model_version == version
                                    else "previously_published",
                                    model_family=q.model_family,
                                    flavor=q.flavor,
                                    mlflow_run_id=binding.mlflow_run_id,
                                    model_artifact=q.model.model_dump(mode="json"),
                                    model_card_report=q.gates["model_card"].report.model_dump(
                                        mode="json"
                                    ),
                                    qualification_sha256=binding.qualification_sha256,
                                    config_sha256=q.config_sha256,
                                    evaluation_id=q.evaluation_id,
                                    visible_last_published_at=publications[version].isoformat(),
                                    freshness=CatalogFreshness(evaluated_at=now).model_dump(
                                        mode="json"
                                    ),
                                )
                            )
                        )
                    )
                return CatalogSnapshot(tuple(versions), approved, now)
