"""Clock, completeness, leakage and backwards-identity tests; no real qualification."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from test_forecast_publication import claim, inputs  # noqa: F401
from test_forecast_read import actor

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.data_contracts.run import RunOutput
from retailops_ai.forecast_jobs.contracts import BatchScope
from retailops_ai.forecast_jobs.freshness_fixture import fixture
from retailops_ai.forecast_jobs.inputs import PreparedInputs, scoped_inputs
from retailops_ai.forecast_jobs.publication import Publication, publication
from retailops_ai.forecast_jobs.publication_acceptance import result_for
from retailops_ai.forecast_jobs.queue import Claim
from retailops_ai.forecast_jobs.read_contracts import ForecastQuery
from retailops_ai.forecast_jobs.reader import projection, verify_source_freshness

ORIGIN = datetime(2026, 9, 28, 23, 59, 59, tzinfo=UTC)


@pytest.fixture
def factory(request):
    claimed = request.getfixturevalue("claim")
    base = scoped_inputs(
        claimed.profile,
        BatchScope(
            product_ids=claimed.profile.scope.product_ids[:1],
            selling_location_ids=claimed.profile.scope.selling_location_ids,
            channel=claimed.profile.scope.channel,
        ),
        14,
    )

    def make(**kwargs):
        options = dict(complete_through=ORIGIN.date())
        options.update(kwargs)
        profile = fixture(base, ORIGIN, **options)
        parent = profile.feature_manifest.descriptor.parent
        request = claimed.run.input_ref.request.model_copy(
            update={"profile_id": profile.profile_id, "as_of": ORIGIN}
        )
        ref = claimed.run.input_ref.model_copy(
            update={
                "profile_id": profile.profile_id,
                "as_of_time": ORIGIN,
                "scope": profile.scope,
                "source_dataset_id": parent.source_dataset_id,
                "curated_dataset_id": parent.curated_dataset_id,
                "feature_set_id": profile.feature_manifest.feature_set_id,
                "request": request,
                "request_hash": request.request_hash(),
            }
        )
        run = claimed.run.model_copy(
            update={
                "input_ref": ref,
                "requested_at": ORIGIN + timedelta(seconds=1),
                "started_at": ORIGIN + timedelta(seconds=2),
            }
        )
        candidate = Claim(run, profile, claimed.token, claimed.release)
        output = publication(run, profile, result_for(candidate), ORIGIN + timedelta(seconds=3))
        completed = run.model_copy(
            update={
                "status": "succeeded",
                "completed_at": ORIGIN + timedelta(seconds=3),
                "output_ref": RunOutput(
                    artifact_id=output.manifest.artifact_id, kind="predictions", complete=True
                ),
            }
        )
        return profile, output, completed

    return make


def read(output, run, *, seconds=3, **kwargs):
    return projection(
        ForecastQuery(),
        actor(output),
        ((output, run),),
        now=ORIGIN + timedelta(seconds=seconds),
        unpublished=kwargs.get("unpublished", {}),
    )


def test_current_requires_declared_completeness_and_cutoff_observation_and_exact_clock(factory):
    profile, out, run = factory()
    result = read(out, run, seconds=86400)
    f = result.items[0].freshness
    assert f.status == "current" and f.reason == "within_policy"
    assert f.source_watermark == ORIGIN and f.source_watermark_as_of == ORIGIN + timedelta(
        seconds=1
    )
    assert f.source_watermark_age_seconds == 86400 and f.source_watermark_origin_lag_seconds == 0
    assert f.observation_lag_days == 0
    old = read(out, run, seconds=86401)
    assert old.items[0].freshness.reason == "origin_age_exceeded"
    assert result.view_sha256 == old.view_sha256
    assert "curated_descriptor" not in result.model_dump_json()
    assert out.manifest.source_freshness == profile.source_freshness


@pytest.mark.parametrize(
    "options,status,reason",
    [
        (
            {"complete_through": ORIGIN.date() - timedelta(days=1)},
            "stale",
            "source_watermark_lag_exceeded",
        ),
        ({"complete_through": None}, "unknown", "source_watermark_not_ready"),
        ({"omit_watermark": True}, "unknown", "source_watermark_unavailable"),
        (
            {"policy_version": "daily-demand-unqualified-v99"},
            "unknown",
            "source_watermark_policy_unsupported",
        ),
        ({"missing_history_days": 2}, "stale", "source_observation_lag_exceeded"),
    ],
)
def test_optimistic_latest_sale_or_replay_does_not_replace_source_declaration(
    factory, options, status, reason
):
    _, out, run = factory(**options)
    f = read(out, run).items[0].freshness
    assert (f.status, f.reason) == (status, reason)
    if options.get("missing_history_days"):
        assert f.source_watermark == ORIGIN and f.observation_lag_days == 2
    if reason == "source_watermark_lag_exceeded":
        assert f.source_watermark_origin_lag_seconds == 86400 and f.observation_lag_days == 0


def test_failed_newer_attempt_keeps_identical_prediction_view_but_is_stale(factory):
    _, out, run = factory()
    base = read(out, run)
    missing = {
        (r.product_id, r.selling_location_id, r.channel, r.horizon_days): (
            ORIGIN,
            run.requested_at + timedelta(seconds=1),
            "run-" + "f" * 32,
        )
        for r in base.items
    }
    failed = read(out, run, unpublished=missing)
    assert all(r.freshness.reason == "newer_run_unpublished" for r in failed.items)
    assert base.view_sha256 == failed.view_sha256


@pytest.mark.parametrize(
    "change", ["declaration", "descriptor", "observation", "version", "cutoff"]
)
def test_rehashed_freshness_cannot_claim_other_parent_availability_or_version(factory, change):
    profile, _, _ = factory(missing_origin=True)
    raw = profile.model_dump(mode="json")
    f = raw["source_freshness"]
    if change == "declaration":
        f["watermark"]["complete_through"] = "2026-09-27"
    elif change == "descriptor":
        f["curated_descriptor"]["watermarks"]["daily_demand_observations"]["complete_through"] = (
            "2026-09-27"
        )
        f["watermark"]["complete_through"] = "2026-09-27"
    elif change == "observation":
        f["observations"][0]["latest_complete_observation_date"] = "2026-09-28"
    elif change == "cutoff":
        f["as_of_time"] = "2026-09-29T23:59:59Z"
    else:
        raw["schema_version"] = "1.0"
    raw["profile_id"] = "batch-profile-sha256-" + canonical_sha256(
        {k: v for k, v in raw.items() if k != "profile_id"}
    )
    with pytest.raises(ValidationError):
        PreparedInputs.model_validate_json(json.dumps(raw))


def test_old_fixture_identity_serialization_and_new_conditional_json_schema(factory):
    raw = Path("contracts/forecast_jobs/v1/fixture/inputs.json").read_bytes()
    old = PreparedInputs.model_validate_json(raw)
    assert old.model_dump(mode="json") == json.loads(raw)
    assert old.profile_id == "batch-profile-sha256-" + canonical_sha256(
        {k: v for k, v in json.loads(raw).items() if k != "profile_id"}
    )
    assert old.source_freshness is None and "source_freshness" not in old.model_dump_json()
    new, out, _ = factory()
    Draft202012Validator(PreparedInputs.model_json_schema()).validate(new.model_dump(mode="json"))
    Draft202012Validator(PreparedInputs.model_json_schema()).validate(old.model_dump(mode="json"))
    Draft202012Validator(Publication.model_json_schema()).validate(out.model_dump(mode="json"))
    broken = new.model_dump(mode="json")
    del broken["source_freshness"]
    assert not Draft202012Validator(PreparedInputs.model_json_schema()).is_valid(broken)


def test_scoped_metadata_carries_only_selected_observation_keys(request):
    profile_value = request.getfixturevalue("inputs")
    full = fixture(profile_value, ORIGIN, complete_through=ORIGIN.date())
    scope = full.scope.model_copy(update={"product_ids": full.scope.product_ids[2:4]})
    small = scoped_inputs(full, scope, 7)
    assert len(small.source_freshness.observations) == 2
    assert {r.product_id for r in small.source_freshness.observations} == set(scope.product_ids)
    assert small.source_freshness.curated_descriptor == full.source_freshness.curated_descriptor
    assert small.profile_id != full.profile_id and small.schema_version == "1.1"


@pytest.mark.parametrize("closed", [False, True])
def test_confirmed_zero_and_closed_day_are_complete_observations(factory, closed):
    profile, out, run = factory(origin_closed=closed)
    point = profile.histories[0].points[-1]
    assert point.observed_units == 0 and point.source_data_complete
    assert point.status == ("closed" if closed else "observed_zero")
    assert read(out, run).items[0].freshness.status == "current"


def test_daily_close_availability_allows_previous_day_but_never_changes_cutoff(factory):
    profile, out, run = factory(missing_origin=True)
    result = read(out, run).items[0].freshness
    assert result.status == "current" and result.observation_lag_days == 1
    assert profile.histories[0].points[-1].status == "missing"
    assert result.latest_complete_observation_date == ORIGIN.date() - timedelta(days=1)


def test_rehashed_output_is_still_bound_to_registered_watermark_evidence(factory):
    profile, out, _ = factory()
    raw = out.model_dump(mode="json")
    raw["manifest"]["source_freshness"]["observations"][0]["latest_complete_observation_date"] = (
        "2026-09-27"
    )
    raw["manifest"]["artifact_id"] = "predictions-sha256-" + canonical_sha256(
        {k: v for k, v in raw["manifest"].items() if k != "artifact_id"}
    )
    changed = Publication.model_validate_json(json.dumps(raw))
    with pytest.raises(ValueError, match="source_freshness_pin_mismatch"):
        verify_source_freshness(
            changed.manifest,
            profile.schema_version,
            profile.source_freshness.model_dump(mode="json"),
        )


def test_watermark_of_later_archive_days_is_clipped_at_historical_origin(factory):
    from retailops_ai.forecast_jobs.freshness import freshness
    from retailops_ai.forecast_jobs.source_freshness import SourceFreshness

    profile, out, _ = factory()
    raw = profile.source_freshness.model_dump(mode="json")
    declaration = raw["curated_descriptor"]["watermarks"]["daily_demand_observations"]
    declaration.update(complete_through="2026-09-30", as_of_time="2026-10-01T00:00:00Z")
    raw["watermark"] = declaration
    evidence = SourceFreshness.model_validate_json(json.dumps(raw))
    result = freshness(
        out.partitions[0].predictions[0],
        evidence,
        now=ORIGIN + timedelta(days=3),
        newer_unpublished=False,
    )
    assert result.source_watermark == ORIGIN and result.source_watermark_origin_lag_seconds == 0
    assert result.status == "stale" and result.reason == "origin_age_exceeded"


def test_reviewable_new_fixture_has_pinned_identity_and_separate_cutoff_availability():
    path = Path("contracts/forecast_jobs/v1/fixture/watermark-inputs.json")
    profile = PreparedInputs.model_validate_json(path.read_bytes())
    assert profile.schema_version == "1.1"
    assert profile.source_freshness.curated_descriptor["purpose"] == "sql_clock_fixture_only"
    assert profile.source_freshness.watermark.complete_through == ORIGIN.date()
