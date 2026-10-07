"""Frozen release/input replay and status honesty; synthetic mechanics only."""

from datetime import timedelta

import pytest
from pydantic import ValidationError
from test_model_lifecycle import actor
from test_stockout_conditional_runtime import conditional as conditional
from test_stockout_lifecycle import backend as backend
from test_stockout_lifecycle import register, request, sealed
from test_stockout_lifecycle import source as source
from test_stockout_runtime import context as context
from test_stockout_runtime import records as records

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.forecast_jobs.contracts import QueuePolicy
from retailops_ai.stockout.upstream_contract import UpstreamPoint
from retailops_ai.stockout_jobs.batch import StockoutOutput, compute, verify_output
from retailops_ai.stockout_jobs.contracts import (
    StockoutInputRef,
    StockoutRequest,
    StockoutRun,
    public_run,
)
from retailops_ai.stockout_runtime.inputs import PhysicalScope, PreparedStockoutInputs


@pytest.fixture
def job(backend, conditional):
    lifecycle, registry, journal = backend
    version = register(backend, "batch-one")
    lifecycle.execute(request(registry, "promote", "batch-promote-one", version), actor())
    release = journal.active(registry.original.model_name)
    feature, kw = conditional
    scope = dict(product_ids=[feature.product_id], stock_location_ids=[feature.stock_location_id])
    upstream = UpstreamPoint(
        product_id=feature.product_id,
        stock_location_id=feature.stock_location_id,
        as_of=feature.as_of,
        forecast_origin=feature.as_of.replace(microsecond=0),
        training_cutoff=feature.as_of.replace(microsecond=0),
        selection_cutoff=feature.as_of.replace(microsecond=0),
        source_available_at=None,
        upstream_model_version="baseline-sha256-" + "0" * 64,
        status="insufficient_data",
        reason="explicit_mechanics_fixture",
        forecast_units_7d=None,
        series=(),
    )
    lineage = kw["lineage"].model_dump(mode="json")
    lineage.update(source_watermark=None, source_completeness_status="unavailable")
    inputs = sealed(
        PreparedStockoutInputs,
        "inputs_id",
        "stockout-inputs-sha256-",
        dict(
            version="stockout-prepared-inputs-1.0.0",
            input_role="inference_public_facts_only",
            scope=scope,
            as_of=feature.model_dump(mode="json")["as_of"],
            points=[
                dict(
                    feature=feature.model_dump(mode="json"),
                    category_id=kw["category_id"],
                    category_available_at=kw["category_available_at"]
                    .isoformat()
                    .replace("+00:00", "Z"),
                    upstream=upstream.model_dump(mode="json"),
                )
            ],
            lineage=lineage,
            source_parent_files_sha256="0" * 64,
            preparation_code_sha256="1" * 64,
            source_freshness_evidence="curated_does_not_supply_a_global_source_watermark",
            parent_replay="complete_public_features_and_upstream",
        ),
    )
    request_body = StockoutRequest(
        profile_id=inputs.inputs_id, as_of=inputs.as_of, scope=inputs.scope
    )
    run = StockoutRun(
        run_id="run-" + "0" * 32,
        status="running",
        attempt=1,
        requested_at=release.binding.approval.reviewed_at + timedelta(seconds=1),
        requested_by="pipeline",
        started_at=release.binding.approval.reviewed_at + timedelta(seconds=2),
        input_ref=StockoutInputRef(
            request=request_body, request_sha256=request_body.request_hash(), lineage=inputs.lineage
        ),
        release=release,
        environment="test",
        policy=QueuePolicy(),
    )
    return run, inputs, release, release.binding.approval.reviewed_at + timedelta(seconds=3)


def test_complete_physical_batch_replays_exact_pins_and_public_projection_omits_approval_package(
    job,
):
    run, inputs, release, now = job
    output = compute(run, inputs, release, generated_at=now)
    verify_output(run, inputs, release, output)
    assert output.model_refits == 0 and not output.source_generation
    assert len(output.items) == 1 and output.items[0].quality_status == "mechanics_only"
    assert output.items[0].freshness_status == "unknown"
    raw = run.model_dump(mode="json")
    raw.update(
        status="succeeded",
        completed_at=output.model_dump(mode="json")["generated_at"],
        output_id=output.output_id,
    )
    done = StockoutRun.model_validate_json(canonical_bytes(raw))
    public = public_run(done).model_dump(mode="json")
    assert public["publication_status"] == "published"
    assert public["resolved_model"]["release"]["release_id"] == release.release_id
    assert (
        "approval" not in public["resolved_model"]
        and "qualification" not in public["resolved_model"]
    )


