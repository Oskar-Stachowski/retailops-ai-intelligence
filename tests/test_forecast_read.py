import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_access import bearer, policy_file, problem
from test_forecast_publication import claim, inputs  # noqa: F401 - shared pytest fixtures

from retailops_ai.api.app import create_app
from retailops_ai.config import Settings
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.data_contracts.run import RunOutput
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs.publication import Partition, Publication, publication, receipt
from retailops_ai.forecast_jobs.publication_acceptance import result_for
from retailops_ai.forecast_jobs.read_contracts import ForecastQuery
from retailops_ai.forecast_jobs.reader import ForecastReadError, projection, resolve_scope


def actor(output, *, products=None, caps=("forecast:read",)):
    scope = output.manifest.scope
    return Principal(
        "local-viewer",
        frozenset({"viewer"}),
        frozenset(caps),
        frozenset(scope.product_ids if products is None else products),
        frozenset(scope.selling_location_ids),
        frozenset({scope.channel}),
    )


@pytest.fixture
def completed(request):
    claimed = request.getfixturevalue("claim")
    now = claimed.run.started_at + timedelta(seconds=1)
    output = publication(claimed.run, claimed.profile, result_for(claimed), now)
    run = claimed.run.model_copy(
        update={
            "status": "succeeded",
            "completed_at": now,
            "output_ref": RunOutput(
                artifact_id=output.manifest.artifact_id, kind="predictions", complete=True
            ),
        }
    )
    return output, run


def page(completed, query=None, who=None, **kwargs):
    output, run = completed
    return projection(
        query or ForecastQuery(),
        who or actor(output),
        (completed,),
        now=output.manifest.generated_at,
        unpublished={},
        **kwargs,
    )


def test_scope_before_counts_stable_pages_and_no_private_binding_fields(completed):
    output, run = completed
    who = actor(output, products=output.manifest.scope.product_ids[1:3])
    first = page(completed, ForecastQuery(limit=5), who)
    second = page(completed, ForecastQuery(limit=5, offset=5, view_sha256=first.view_sha256), who)
    full = page(completed, ForecastQuery(limit=200), who)
    assert first.pagination.total == 28 and first.pagination.next_offset == 5
    assert first.items + second.items == full.items[:10]
    assert {r.product_id for r in full.items} == who.product_ids
    assert all(
        r.inference_run_id == run.run_id and r.release_id == run.release_id for r in full.items
    )
    assert all(
        r.prediction_interval is None and r.interval_unavailable_reason == "not_published"
        for r in full.items
    )
    assert (
        "source_uri" not in full.model_dump_json()
        and "fixture-product-19" not in full.model_dump_json()
    )


def test_continuation_requires_same_query_principal_scope_and_result(completed):
    first = page(completed)
    with pytest.raises(ForecastReadError, match="view-required"):
        page(completed, ForecastQuery(offset=50))
    with pytest.raises(ForecastReadError, match="view-changed"):
        page(
            completed,
            ForecastQuery(
                offset=50, view_sha256=first.view_sha256, product_id="fixture-product-00"
            ),
        )
    out, _ = completed
    with pytest.raises(ForecastReadError, match="view-changed"):
        page(
            completed,
            ForecastQuery(view_sha256=first.view_sha256),
            actor(out, products=("fixture-product-00",)),
        )
    absent = projection(
        ForecastQuery(), actor(out), (), now=out.manifest.generated_at, unpublished={}
    )
    assert absent.data_status == "no_data" and absent.items == () and absent.pagination.total == 0
    with pytest.raises(ForecastReadError, match="output-not-found"):
        projection(
            ForecastQuery(inference_run_id="run-" + "b" * 32),
            actor(out),
            (),
            now=out.manifest.generated_at,
            unpublished={},
        )


def test_filter_bounds_and_date_selection_never_fall_back(completed):
    out, _ = completed
    origin = out.manifest.as_of_time
    selected = page(
        completed,
        ForecastQuery(
            product_id="fixture-product-00",
            as_of=origin,
            target_from=origin.date() + timedelta(days=2),
            target_to=origin.date() + timedelta(days=4),
        ),
    )
    assert selected.pagination.total == 3
    assert [r.horizon_days for r in selected.items] == [2, 3, 4]
    with pytest.raises(ForecastReadError, match="scope-invalid"):
        page(completed, ForecastQuery(product_id="outside"))
    broad = actor(out, products=tuple(f"p-{i}" for i in range(21)))
    with pytest.raises(ForecastReadError, match="scope-limit"):
        resolve_scope(ForecastQuery(), broad)
    assert resolve_scope(ForecastQuery(product_id="p-0"), broad).products == ("p-0",)


