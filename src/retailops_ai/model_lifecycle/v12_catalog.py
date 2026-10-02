"""V12 catalog from scoped, complete publications and immutable enrollment/release records."""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from sqlalchemy import Engine, text

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs.queue import checked
from retailops_ai.forecast_jobs.source_freshness import SourceFreshness
from retailops_ai.forecast_jobs.v12_batch import V12BatchReceipt
from retailops_ai.forecast_jobs.v12_publication import V12Publication, verify_publication
from retailops_ai.forecast_jobs.v12_queue import record
from retailops_ai.model_lifecycle.read_contracts import (
    CatalogFreshness,
    CatalogPagination,
    CatalogQuery,
    CatalogScope,
)
from retailops_ai.model_lifecycle.reader import CatalogError, authorized
from retailops_ai.model_lifecycle.v12_lifecycle_contracts import (
    V12Binding,
    V12ModelRelease,
    model_namespace,
)
from retailops_ai.model_lifecycle.v12_metadata_contracts import (
    V12ApprovedRelease,
    V12CatalogModel,
    V12CatalogVersion,
    V12ModelPage,
    V12VersionPage,
    evaluation_identity,
)

MAX_VERSIONS = 1000
MAX_READ_BYTES = 16 * 1024**2
MAX_HEAD_BYTES = 256 * 1024


class V12ModelCatalog(Protocol):
    def models(self, query: CatalogQuery, actor: Principal) -> V12ModelPage: ...
    def model(self, name: str, scope: CatalogScope, actor: Principal) -> V12CatalogModel: ...
    def versions(self, name: str, query: CatalogQuery, actor: Principal) -> V12VersionPage: ...


@dataclass(frozen=True)
class V12CatalogSnapshot:
    model: str
    versions: tuple[V12CatalogVersion, ...]
    approved: V12ApprovedRelease | None
    generated_at: datetime

    def summary(self) -> V12CatalogModel:
        if not self.versions:
            raise CatalogError(404, "model-not-found")
        return V12CatalogModel.model_validate_json(
            json.dumps(
                dict(
                    model_name=self.model,
                    visible_version_count=len(self.versions),
                    approved_release=self.approved.model_dump(mode="json")
                    if self.approved
                    else None,
                    freshness=CatalogFreshness(evaluated_at=self.generated_at).model_dump(
                        mode="json"
                    ),
                    generated_at=self.generated_at.isoformat(),
                )
            )
        )


def page(
    snapshot: V12CatalogSnapshot, query: CatalogQuery, actor: Principal, *, models: bool
) -> V12ModelPage | V12VersionPage:
    scope = authorized(query, actor)
    items = (
        (snapshot.summary(),)
        if models and snapshot.versions
        else ()
        if models
        else snapshot.versions
    )
    view = canonical_sha256(
        dict(
            projection="v12-catalog-v1",
            model=snapshot.model,
            models=models,
            principal_id=actor.principal_id,
            scope=dict(products=scope.products, locations=scope.locations, channels=scope.channels),
            items=[v.model_dump(mode="json", exclude={"freshness", "generated_at"}) for v in items],
        )
    )
    if query.offset and query.view_sha256 is None:
        raise CatalogError(409, "model-view-required")
    if query.view_sha256 is not None and query.view_sha256 != view:
        raise CatalogError(409, "model-view-changed")
    end = query.offset + query.limit
    cls = V12ModelPage if models else V12VersionPage
    return cls.model_validate_json(
        json.dumps(
            dict(
                items=[v.model_dump(mode="json") for v in items[query.offset : end]],
                pagination=CatalogPagination(
                    limit=query.limit,
                    offset=query.offset,
                    total=len(items),
                    next_offset=end if end < len(items) else None,
                ).model_dump(mode="json"),
                generated_at=snapshot.generated_at.isoformat(),
                data_status="available" if items else "no_data",
                view_sha256=view,
            )
        )
    )


