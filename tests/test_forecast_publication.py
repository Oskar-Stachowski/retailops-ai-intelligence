"""Content, authorization and worker wiring; qualifications here are explicit test stubs."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from psycopg.errors import LockNotAvailable, QueryCanceled
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecast_jobs import worker
from retailops_ai.forecast_jobs.contracts import (
    BatchInput,
    BatchRequest,
    BatchRun,
    BatchScope,
    QueuePolicy,
)
from retailops_ai.forecast_jobs.inputs import PreparedInputs, scoped_inputs
from retailops_ai.forecast_jobs.publication import Publication, publication
from retailops_ai.forecast_jobs.publication_acceptance import (
    competing_completion,
    competing_submission,
    expanded_fixture,
    result_for,
    stub_release,
)
from retailops_ai.forecast_jobs.queue import Claim, LeaseLost


@pytest.mark.parametrize("busy", [False, True])
def test_duplicate_publisher_can_lose_lease_or_bounded_lock_wait(claim, busy):
    class Duplicate:
        def complete_forecast(self, *_):
            if busy:
                raise OperationalError("private SQL", {}, LockNotAvailable())
            raise LeaseLost("batch_lease_lost")

    assert competing_completion(Duplicate(), claim) == (
        "publication_busy" if busy else "lease_lost"
    )


def test_duplicate_publisher_does_not_hide_other_database_failures(claim):
    class Broken:
        def complete_forecast(self, *_):
            raise OperationalError("private SQL", {}, QueryCanceled())

    with pytest.raises(OperationalError):
        competing_completion(Broken(), claim)


@pytest.mark.parametrize("unexpected", [False, True])
def test_duplicate_admission_accepts_only_bounded_lock_contention(claim, unexpected):
    class Contended:
        def submit(self, *_):
            error = QueryCanceled() if unexpected else LockNotAvailable()
            raise OperationalError("private SQL", {}, error)

    if unexpected:
        with pytest.raises(OperationalError):
            competing_submission(Contended(), None, None, "same-key")
    else:
        assert competing_submission(Contended(), None, None, "same-key") is None


@pytest.fixture(scope="module")
def inputs():
    return expanded_fixture(
        PreparedInputs.model_validate_json(
            Path("contracts/forecast_jobs/v1/fixture/inputs.json").read_bytes()
        )
    )


@pytest.fixture
def claim(inputs):
    release = stub_release(inputs, "sha256:" + "a" * 64)
    request = BatchRequest(
        profile_id=inputs.profile_id, as_of=inputs.as_of_time, channel=inputs.scope.channel
    )
    parent = inputs.feature_manifest.descriptor.parent
    run = BatchRun(
        schema_version="1.0",
        contract_type="run",
        run_id="run-" + "a" * 32,
        status="running",
        attempt=1,
        requested_at=datetime.now(UTC),
        requested_by="fixture-pipeline",
        started_at=datetime.now(UTC),
        completed_at=None,
        output_ref=None,
        error=None,
        input_ref=BatchInput(
            source_dataset_id=parent.source_dataset_id,
            curated_dataset_id=parent.curated_dataset_id,
            feature_set_id=inputs.feature_manifest.feature_set_id,
            as_of_time=inputs.as_of_time,
            profile_id=inputs.profile_id,
            request_hash=request.request_hash(),
            request=request,
            scope=inputs.scope,
        ),
        resolved_model=release.binding,
        release_id=release.release_id,
        image_digest=release.image_digest,
        environment="test",
        purpose="qualified_forecast",
        policy=QueuePolicy(),
    )
    return Claim(run, inputs, "00000000-0000-0000-0000-000000000001", release)


def test_scoped_derivation_preserves_parent_and_never_sends_outside_rows(inputs):
    scope = BatchScope(
        product_ids=(inputs.scope.product_ids[3],),
        selling_location_ids=inputs.scope.selling_location_ids,
        channel=inputs.scope.channel,
    )
    small = scoped_inputs(inputs, scope, 7)
    assert len(small.rows) == 7 and len(small.histories) == 1
    assert small.profile_id != inputs.profile_id
    assert small.feature_manifest == inputs.feature_manifest
    assert scoped_inputs(inputs, scope, 7) == small
    assert {r.product_id for r in small.rows} == set(scope.product_ids)
    assert scoped_inputs(inputs, inputs.scope, 14) == inputs
    with pytest.raises(ValueError, match="uncovered"):
        scoped_inputs(inputs, scope.model_copy(update={"product_ids": ("unauthorized",)}), 7)
    with pytest.raises(ValueError, match="uncovered"):
        scoped_inputs(small, scope, 14)


def test_complete_partitioned_publication_has_pins_grain_and_content_identity(claim):
    output = publication(claim.run, claim.profile, result_for(claim), datetime.now(UTC))
    assert [len(p.predictions) for p in output.partitions] == [256, 24]
    assert output.manifest.row_count == 280
    assert output.manifest.resolved_model == claim.run.resolved_model
    assert output.manifest.profile_id == claim.profile.profile_id
    assert Publication.model_validate_json(output.model_dump_json()) == output


@pytest.mark.parametrize(
    "change", ["missing_partition", "duplicate", "date", "negative", "nan", "receipt", "identity"]
)
def test_changed_partial_or_invalid_output_is_refused_even_with_rehashed_manifest(claim, change):
    raw = publication(claim.run, claim.profile, result_for(claim), datetime.now(UTC)).model_dump(
        mode="json"
    )
    if change == "missing_partition":
        raw["partitions"].pop()
    elif change == "duplicate":
        raw["partitions"][0]["predictions"][1] = raw["partitions"][0]["predictions"][0]
    elif change == "date":
        raw["partitions"][0]["predictions"][0]["target_date"] = "2099-01-01"
    elif change in {"negative", "nan"}:
        raw["partitions"][0]["predictions"][0]["predicted_units"] = (
            -1.0 if change == "negative" else float("nan")
        )
    elif change == "receipt":
        raw["manifest"]["partitions"][0]["sha256"] = "a" * 64
    else:
        raw["manifest"]["artifact_id"] = "predictions-sha256-" + "a" * 64
    if change != "identity":
        raw["manifest"]["artifact_id"] = "predictions-sha256-" + canonical_sha256(
            {k: v for k, v in raw["manifest"].items() if k != "artifact_id"}
        )
    with pytest.raises(ValidationError):
        Publication.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("change", ["preflight", "release", "profile", "count", "lineage"])
def test_result_cannot_publish_for_other_run_input_or_preflight(claim, change):
    result = result_for(claim)
    run = claim.run
    if change == "preflight":
        result = result.model_copy(update={"purpose": "runtime_preflight_only"})
    elif change == "release":
        result = result.model_copy(update={"release_id": "model-release-sha256-" + "b" * 64})
    elif change == "profile":
        result = result.model_copy(update={"profile_id": "batch-profile-sha256-" + "b" * 64})
    elif change == "count":
        values = result.quantities[:-1]
        result = result.model_copy(
            update={"quantities": values, "quantities_sha256": canonical_sha256(values)}
        )
    else:
        run = run.model_copy(
            update={
                "input_ref": run.input_ref.model_copy(
                    update={"source_dataset_id": "source-sha256-" + "b" * 64}
                )
            }
        )
    with pytest.raises(ValueError):
        publication(run, claim.profile, result, datetime.now(UTC))


class Queue:
    environment = "test"
    mechanics = False

    def __init__(self):
        self.completed = []
        self.failed = []
        self.heartbeats = 0

    def execution_budget(self, claim):
        return 17.0

    def heartbeat(self, claim):
        self.heartbeats += 1

    def complete_forecast(self, claim, result):
        self.completed.append(result)

    def fail(self, claim, **kwargs):
        self.failed.append(kwargs)


def test_qualified_worker_connects_subset_budget_heartbeat_and_pinned_result(monkeypatch, claim):
    queue = Queue()
    captured = []

    def compute(request, *, on_tick):
        captured.append(request)
        on_tick()
        on_tick()
        return result_for(claim)

    monkeypatch.setattr(worker, "supervise", compute)
    assert (
        worker.run_forecast_attempt(
            queue, claim, compose=False, image_digest=claim.run.image_digest
        )
        == "succeeded"
    )
    assert queue.heartbeats == 1 and len(queue.completed) == 1 and not queue.failed
    assert captured[0].limits.wall_seconds == pytest.approx(16.8)
    assert captured[0].purpose == "qualified_forecast_computation"
    assert captured[0].release == claim.release


def test_worker_lost_lease_never_writes_failure_or_result(monkeypatch, claim):
    queue = Queue()

    def lost(claim):
        raise LeaseLost("lost")

    def compute(request, *, on_tick):
        on_tick()
        pytest.fail("computation continued after lost lease")

    monkeypatch.setattr(queue, "heartbeat", lost)
    monkeypatch.setattr(worker, "supervise", compute)
    assert (
        worker.run_forecast_attempt(
            queue, claim, compose=False, image_digest=claim.run.image_digest
        )
        == "lease_lost"
    )
    assert not queue.completed and not queue.failed


def test_wrong_runtime_image_publishes_nothing(monkeypatch, claim):
    queue = Queue()

    def compute(*args, **kwargs):
        pytest.fail("wrong image entered computation")

    monkeypatch.setattr(worker, "supervise", compute)
    assert (
        worker.run_forecast_attempt(queue, claim, compose=False, image_digest="sha256:" + "b" * 64)
        == "failed"
    )
    assert not queue.completed and len(queue.failed) == 1


def test_compute_failure_never_calls_publication_and_closes_attempt(monkeypatch, claim):
    queue = Queue()

    def failed(*args, **kwargs):
        raise ValueError("runtime_execution_child_failed")

    monkeypatch.setattr(worker, "supervise", failed)
    assert (
        worker.run_forecast_attempt(
            queue, claim, compose=False, image_digest=claim.run.image_digest
        )
        == "failed"
    )
    assert not queue.completed
    assert queue.failed == [{"reason": "qualified_executor_failed", "retryable": True}]
