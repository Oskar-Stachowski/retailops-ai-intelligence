"""Immutable recomputed anomaly metrics with authorization to the whole report."""

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, text

from retailops_ai.anomaly_evaluation.verification import verify_quality
from retailops_ai.anomaly_portfolio.lifecycle_contract import Binding
from retailops_ai.anomaly_portfolio.model import Model
from retailops_ai.anomaly_portfolio.result_store import ReadError, authorized
from retailops_ai.anomaly_portfolio.serving_contract import Query
from retailops_ai.domain.access import Principal
from retailops_ai.model_lifecycle.anomaly_evaluation_contracts import (
    AnomalyEvaluationDescriptor,
    AnomalyEvaluationDetail,
    AnomalyEvaluationEvidence,
    AnomalyEvaluationScope,
    AnomalyEvaluationSummary,
)
from retailops_ai.model_lifecycle.evaluation_contracts import (
    EvaluationDetail,
    EvaluationPage,
    EvaluationQuery,
    EvaluationSummary,
)
from retailops_ai.model_lifecycle.evaluation_store import EvaluationError, EvaluationReader
from retailops_ai.model_lifecycle.read_contracts import (
    CatalogFreshness,
    CatalogPagination,
    CatalogScope,
)
from retailops_ai.source_snapshot.files import canonical_json, json_sha256

MAX_BYTES = 128 * 1024
MAX_RECORDS = 256


def checked(scope: CatalogScope, actor: Principal) -> None:
    try:
        authorized(actor, Query(**scope.model_dump(include=set(CatalogScope.model_fields))))
    except ReadError:
        raise EvaluationError(
            403,
            "evaluation-read-denied"
            if "anomaly:read" not in actor.capabilities
            else "evaluation-scope-invalid",
        ) from None


def visible(e: AnomalyEvaluationEvidence, scope: CatalogScope, actor: Principal) -> bool:
    # A filter narrows authorization; global metrics cannot be presented as
    # metrics of one SKU or location when they include other entities.
    return all(
        set(values) <= ({selected} if selected is not None else allowed)
        for values, selected, allowed in (
            (e.descriptor.scope.product_ids, scope.product_id, actor.product_ids),
            (
                e.descriptor.scope.selling_location_ids,
                scope.selling_location_id,
                actor.selling_location_ids,
            ),
            (e.descriptor.scope.channels, scope.channel, actor.channels),
        )
    )


def projection(
    e: AnomalyEvaluationEvidence, now: datetime, *, detail: bool = False
) -> AnomalyEvaluationSummary | AnomalyEvaluationDetail:
    cls = AnomalyEvaluationDetail if detail else AnomalyEvaluationSummary
    value = e.descriptor.model_dump(mode="json", include=set(cls.model_fields))
    value.update(
        evidence_sha256=e.evidence_sha256,
        freshness=CatalogFreshness(evaluated_at=now).model_dump(mode="json"),
    )
    return cls.model_validate_json(json.dumps(value))


def page(items: tuple[Any, ...], query: EvaluationQuery, actor: Principal) -> EvaluationPage:
    ordered = tuple(sorted(items, key=lambda i: (i.generated_at, i.evaluation_id), reverse=True))
    if len(ordered) > MAX_RECORDS:
        raise EvaluationError(429, "evaluation-read-budget")
    digest = json_sha256(
        {
            "principal": actor.principal_id,
            "scope": query.model_dump(mode="json", exclude={"limit", "offset", "view_sha256"}),
            "evidence": [i.evidence_sha256 for i in ordered],
        }
    )
    if query.offset and query.view_sha256 is None:
        raise EvaluationError(409, "evaluation-view-required")
    if query.view_sha256 is not None and query.view_sha256 != digest:
        raise EvaluationError(409, "evaluation-view-changed")
    end = query.offset + query.limit
    return EvaluationPage(
        items=ordered[query.offset : end],
        pagination=CatalogPagination(
            limit=query.limit,
            offset=query.offset,
            total=len(ordered),
            next_offset=end if end < len(ordered) else None,
        ),
        generated_at=datetime.now(UTC),
        data_status="available" if ordered else "no_data",
        view_sha256=digest,
    )