def test_later_old_replay_cannot_replace_newer_origin_or_trigger_date_fallback(completed):
    original, old_run = completed
    origin = original.manifest.as_of_time + timedelta(days=1)
    request = old_run.input_ref.request.model_copy(update={"as_of": origin})
    new_run = old_run.model_copy(
        update={
            "run_id": "run-" + "b" * 32,
            "requested_at": old_run.requested_at - timedelta(seconds=3),
            "input_ref": old_run.input_ref.model_copy(
                update={
                    "as_of_time": origin,
                    "request": request,
                    "request_hash": request.request_hash(),
                }
            ),
        }
    )
    parts = tuple(
        Partition(
            ordinal=p.ordinal,
            predictions=tuple(
                r.model_copy(
                    update={
                        "forecast_origin": origin,
                        "target_date": r.target_date + timedelta(days=1),
                        "predicted_units": 9.0,
                    }
                )
                for r in p.predictions
            ),
        )
        for p in original.partitions
    )
    rows = [r.model_dump(mode="json") for p in parts for r in p.predictions]
    raw = original.manifest.model_dump(mode="json", exclude={"artifact_id"})
    raw.update(
        as_of_time=origin.isoformat().replace("+00:00", "Z"),
        run_id=new_run.run_id,
        partitions=[receipt(p).model_dump(mode="json") for p in parts],
        predictions_sha256=canonical_sha256(rows),
    )
    raw["artifact_id"] = "predictions-sha256-" + canonical_sha256(raw)
    newer = Publication.model_validate_json(
        json.dumps({"manifest": raw, "partitions": [p.model_dump(mode="json") for p in parts]})
    )
    new_run = new_run.model_copy(
        update={
            "output_ref": RunOutput(
                kind="predictions", artifact_id=newer.manifest.artifact_id, complete=True
            )
        }
    )
    who = actor(original, products=("fixture-product-00",))
    result = projection(
        ForecastQuery(),
        who,
        ((newer, new_run), completed),
        now=original.manifest.generated_at,
        unpublished={},
    )
    assert result.pagination.total == 14 and all(r.predicted_units == 9.0 for r in result.items)
    target = original.manifest.as_of_time.date() + timedelta(days=1)
    empty = projection(
        ForecastQuery(target_from=target, target_to=target),
        who,
        (completed, (newer, new_run)),
        now=original.manifest.generated_at,
        unpublished={},
    )
    assert empty.data_status == "no_data"
    old = projection(
        ForecastQuery(as_of=original.manifest.as_of_time),
        who,
        (completed, (newer, new_run)),
        now=original.manifest.generated_at,
        unpublished={},
    )
    assert old.selection == "origin" and all(r.predicted_units == 2.0 for r in old.items)


@pytest.mark.parametrize(
    "raw",
    [
        {"limit": 201},
        {"limit": True},
        {"offset": 2801},
        {"role": "admin"},
        {"target_from": "2026-01-01"},
        {"target_from": "2026-01-02", "target_to": "2026-01-01"},
        {"target_from": "2026-01-01", "target_to": "2026-03-01"},
        {"as_of": "2026-01-01T12:00:00Z"},
        {"as_of": "2026-01-01T23:59:59+01:00"},
    ],
)
def test_closed_bounded_read_query(raw):
    with pytest.raises(ValidationError):
        ForecastQuery.model_validate_json(json.dumps(raw))


def test_freshness_uses_origin_and_failed_newer_attempt_not_replay_time(completed):
    out, run = completed
    origin = out.manifest.as_of_time
    # Controlled clock tests the exact policy boundary, independently of today's date.
    early = out.model_copy(
        update={"manifest": out.manifest.model_copy(update={"generated_at": origin})}
    )
    raw = early.model_dump(mode="json")

    raw["manifest"]["artifact_id"] = "predictions-sha256-" + canonical_sha256(
        {k: v for k, v in raw["manifest"].items() if k != "artifact_id"}
    )
    early = Publication.model_validate_json(json.dumps(raw))
    run = run.model_copy(
        update={
            "requested_at": origin,
            "started_at": origin,
            "completed_at": origin,
            "output_ref": RunOutput(
                artifact_id=early.manifest.artifact_id, kind="predictions", complete=True
            ),
        }
    )
    who = actor(out)
    current = projection(
        ForecastQuery(), who, ((early, run),), now=origin + timedelta(seconds=86400), unpublished={}
    )
    stale = projection(
        ForecastQuery(), who, ((early, run),), now=origin + timedelta(seconds=86401), unpublished={}
    )
    assert current.items[0].freshness.status == "unknown"
    assert current.items[0].freshness.reason == "source_watermark_unavailable"
    assert stale.items[0].freshness.reason == "origin_age_exceeded"
    assert current.view_sha256 == stale.view_sha256
    unresolved = {
        (
            "fixture-product-00",
            early.manifest.scope.selling_location_ids[0],
            early.manifest.scope.channel,
            h,
        ): (origin, origin + timedelta(seconds=1), "run-" + "f" * 32)
        for h in range(1, 8)
    }
    failed = projection(
        ForecastQuery(),
        who,
        ((early, run),),
        now=origin + timedelta(seconds=2),
        unpublished=unresolved,
    )
    assert sum(r.freshness.reason == "newer_run_unpublished" for r in failed.items) == 7
    assert sum(r.freshness.status == "unknown" for r in failed.items) == 43
    assert all(r.freshness.status == "stale" for r in page(completed).items)