class PostgresV12Catalog:
    def __init__(
        self,
        engine: Engine,
        environment: Literal["local", "test"],
        *,
        mechanics: bool = False,
        development: bool = False,
    ) -> None:
        if environment not in {"local", "test"} or (mechanics and environment != "test"):
            raise ValueError("v12_catalog_environment")
        self.engine, self.environment = engine, environment
        self.name = model_namespace(environment, mechanics=mechanics, development=development)

    def models(self, query: CatalogQuery, actor: Principal) -> V12ModelPage:
        result = page(self._snapshot(query, actor), query, actor, models=True)
        if not isinstance(result, V12ModelPage):
            raise ValueError("v12_model_page_type")
        return result

    def model(self, name: str, scope: CatalogScope, actor: Principal) -> V12CatalogModel:
        authorized(scope, actor)
        if name != self.name:
            raise CatalogError(404, "model-not-found")
        return self._snapshot(scope, actor).summary()

    def versions(self, name: str, query: CatalogQuery, actor: Principal) -> V12VersionPage:
        authorized(query, actor)
        if name != self.name:
            raise CatalogError(404, "model-not-found")
        snapshot = self._snapshot(query, actor)
        if not snapshot.versions:
            raise CatalogError(404, "model-not-found")
        result = page(snapshot, query, actor, models=False)
        if not isinstance(result, V12VersionPage):
            raise ValueError("v12_version_page_type")
        return result

    def _snapshot(self, request: CatalogScope, actor: Principal) -> V12CatalogSnapshot:
        scope = authorized(request, actor)
        params = dict(
            env=self.environment,
            model=self.name,
            products=list(scope.products),
            locations=list(scope.locations),
            channels=list(scope.channels),
            cap=MAX_VERSIONS + 1,
        )
        with self.engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
            with conn.begin():
                conn.execute(text("SET TRANSACTION READ ONLY"))
                now = checked(conn)
                conn.execute(text("SET LOCAL statement_timeout='3s'"))
                headers = conn.execute(
                    text("""
WITH visible AS (
 SELECT DISTINCT ON (o.document->'resolved_model'->>'model_version')
 o.artifact_id,o.document->'resolved_model'->>'model_version' version,o.run_id,
 octet_length(o.document::text)+octet_length(c.receipt::text)+octet_length(r.record::text) bytes
 FROM ai.v12_forecast_outputs o JOIN ai.v12_batch_runs r USING(run_id)
 LEFT JOIN ai.v12_batch_receipts c USING(run_id)
 WHERE o.environment=:env AND o.model_name=:model
  AND o.document->'scope'->>'channel'=ANY(:channels)
  AND (o.document->'scope'->'product_ids') ?| CAST(:products AS text[])
  AND (o.document->'scope'->'selling_location_ids') ?| CAST(:locations AS text[])
 ORDER BY version,(o.document->>'generated_at')::timestamptz DESC,o.run_id DESC
)
SELECT visible.*,octet_length(v.binding::text) binding_bytes
FROM visible LEFT JOIN ai.v12_model_versions v ON v.model_name=:model AND v.model_version=visible.version
ORDER BY visible.version::bigint LIMIT :cap
"""),
                    params,
                ).all()
                if any(h.bytes is None or h.binding_bytes is None for h in headers):
                    raise ValueError("v12_catalog_missing_dependency")
                if (
                    len(headers) > MAX_VERSIONS
                    or sum(h.bytes + h.binding_bytes for h in headers) > MAX_READ_BYTES
                ):
                    raise CatalogError(429, "model-read-budget")
                bindings: dict[str, V12Binding] = {}
                publications = {}
                for h in headers:
                    row = conn.execute(
                        text("""
SELECT o.document,r.record,c.receipt,v.binding,
 p.profile->>'schema_version' input_version,p.profile->'source_freshness' input_freshness,
 EXISTS(SELECT 1 FROM ai.v12_model_steps s WHERE s.decision_id=v.decision_id AND s.phase='completed') complete
FROM ai.v12_forecast_outputs o JOIN ai.v12_batch_runs r USING(run_id)
JOIN ai.v12_batch_receipts c USING(run_id)
JOIN ai.forecast_prepared_inputs p ON p.environment=o.environment AND p.profile_id=r.profile_id
JOIN ai.v12_model_versions v ON v.model_name=o.model_name AND v.model_version=o.document->'resolved_model'->>'model_version'
WHERE o.artifact_id=:id
"""),
                        dict(id=h.artifact_id),
                    ).one()
                    output = V12Publication.model_validate_json(json.dumps(row.document))
                    run = record(row.record)
                    receipt = V12BatchReceipt.model_validate_json(json.dumps(row.receipt))
                    binding = V12Binding.model_validate_json(json.dumps(row.binding))
                    source = SourceFreshness.model_validate_json(json.dumps(row.input_freshness))
                    verify_publication(output, run, receipt)
                    if (
                        output.artifact_id != h.artifact_id
                        or output.environment != self.environment
                        or binding.model_name != self.name
                        or binding.model_version != h.version
                        or not row.complete
                        or output.resolved_model != binding
                        or output.generated_at > now
                        or row.input_version != "1.1"
                        or source.scoped(output.scope) != output.source_freshness
                    ):
                        raise ValueError("v12_catalog_publication_or_enrollment_pin")
                    bindings[h.version] = binding
                    publications[h.version] = output.generated_at
                approved = None
                if bindings:
                    head = conn.execute(
                        text("""
SELECT h.release_id,r.model_version,octet_length(r.release::text) bytes
FROM ai.v12_model_heads h JOIN ai.v12_model_releases r USING(release_id)
WHERE h.model_name=:model
"""),
                        dict(model=self.name),
                    ).first()
                    if head is not None and head.model_version in bindings:
                        if head.bytes > MAX_HEAD_BYTES:
                            raise CatalogError(429, "model-read-budget")
                        row = conn.execute(
                            text("""
SELECT release,EXISTS(SELECT 1 FROM ai.v12_model_steps s WHERE s.decision_id=r.decision_id AND s.phase='completed') complete
FROM ai.v12_model_releases r WHERE release_id=:id
"""),
                            dict(id=head.release_id),
                        ).one()
                        release = V12ModelRelease.model_validate_json(json.dumps(row.release))
                        if (
                            not row.complete
                            or release.release_id != head.release_id
                            or release.binding != bindings[head.model_version]
                        ):
                            raise ValueError("v12_catalog_head_binding")
                        approved = V12ApprovedRelease(
                            release_id=release.release_id,
                            model_version=head.model_version,
                            image_digest=release.image_digest,
                        )
                versions = []
                for version, binding in bindings.items():
                    pin = binding.approval.qualification.pin
                    versions.append(
                        V12CatalogVersion(
                            model_name=binding.model_name,
                            model_version=version,
                            status="approved_release_recorded"
                            if approved and approved.model_version == version
                            else "previously_published",
                            original_export_run_id=pin.run_id,
                            mlflow_run_id=binding.mlflow_run_id,
                            campaign_mlflow_run_id=binding.campaign_mlflow_run_id,
                            cohort_id=pin.cohort_id,
                            fold=pin.fold.name,
                            recipe_id=pin.recipe_id,
                            recipe_artifact=pin.recipe,
                            source_dataset_id=pin.source_dataset_id,
                            snapshot_id=pin.snapshot_id,
                            code_sha256=pin.code_sha256,
                            dependency_lock_sha256=pin.dependency_lock_sha256,
                            approval_sha256=binding.approval_sha256,
                            runtime_pin_sha256=canonical_sha256(pin.model_dump(mode="json")),
                            approval_valid_until=binding.approval.qualification.valid_until,
                            evaluation_id=evaluation_identity(self.name, pin.run_id),
                            visible_last_published_at=publications[version],
                            freshness=CatalogFreshness(evaluated_at=now),
                        )
                    )
                return V12CatalogSnapshot(self.name, tuple(versions), approved, now)