class PostgresAnomalyEvaluations:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def register(
        self,
        binding: Binding,
        gate: dict[str, Any],
        config: dict[str, Any],
        artifact: Callable[[str], bytes],
    ) -> AnomalyEvaluationEvidence:
        binding = Binding.model_validate_json(binding.model_dump_json())
        q = binding.qualification
        raw_model = artifact("model.json")
        model = Model.model_validate_json(raw_model)
        if (
            (len(raw_model), hashlib.sha256(raw_model).hexdigest())
            != (q.model.size_bytes, q.model.sha256)
            or hashlib.sha256(canonical_json(config) + b"\n").hexdigest() != q.config.sha256
            or hashlib.sha256(canonical_json(gate) + b"\n").hexdigest()
            != q.gates["segments"].report.sha256
            or any(g.status != "passed" for g in q.gates.values())
        ):
            raise ValueError("anomaly_evaluation_artifact_binding")
        quality = verify_quality(gate, config, artifact, model.detector_id, q.model_family)
        if q.evaluation_id != quality["quality_id"]:
            raise ValueError("anomaly_evaluation_qualification_binding")
        summary = quality["descriptor"]["summary"]
        scopes = config["selection"]["descriptor"]["protocol"]["scopes"]
        descriptor = AnomalyEvaluationDescriptor.model_validate_json(
            json.dumps(
                {
                    "evaluation_id": quality["quality_id"],
                    "registered_model_version": binding.model_version,
                    "mlflow_run_id": binding.mlflow_run_id,
                    "detector_id": model.detector_id,
                    "family": q.model_family,
                    "scope": AnomalyEvaluationScope(
                        product_ids=tuple(sorted({s["product_id"] for s in scopes})),
                        selling_location_ids=tuple(
                            sorted({s["selling_location_id"] for s in scopes})
                        ),
                        channels=tuple(sorted({s["channel"] for s in scopes})),
                    ).model_dump(mode="json"),
                    "source_dataset_ids": sorted(c["source_dataset_id"] for c in summary["cases"]),
                    "generated_at": gate["evaluated_at"],
                    "model_artifact": q.model.model_dump(mode="json"),
                    "quality_policy": quality["descriptor"]["policy"],
                    **{
                        k: summary[k]
                        for k in (
                            "metrics",
                            "counts",
                            "episodes_per_type",
                            "detected_episodes_per_type",
                            "positive_observations_per_type",
                            "per_segment",
                        )
                    },
                }
            )
        )
        evidence = AnomalyEvaluationEvidence(
            descriptor=descriptor,
            evidence_sha256=json_sha256(descriptor.model_dump(mode="json")),
        )
        if len(canonical_json(evidence.model_dump(mode="json"))) > MAX_BYTES:
            raise ValueError("anomaly_evaluation_byte_budget")
        with self.engine.begin() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(707071)"))
            enrolled = conn.execute(
                text(
                    "SELECT binding FROM ai.anomaly_model_versions WHERE model_name=:name AND model_version=:version"
                ),
                {"name": binding.model_name, "version": binding.model_version},
            ).scalar_one_or_none()
            if enrolled is None or Binding.model_validate_json(json.dumps(enrolled)) != binding:
                raise ValueError("anomaly_evaluation_version_not_enrolled")
            old = conn.execute(
                text("SELECT evidence FROM ai.anomaly_evaluations WHERE evaluation_id=:id"),
                {"id": descriptor.evaluation_id},
            ).scalar_one_or_none()
            if old is not None:
                previous = AnomalyEvaluationEvidence.model_validate_json(json.dumps(old))
                if previous != evidence:
                    raise ValueError("anomaly_evaluation_registration_conflict")
                return previous
            if conn.scalar(text("SELECT count(*) FROM ai.anomaly_evaluations")) >= MAX_RECORDS:
                raise ValueError("anomaly_evaluation_registration_capacity")
            if descriptor.generated_at > conn.scalar(text("SELECT now()")):
                raise ValueError("anomaly_evaluation_from_future")
            conn.execute(
                text(
                    "INSERT INTO ai.anomaly_evaluations(evaluation_id,evidence_sha256,model_version,evidence) VALUES(:id,:sha,:version,CAST(:body AS jsonb))"
                ),
                {
                    "id": descriptor.evaluation_id,
                    "sha": evidence.evidence_sha256,
                    "version": binding.model_version,
                    "body": evidence.model_dump_json(),
                },
            )
        return evidence

    def read(
        self, scope: CatalogScope, actor: Principal, identity: str | None = None
    ) -> tuple[AnomalyEvaluationEvidence, ...]:
        checked(scope, actor)
        params = {
            "id": identity,
            "products": json.dumps(
                [scope.product_id] if scope.product_id else sorted(actor.product_ids)
            ),
            "locations": json.dumps(
                [scope.selling_location_id]
                if scope.selling_location_id
                else sorted(actor.selling_location_ids)
            ),
            "channels": json.dumps([scope.channel] if scope.channel else sorted(actor.channels)),
        }
        with self.engine.connect() as conn:
            rows = (
                conn.execute(
                    text(
                        "SELECT evaluation_id,evidence_sha256,evidence,registered_at FROM ai.anomaly_evaluations "
                    "WHERE (CAST(:id AS text) IS NULL OR evaluation_id=:id) "
                        "AND evidence->'descriptor'->'scope'->'product_ids' <@ CAST(:products AS jsonb) "
                        "AND evidence->'descriptor'->'scope'->'selling_location_ids' <@ CAST(:locations AS jsonb) "
                        "AND evidence->'descriptor'->'scope'->'channels' <@ CAST(:channels AS jsonb) "
                        "ORDER BY registered_at,evaluation_id LIMIT 257"
                    ),
                    params,
                )
                .mappings()
                .all()
            )
        if len(rows) > MAX_RECORDS:
            raise EvaluationError(429, "evaluation-read-budget")
        results = []
        for row in rows:
            e = AnomalyEvaluationEvidence.model_validate_json(json.dumps(row["evidence"]))
            if (
                (e.descriptor.evaluation_id, e.evidence_sha256)
                != (row["evaluation_id"], row["evidence_sha256"])
                or not e.descriptor.generated_at <= row["registered_at"] <= datetime.now(UTC)
                or not visible(e, scope, actor)
                or len(canonical_json(e.model_dump(mode="json"))) > MAX_BYTES
            ):
                raise ValueError("anomaly_evaluation_stored_evidence")
            results.append(e)
        return tuple(results)

    def evaluations(self, query: EvaluationQuery, actor: Principal) -> EvaluationPage:
        evidence = self.read(query, actor)
        now = datetime.now(UTC)
        return page(
            tuple(projection(e, now) for e in evidence)
            if query.quality_status in (None, "passed")
            else (),
            query,
            actor,
        )

    def evaluation(
        self, identity: str, scope: CatalogScope, actor: Principal
    ) -> AnomalyEvaluationDetail:
        evidence = self.read(scope, actor, identity)
        if not evidence:
            raise EvaluationError(404, "evaluation-not-found")
        result = projection(evidence[0], datetime.now(UTC), detail=True)
        assert isinstance(result, AnomalyEvaluationDetail)  # noqa: S101 - fixed projection
        return result