def test_entire_publication_and_run_pins_validated_even_outside_page(completed):
    out, run = completed
    who = actor(out, products=("fixture-product-00",))
    last = out.partitions[-1]
    changed = last.model_copy(update={"predictions": last.predictions[:-1]})
    corrupt = out.model_copy(update={"partitions": (*out.partitions[:-1], changed)})
    with pytest.raises(ValueError):
        projection(
            ForecastQuery(limit=1),
            who,
            ((corrupt, run),),
            now=out.manifest.generated_at,
            unpublished={},
        )
    with pytest.raises(ValueError, match="pin_mismatch"):
        projection(
            ForecastQuery(),
            who,
            ((out, run.model_copy(update={"release_id": "model-release-sha256-" + "b" * 64})),),
            now=out.manifest.generated_at,
            unpublished={},
        )
    with pytest.raises(ForecastReadError, match="read-budget"):
        projection(
            ForecastQuery(), who, (completed,) * 33, now=out.manifest.generated_at, unpublished={}
        )


class Backend:
    def __init__(self, completed):
        self.completed, self.calls, self.fail = completed, [], False

    def read(self, query, principal):
        self.calls.append((query, principal))
        if self.fail:
            raise ValueError("private-secret-connection-path")
        return page(self.completed, query, principal)


def test_publication_time_precedes_commit_but_cannot_follow_run_completion(completed):
    out, run = completed
    later = run.model_copy(update={"completed_at": run.completed_at + timedelta(seconds=1)})
    result = projection(
        ForecastQuery(), actor(out), ((out, later),), now=later.completed_at, unpublished={}
    )
    assert result.items[0].generated_at == out.manifest.generated_at
    early = run.model_copy(update={"completed_at": run.completed_at - timedelta(milliseconds=500)})
    with pytest.raises(ValueError, match="pin_mismatch"):
        projection(
            ForecastQuery(), actor(out), ((out, early),), now=run.completed_at, unpublished={}
        )


def test_real_http_contract_identity_filters_and_dependency_failure(tmp_path, completed):
    out, _ = completed

    def mutate(value):
        scope = out.manifest.scope
        value["grants"][0]["scope"] = {
            "product_ids": [scope.product_ids[0]],
            "selling_location_ids": list(scope.selling_location_ids),
            "channels": [scope.channel],
        }

    path, tokens = policy_file(tmp_path, mutate)
    backend = Backend(completed)
    with TestClient(
        create_app(
            Settings(APP_ENV="test", ARTIFACT_ROOT="./artifacts", API_AUTH_FILE=path),
            forecast_reader=backend,
        ),
        base_url="http://127.0.0.1",
    ) as c:
        uri = "/api/v1/forecasts"
        problem(c.get(uri), 401)
        problem(c.get(uri, headers=bearer(tokens["local-admin"])), 403)
        assert backend.calls == []
        headers = bearer(tokens["local-viewer"])
        first = c.get(uri, headers=headers, params={"limit": 2})
        assert first.status_code == 200 and first.json()["pagination"]["total"] == 14
        assert (
            c.get(
                uri,
                headers=headers,
                params={"limit": 2, "offset": 2, "view_sha256": first.json()["view_sha256"]},
            ).status_code
            == 200
        )
        for params in (
            {"user_id": "local-admin"},
            {"target_from": "2026-01-01"},
            {"target_from": "2026-01-01T00:00:00Z", "target_to": "2026-01-01"},
            {"as_of": "2026-01-01T23:59:59+01:00"},
            {"limit": 201},
        ):
            problem(c.get(uri, headers=headers, params=params), 422)
        problem(c.get(uri + "?channel=store&channel=online", headers=headers), 422)
        assert c.get("/ready").status_code == 200
        backend.fail = True
        response = c.get(uri, headers=headers)
        problem(response, 503)
        assert response.json()["code"] == "forecast-output-invalid"
        assert "private-secret" not in response.text


def test_absent_reader_is_unavailable_after_authentication(tmp_path):
    path, tokens = policy_file(tmp_path)
    with TestClient(
        create_app(Settings(APP_ENV="test", ARTIFACT_ROOT="./artifacts", API_AUTH_FILE=path)),
        base_url="http://127.0.0.1",
    ) as c:
        problem(c.get("/api/v1/forecasts", headers=bearer(tokens["local-viewer"])), 503)