@pytest.mark.parametrize("change", ["profile", "source", "scope", "release", "origin"])
def test_inputs_or_release_cannot_change_after_the_job_is_accepted(job, change):
    run, inputs, release, now = job
    if change == "profile":
        run = run.model_copy(
            update={
                "input_ref": run.input_ref.model_copy(
                    update={
                        "request": run.input_ref.request.model_copy(
                            update={"profile_id": "stockout-inputs-sha256-" + "f" * 64}
                        )
                    }
                )
            }
        )
    elif change == "source":
        run = run.model_copy(
            update={
                "input_ref": run.input_ref.model_copy(
                    update={
                        "lineage": inputs.lineage.model_copy(
                            update={"source_dataset_id": "source-sha256-" + "f" * 64}
                        )
                    }
                )
            }
        )
    elif change == "scope":
        request_body = run.input_ref.request.model_copy(
            update={"scope": inputs.scope.model_copy(update={"product_ids": ("other",)})}
        )
        run = run.model_copy(
            update={
                "input_ref": run.input_ref.model_copy(
                    update={"request": request_body, "request_sha256": request_body.request_hash()}
                )
            }
        )
    elif change == "release":
        release = release.model_copy(update={"image_digest": "sha256:" + "f" * 64})
    else:
        request_body = run.input_ref.request.model_copy(
            update={"as_of": inputs.as_of - timedelta(days=1)}
        )
        run = run.model_copy(
            update={
                "input_ref": run.input_ref.model_copy(
                    update={"request": request_body, "request_sha256": request_body.request_hash()}
                )
            }
        )
    with pytest.raises(ValueError):
        compute(run, inputs, release, generated_at=now)


def test_resealed_probability_cannot_be_published_without_exact_replay(job):
    run, inputs, release, now = job
    output = compute(run, inputs, release, generated_at=now)
    body = output.model_dump(mode="json", exclude={"output_id"})
    body["items"][0]["probability"] = 0.0
    body["items"][0]["risk_band"] = "low"
    changed = sealed(StockoutOutput, "output_id", "stockout-output-sha256-", body)
    with pytest.raises(ValueError, match="complete_replay"):
        verify_output(run, inputs, release, changed)


@pytest.mark.parametrize("state", ["queued", "succeeded", "failed"])
def test_worker_cannot_compute_for_non_running_state(job, state):
    run, inputs, release, now = job
    with pytest.raises(ValueError):
        compute(run.model_copy(update={"status": state}), inputs, release, generated_at=now)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_generation", 0),
        ("source_generation", "false"),
        ("model_refits", True),
        ("model_refits", False),
        ("complete", 1),
    ],
)
def test_resealed_output_cannot_coerce_execution_boundary_flags(job, field, value):
    run, inputs, release, now = job
    body = compute(run, inputs, release, generated_at=now).model_dump(
        mode="json", exclude={"output_id"}
    )
    body[field] = value
    with pytest.raises(ValidationError):
        sealed(StockoutOutput, "output_id", "stockout-output-sha256-", body)


def test_job_request_hash_uses_physical_scope_and_no_forecast_channel():
    a = StockoutRequest(
        profile_id="stockout-inputs-sha256-" + "0" * 64,
        as_of="2026-07-13T23:59:59.999999Z",
        scope=PhysicalScope(product_ids=("b", "a"), stock_location_ids=("stock2", "stock1")),
    )
    b = a.model_copy(
        update={
            "scope": PhysicalScope(product_ids=("a", "b"), stock_location_ids=("stock1", "stock2"))
        }
    )
    assert a.request_hash() == b.request_hash()
    for field in ("channel", "selling_location_ids", "features", "private_root"):
        with pytest.raises(ValidationError):
            StockoutRequest.model_validate_json(
                canonical_bytes({**a.model_dump(mode="json"), field: "forbidden"})
            )