class CombinedEvaluations:
    def __init__(self, forecast: EvaluationReader | None, anomaly: EvaluationReader) -> None:
        self.forecast, self.anomaly = forecast, anomaly

    def evaluations(self, query: EvaluationQuery, actor: Principal) -> EvaluationPage:
        # Each backend returns only fully authorized reports. The common view
        # pins the union, including both families, before counts and pagination.
        full = EvaluationQuery(
            **query.model_dump(exclude={"limit", "offset", "view_sha256"}), limit=200
        )
        items: list[EvaluationSummary | AnomalyEvaluationSummary] = []
        for capability, backend in (
            ("forecast:read", self.forecast),
            ("anomaly:read", self.anomaly),
        ):
            if capability in actor.capabilities:
                if backend is None:
                    raise EvaluationError(503, "evaluation-evidence-invalid")
                result = backend.evaluations(full, actor)
                items.extend(result.items)
                if result.pagination.next_offset is not None:
                    next_query = full.model_copy(
                        update={
                            "offset": result.pagination.next_offset,
                            "view_sha256": result.view_sha256,
                        }
                    )
                    items.extend(backend.evaluations(next_query, actor).items)
        if not {"forecast:read", "anomaly:read"} & actor.capabilities:
            raise EvaluationError(403, "evaluation-read-denied")
        return page(tuple(items), query, actor)

    def evaluation(
        self, identity: str, scope: CatalogScope, actor: Principal
    ) -> EvaluationDetail | AnomalyEvaluationDetail:
        if identity.startswith("anomaly-quality-sha256-"):
            if "anomaly:read" not in actor.capabilities:
                raise EvaluationError(403, "evaluation-read-denied")
            return self.anomaly.evaluation(identity, scope, actor)
        if "forecast:read" not in actor.capabilities:
            raise EvaluationError(403, "evaluation-read-denied")
        if self.forecast is None:
            raise EvaluationError(503, "evaluation-evidence-invalid")
        return self.forecast.evaluation(identity, scope, actor)
