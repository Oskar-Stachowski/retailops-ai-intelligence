"""Common metadata dispatch and whole-report authorization keep anomaly scope intact."""

import json
from copy import deepcopy
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from test_access import bearer, policy_file, problem
from test_anomaly_quality import POLICY, cases
from test_evaluations import actor

from retailops_ai.anomaly_evaluation.quality import assess
from retailops_ai.api.app import create_app
from retailops_ai.config import Settings
from retailops_ai.model_lifecycle.anomaly_evaluation_contracts import (
    AnomalyEvaluationDescriptor,
    AnomalyEvaluationEvidence,
    version_evaluation_id,
)
from retailops_ai.model_lifecycle.anomaly_evaluation_store import checked, page, projection, visible
from retailops_ai.model_lifecycle.evaluation_contracts import EvaluationQuery
from retailops_ai.model_lifecycle.evaluation_store import EvaluationError
from retailops_ai.model_lifecycle.read_contracts import CatalogScope
from retailops_ai.source_snapshot.files import json_sha256

NOW = datetime(2026, 9, 1, tzinfo=UTC)


@pytest.fixture
def evidence():
    q = assess(cases(), POLICY)
    summary = q["descriptor"]["summary"]
    descriptor = AnomalyEvaluationDescriptor(
        evaluation_id=version_evaluation_id(q["quality_id"], "1", "1" * 32),
        quality_id=q["quality_id"],
        registered_model_version="1",
        mlflow_run_id="1" * 32,
        detector_id=summary["detector_id"],
        family=summary["family"],
        scope=dict(
            product_ids=("p-101", "p-202"), selling_location_ids=("s-1",), channels=("store",)
        ),
        source_dataset_ids=tuple(sorted(c["source_dataset_id"] for c in summary["cases"])),
        generated_at=NOW,
        model_artifact=dict(sha256="2" * 64, size_bytes=123),
        quality_policy=POLICY,
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
    )
    return AnomalyEvaluationEvidence(
        descriptor=descriptor, evidence_sha256=json_sha256(descriptor.model_dump(mode="json"))
    )


class MemoryAnomalyEvaluations:
    def __init__(self, evidence):
        self.evidence = evidence

    def evaluations(self, query, principal):
        checked(query, principal)
        items = (
            (projection(self.evidence, NOW),) if visible(self.evidence, query, principal) else ()
        )
        return page(items, query, principal)

    def evaluation(self, identity, scope, principal):
        checked(scope, principal)
        if identity != self.evidence.descriptor.evaluation_id or not visible(
            self.evidence, scope, principal
        ):
            raise EvaluationError(404, "evaluation-not-found")
        return projection(self.evidence, NOW, detail=True)


def test_global_anomaly_report_is_hidden_for_partial_scope(evidence):
    broad = actor(products=("p-101", "p-202"), caps=("anomaly:read",))
    assert visible(evidence, CatalogScope(), broad)
    assert not visible(evidence, CatalogScope(), actor(caps=("anomaly:read",)))
    assert not visible(evidence, CatalogScope(product_id="p-101"), broad)
    detail = projection(evidence, NOW, detail=True)
    assert detail.metrics.high_severity_precision == 1
    assert detail.counts["episodes"] == 15
    assert "episode_id" not in detail.model_dump_json()
    assert "delay_and_repeat_details" not in detail.model_dump_json()
    raw = evidence.model_dump(mode="json")
    raw["descriptor"]["metrics"]["precision"] = 0.25
    with pytest.raises(ValueError, match="evidence_identity"):
        AnomalyEvaluationEvidence.model_validate_json(json.dumps(raw))


def test_common_evaluation_route_dispatches_caps_and_requires_whole_scope(tmp_path, evidence):
    def mutate(value):
        value["grants"][0]["capabilities"] = ["anomaly:read"]

    auth, tokens = policy_file(tmp_path, mutate)
    app = create_app(
        Settings(APP_ENV="test", ARTIFACT_ROOT="./artifacts", API_AUTH_FILE=auth),
        anomaly_evaluation_reader=MemoryAnomalyEvaluations(evidence),
    )
    with TestClient(app, base_url="http://127.0.0.1") as c:
        url = "/api/v1/evaluations/" + evidence.descriptor.evaluation_id
        problem(c.get(url), 401)
        problem(c.get(url, headers=bearer(tokens["local-viewer"])), 404)
        problem(c.get(url, headers=bearer(tokens["local-admin"])), 403)
        response = c.get("/api/v1/evaluations", headers=bearer(tokens["local-viewer"]))
        assert response.status_code == 200 and response.json()["pagination"]["total"] == 0
        assert "p-202" not in response.text
        problem(c.get(url + "?product_id=p-202", headers=bearer(tokens["local-viewer"])), 403)


def test_common_union_view_cannot_reuse_another_scope_or_modified_report(evidence):
    who = actor(products=("p-101", "p-202"), caps=("anomaly:read",))
    item = projection(evidence, NOW)
    first = page((item,), EvaluationQuery(limit=1), who)
    assert (
        page((item,), EvaluationQuery(view_sha256=first.view_sha256), who).view_sha256
        == first.view_sha256
    )
    for query, values in (
        (EvaluationQuery(view_sha256=first.view_sha256, product_id="p-101"), (item,)),
        (EvaluationQuery(view_sha256=first.view_sha256), ()),
    ):
        with pytest.raises(EvaluationError, match="view-changed"):
            page(values, query, who)
    changed = deepcopy(evidence.model_dump(mode="json"))
    changed["descriptor"]["registered_model_version"] = "2"
    changed["descriptor"]["evaluation_id"] = version_evaluation_id(
        changed["descriptor"]["quality_id"], "2", changed["descriptor"]["mlflow_run_id"]
    )
    changed["evidence_sha256"] = json_sha256(changed["descriptor"])
    other = projection(AnomalyEvaluationEvidence.model_validate_json(json.dumps(changed)), NOW)
    with pytest.raises(EvaluationError, match="view-changed"):
        page((other,), EvaluationQuery(view_sha256=first.view_sha256), who)


def test_repeated_registration_keeps_shared_quality_and_separate_version_identity(evidence):
    first = evidence.descriptor
    raw = evidence.model_dump(mode="json")
    raw["descriptor"]["registered_model_version"] = "2"
    raw["evidence_sha256"] = json_sha256(raw["descriptor"])
    with pytest.raises(ValueError, match="version_identity"):
        AnomalyEvaluationEvidence.model_validate_json(json.dumps(raw))
    raw["descriptor"]["evaluation_id"] = version_evaluation_id(
        first.quality_id, "2", first.mlflow_run_id
    )
    raw["evidence_sha256"] = json_sha256(raw["descriptor"])
    second = AnomalyEvaluationEvidence.model_validate_json(json.dumps(raw))
    assert second.descriptor.quality_id == first.quality_id
    assert second.descriptor.evaluation_id != first.evaluation_id
