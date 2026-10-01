"""Publication/read boundaries on invented, explicit small v12 inference fixtures."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from test_access import bearer, policy_file, problem
from test_v12_lifecycle import artifacts as artifacts
from test_v12_lifecycle import inputs as inputs
from test_v12_lifecycle import loaded as loaded
from test_v12_lifecycle import prepared_input as prepared_input
from test_v12_lifecycle import qualification as qualification
from test_v12_lifecycle import serving as serving
from test_v12_lifecycle import tables as tables
from test_v12_lifecycle import timeline as timeline
from test_v12_queue import actor as pipeline
from test_v12_queue import release_for, running

from retailops_ai.api.app import create_app
from retailops_ai.config import Settings
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs.queue import BatchError
from retailops_ai.forecast_jobs.read_contracts import ForecastQuery
from retailops_ai.forecast_jobs.reader import ForecastReadError
from retailops_ai.forecast_jobs.v12_batch import V12BatchReceipt, V12BatchRun, computation_receipt
from retailops_ai.forecast_jobs.v12_output_store import PostgresV12Publisher
from retailops_ai.forecast_jobs.v12_publication import (
    V12Publication,
    publication,
    verify_publication,
)
from retailops_ai.forecast_jobs.v12_reader import PostgresV12ForecastReader, projection


@pytest.fixture
def completed(serving, inputs):
    _, model = serving
    release = release_for(model)
    run = running(inputs, release)
    result = model.predict(inputs)
    receipt = computation_receipt(run, inputs, release, (result,))
    raw = run.model_dump(mode="json")
    raw.update(
        status="succeeded",
        completed_at=datetime.now(UTC).isoformat(),
        output_id=receipt.artifact_id,
    )
    succeeded = V12BatchRun.model_validate_json(json.dumps(raw))
    return publication(succeeded, inputs, receipt, datetime.now(UTC)), succeeded, receipt


def viewer(output, *, products=None, principal_id="local-viewer"):
    scope = output.scope
    return Principal(
        principal_id,
        frozenset({"viewer"}),
        frozenset({"forecast:read"}),
        frozenset(products or scope.product_ids),
        frozenset(scope.selling_location_ids),
        frozenset({scope.channel}),
    )


def page(completed, query=None, who=None, **kwargs):
    output, run, receipt = completed
    return projection(
        query or ForecastQuery(),
        who or viewer(output),
        ((output, run, receipt),),
        now=kwargs.pop("now", output.generated_at),
        unpublished=kwargs.pop("unpublished", {}),
        **kwargs,
    )


def rehashed(raw):
    raw["artifact_id"] = "v12-forecasts-sha256-" + canonical_sha256(
        {k: v for k, v in raw.items() if k != "artifact_id"}
    )
    return V12Publication.model_validate_json(json.dumps(raw))


def test_exact_functionals_and_baselines_survive_publication_and_api_projection(completed):
    output, run, receipt = completed
    original = {p.key: p for part in receipt.parts for p in part.predictions}
    result = page(completed)
    assert result.pagination.total == 14 and len(result.items) == 14
    assert [r.prediction for r in result.items] == [r.prediction for r in output.rows]
    assert all(original[r.prediction.key] == r.prediction for r in result.items)
    assert all(
        r.receipt_id == receipt.artifact_id and r.release_id == run.release_id for r in result.items
    )
    assert all(r.quality_status == "passed_at_publication" for r in result.items)
    assert (
        "source_uri" not in result.model_dump_json()
        and "mlflow-artifacts:" not in result.model_dump_json()
    )
    assert "recipe_path" not in result.model_dump_json()


@pytest.mark.parametrize(
    "change", ["missing", "duplicate", "value", "part", "origin", "binding", "time"]
)
def test_rehashed_or_unchecked_publication_tamper_is_refused(completed, change):
    output, run, receipt = completed
    raw = output.model_dump(mode="json")
    if change == "missing":
        raw["rows"].pop()
    elif change == "duplicate":
        raw["rows"][-1] = raw["rows"][0]
    elif change == "value":
        raw["rows"][-1]["prediction"]["candidate"]["mean"] = 12345
        raw["predictions_sha256"] = canonical_sha256(
            [r["prediction"] for r in sorted(raw["rows"], key=lambda r: r["prediction"]["key"])]
        )
    elif change == "part":
        raw["rows"][-1]["execution_profile_id"] = "batch-profile-sha256-" + "b" * 64
    elif change == "origin":
        raw["rows"][-1]["forecast_origin"] = (output.as_of_time + timedelta(days=1)).isoformat()
    elif change == "binding":
        raw["release_id"] = "v12-model-release-sha256-" + "c" * 64
    else:
        raw["generated_at"] = (run.completed_at - timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError):
        changed = rehashed(raw)
        verify_publication(changed, run, receipt)


def test_unpublished_computation_and_foreign_read_scope_are_refused(completed, inputs):
    output, run, receipt = completed
    with pytest.raises(ValueError):
        verify_publication(
            output,
            run.model_copy(update=dict(status="running", completed_at=None, output_id=None)),
            receipt,
        )
    with pytest.raises(ForecastReadError, match="scope-invalid"):
        page(completed, ForecastQuery(product_id="outside"))
    with pytest.raises(ForecastReadError, match="read-denied"):
        page(completed, who=pipeline(inputs))
    with pytest.raises(ForecastReadError, match="output-not-found"):
        page(completed, ForecastQuery(inference_run_id="run-" + "b" * 32))
    with pytest.raises(ValueError):
        PostgresV12ForecastReader(None, "local", mechanics=True)


def test_pagination_is_stable_and_bound_to_principal_query_and_predictions(completed):
    first = page(completed, ForecastQuery(limit=3))
    second = page(completed, ForecastQuery(limit=3, offset=3, view_sha256=first.view_sha256))
    assert first.items + second.items == page(completed).items[:6]
    with pytest.raises(ForecastReadError, match="view-required"):
        page(completed, ForecastQuery(offset=3))
    with pytest.raises(ForecastReadError, match="view-changed"):
        page(
            completed,
            ForecastQuery(offset=3, view_sha256=first.view_sha256),
            viewer(completed[0], principal_id="another"),
        )
    aged = page(completed, now=completed[0].generated_at + timedelta(days=1))
    assert aged.view_sha256 == first.view_sha256
    assert all(r.freshness.status == "stale" for r in aged.items)
    absent = projection(
        ForecastQuery(), viewer(completed[0]), (), now=completed[0].generated_at, unpublished={}
    )
    assert absent.data_status == "no_data" and absent.pagination.total == 0


def test_newer_unpublished_attempt_marks_old_forecasts_stale(completed):
    output, run, _ = completed
    key = output.rows[0]
    unpublished = {
        (key.product_id, key.selling_location_id, key.channel, key.horizon_days): (
            output.as_of_time,
            run.requested_at + timedelta(seconds=1),
            "run-" + "f" * 32,
        )
    }
    result = page(completed, unpublished=unpublished)
    assert result.items[0].freshness.reason == "newer_run_unpublished"
    assert sum(r.freshness.reason == "newer_run_unpublished" for r in result.items) == 1


def test_null_functionals_and_exact_reference_metadata_are_preserved(completed):
    output, run, receipt = completed
    raw = receipt.model_dump(mode="json")
    prediction = raw["parts"][0]["predictions"][0]
    prediction["candidate"] = prediction["baseline"] = dict(median=None, mean=None, interval=None)
    prediction["metadata"].update(selected=None, baseline=None)
    raw["parts"][0]["predictions_sha256"] = canonical_sha256(raw["parts"][0]["predictions"])
    raw["predictions_sha256"] = canonical_sha256(
        sorted(raw["parts"][0]["predictions"], key=lambda p: p["key"])
    )
    raw["artifact_id"] = "v12-computation-sha256-" + canonical_sha256(
        {k: v for k, v in raw.items() if k != "artifact_id"}
    )
    changed_receipt = V12BatchReceipt.model_validate_json(json.dumps(raw))
    changed_run = run.model_copy(update=dict(output_id=changed_receipt.artifact_id))
    document = output.model_dump(mode="json")
    document["receipt_id"] = changed_receipt.artifact_id
    document["predictions_sha256"] = changed_receipt.predictions_sha256
    document["rows"][0]["prediction"] = prediction
    changed_output = rehashed(document)
    result = page((changed_output, changed_run, changed_receipt))
    assert result.items[0].prediction.candidate.model_dump() == dict(
        median=None, mean=None, interval=None
    )
    assert result.items[0].prediction.metadata.selected is None


def test_latest_order_uses_request_time_and_not_later_publication(completed):
    output, run, receipt = completed
    later_request = run.requested_at + timedelta(milliseconds=100)
    raw = receipt.model_dump(mode="json")
    raw["run_id"] = "run-" + "b" * 32
    raw["artifact_id"] = "v12-computation-sha256-" + canonical_sha256(
        {k: v for k, v in raw.items() if k != "artifact_id"}
    )
    later_receipt = V12BatchReceipt.model_validate_json(json.dumps(raw))
    later_run = run.model_copy(
        update=dict(
            run_id=raw["run_id"], requested_at=later_request, output_id=later_receipt.artifact_id
        )
    )
    document = output.model_dump(mode="json")
    document.update(run_id=later_run.run_id, receipt_id=later_receipt.artifact_id)
    later = rehashed(document)
    replay = rehashed(
        {
            **output.model_dump(mode="json"),
            "generated_at": (output.generated_at + timedelta(seconds=2))
            .isoformat()
            .replace("+00:00", "Z"),
        }
    )
    result = projection(
        ForecastQuery(),
        viewer(output),
        ((later, later_run, later_receipt), (replay, run, receipt)),
        now=replay.generated_at,
        unpublished={},
    )
    assert {r.inference_run_id for r in result.items} == {later_run.run_id}
    filtered = page(
        completed,
        ForecastQuery(
            target_from=output.as_of_time.date() + timedelta(days=2),
            target_to=output.as_of_time.date() + timedelta(days=4),
        ),
    )
    assert [r.horizon_days for r in filtered.items] == [2, 3, 4]
    with pytest.raises(ForecastReadError, match="read-budget"):
        projection(
            ForecastQuery(),
            viewer(output),
            (completed,) * 33,
            now=output.generated_at,
            unpublished={},
        )


def test_publisher_requires_pipeline_write_capability_before_database_access(completed):
    with pytest.raises(BatchError, match="run-denied"):
        PostgresV12Publisher(None, None).publish(completed[1].run_id, viewer(completed[0]))


class Backend:
    def __init__(self, completed):
        self.completed, self.calls, self.fail = completed, [], False

    def read(self, query, principal):
        self.calls.append(query)
        if self.fail:
            raise ValueError("private-path-or-token")
        return page(self.completed, query, principal)


def test_authenticated_v12_http_rejects_spoofing_and_preserves_v1_route(tmp_path, completed):
    output = completed[0]

    def mutate(value):
        value["grants"][0]["scope"] = dict(
            product_ids=list(output.scope.product_ids),
            selling_location_ids=list(output.scope.selling_location_ids),
            channels=[output.scope.channel],
        )

    policy, tokens = policy_file(tmp_path, mutate)
    backend = Backend(completed)
    with TestClient(
        create_app(
            Settings(APP_ENV="test", ARTIFACT_ROOT="./artifacts", API_AUTH_FILE=policy),
            v12_forecast_reader=backend,
        ),
        base_url="http://127.0.0.1",
    ) as client:
        uri = "/api/v1/forecasts/v12"
        problem(client.get(uri), 401)
        problem(client.get(uri, headers=bearer(tokens["local-admin"])), 403)
        assert not backend.calls
        headers = bearer(tokens["local-viewer"])
        result = client.get(uri, headers=headers, params=dict(limit=2))
        assert result.status_code == 200 and result.json()["pagination"]["total"] == 14
        for params in (
            {"limit": 201},
            {"user_id": "local-admin"},
            {"target_from": "2026-01-01"},
            {"target_from": "2026-01-01T00:00:00Z", "target_to": "2026-01-01"},
        ):
            problem(client.get(uri, headers=headers, params=params), 422)
        problem(client.get(uri + "?channel=store&channel=online", headers=headers), 422)
        problem(client.get("/api/v1/forecasts", headers=headers), 503)
        backend.fail = True
        response = client.get(uri, headers=headers)
        problem(response, 503)
        assert "private-path" not in response.text
