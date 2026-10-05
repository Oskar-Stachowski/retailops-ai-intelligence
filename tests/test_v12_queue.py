"""V12 chunk/receipt/worker boundaries on explicit small source/export/predictor doubles."""

import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_v12_inference import IMAGE, inference_result
from test_v12_lifecycle import artifacts as artifacts
from test_v12_lifecycle import inputs as inputs
from test_v12_lifecycle import loaded as loaded
from test_v12_lifecycle import prepared_input as prepared_input
from test_v12_lifecycle import qualification as qualification
from test_v12_lifecycle import serving as serving
from test_v12_lifecycle import tables as tables
from test_v12_lifecycle import timeline as timeline

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs import v12_batch, v12_worker
from retailops_ai.forecast_jobs.contracts import BatchInput, BatchRequest, BatchScope, QueuePolicy
from retailops_ai.forecast_jobs.inputs import InputContent, prepared
from retailops_ai.forecast_jobs.queue import BatchError, LeaseLost
from retailops_ai.forecast_jobs.source_freshness import observations
from retailops_ai.forecast_jobs.v12_batch import (
    V12BatchReceipt,
    V12BatchRun,
    chunks,
    computation_receipt,
    verify_receipt,
)
from retailops_ai.forecast_jobs.v12_queue import (
    PostgresV12Queue,
    V12Claim,
    authorize_read,
    transition,
)
from retailops_ai.model_lifecycle.v12_lifecycle_contracts import (
    TEST_MODEL,
    V12Binding,
    capsule_names,
    database_release,
)
from retailops_ai.security.model_operator import model_operator, private_principal


def expanded(inputs, products=20, locations=2):
    """Invented repeated series for batching only; not an accepted feature package."""
    scope = BatchScope(
        product_ids=tuple(f"p-{n:02d}" for n in range(products)),
        selling_location_ids=tuple(f"s-{n:02d}" for n in range(locations)),
        channel="store",
    )
    histories, rows = [], []
    for p in scope.product_ids:
        for loc in scope.selling_location_ids:
            h = inputs.histories[0].model_copy(
                update=dict(
                    product_id=p,
                    selling_location_id=loc,
                    points=tuple(
                        point.model_copy(update=dict(product_id=p, selling_location_id=loc))
                        for point in inputs.histories[0].points
                    ),
                )
            )
            histories.append(h)
            rows.extend(
                row.model_copy(
                    update=dict(
                        product_id=p,
                        selling_location_id=loc,
                        history_context_sha256=h.content_sha256(),
                    )
                )
                for row in inputs.rows
            )
    freshness = inputs.source_freshness.model_copy(
        update=dict(observations=observations(tuple(histories)))
    )
    return prepared(
        InputContent(
            schema_version="1.1",
            source_freshness=freshness,
            feature_manifest=inputs.feature_manifest,
            as_of_time=inputs.as_of_time,
            scope=scope,
            horizon_days=14,
            rows=tuple(rows),
            histories=tuple(histories),
        )
    )


def release_for(loaded):
    approval = loaded.release
    files = {name: dict(sha256="a" * 64, size_bytes=1) for name in capsule_names()}
    files["release.json"]["sha256"] = "b" * 64
    binding = V12Binding.model_validate_json(
        canonical_bytes(
            dict(
                model_name=TEST_MODEL,
                mlflow_run_id="1" * 32,
                campaign_mlflow_run_id="2" * 32,
                source_uri="mlflow-artifacts:/1/unit/v12-release",
                approval_sha256="b" * 64,
                approval=approval.model_dump(mode="json"),
                files=files,
                model_version="1",
            )
        )
    )
    return database_release(
        decision_id="decision-v12-queue-fixture",
        binding=binding.model_dump(mode="json"),
        image_digest=IMAGE,
        previous_release_id=None,
        previous_version=None,
        restored_from_release_id=None,
    )


def actor(inputs):
    return Principal(
        principal_id="unit-pipeline",
        roles=frozenset({"pipeline"}),
        capabilities=frozenset({"forecast:run"}),
        product_ids=frozenset(inputs.scope.product_ids),
        selling_location_ids=frozenset(inputs.scope.selling_location_ids),
        channels=frozenset({"store"}),
    )


def running(inputs, release, *, status="running", **changes):
    request = BatchRequest(profile_id=inputs.profile_id, as_of=inputs.as_of_time, channel="store")
    parent = inputs.feature_manifest.descriptor.parent
    raw = dict(
        run_id="run-" + "a" * 32,
        status=status,
        attempt=1,
        requested_at=datetime.now(UTC) - timedelta(seconds=2),
        requested_by="unit-pipeline",
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
        image_digest=IMAGE,
        environment="test",
        policy=QueuePolicy(),
        started_at=datetime.now(UTC) - timedelta(seconds=1) if status == "running" else None,
    )
    raw.update(changes)
    return V12BatchRun(**raw)


class MemoryQueue:
    def __init__(self):
        self.completed, self.failed, self.heartbeats = [], [], 0
        self.lost_at = None
        self.budget = 120.0

    def heartbeat(self, claim):
        self.heartbeats += 1
        if self.lost_at == self.heartbeats:
            raise LeaseLost("explicit_fixture_lease_lost")

    def execution_budget(self, claim):
        return self.budget

    def complete(self, claim, output, *, tick=None):
        verify_receipt(claim.run, claim.profile, claim.release, output, tick=tick)
        self.completed.append(output)

    def fail(self, claim, **kwargs):
        kwargs["exception"] = str(sys.exception())
        self.failed.append(kwargs)


@pytest.fixture
def batch(serving, inputs):
    _, model = serving
    profile = expanded(inputs)
    release = release_for(model)
    run = running(profile, release)
    claim = V12Claim(
        run, profile, release, "private-token", datetime.now(UTC) + timedelta(seconds=120)
    )
    return claim, model


def test_split_full_rectangles_and_complete_worker_receipt(batch, monkeypatch):
    claim, model = batch
    parts = chunks(claim.profile, claim.profile.scope, 14, claim.release)
    assert [len(p.rows) for p in parts] == [252, 28, 252, 28]
    assert parts == chunks(claim.profile, claim.profile.scope, 14, claim.release)
    assert sum(len(p.rows) for p in parts) == 560
    queue = MemoryQueue()
    calls, guards = [], []
    original = type(model).predict

    def predict(self, inputs, **kwargs):
        calls.append((len(inputs.rows), kwargs["limits"]))
        kwargs["tick"]()
        return original(self, inputs, **kwargs)

    monkeypatch.setattr(type(model), "predict", predict)
    assert (
        v12_worker.run_attempt(queue, claim, model, guard=lambda full: guards.append(full))
        == "succeeded"
    ), queue.failed
    assert len(queue.completed) == 1 and not queue.failed
    output = queue.completed[0]
    assert sum(len(p.predictions) for p in output.parts) == 560
    assert output.published_forecast_outputs == output.model_refits == 0
    assert all(
        limit.wall_seconds <= model.release.qualification.limits.wall_seconds for _, limit in calls
    )
    assert guards.count(True) == 1
    assert len(set(p.key for part in output.parts for p in part.predictions)) == 560


@pytest.fixture
def worker_clock(monkeypatch):
    clock = SimpleNamespace(now=1000.0)
    # Replace only the worker's clock reference; predictor resource limits keep
    # their real clock. Scope geometry is not a host-speed qualification.
    monkeypatch.setattr(v12_worker, "time", SimpleNamespace(monotonic=lambda: clock.now))
    return clock


def test_maximum_scope_uses_ten_bounded_parts(batch, inputs, worker_clock):
    claim, model = batch
    profile = expanded(inputs, locations=5)
    parts = chunks(profile, profile.scope, 14, claim.release)
    assert len(parts) == 10 and sum(len(p.rows) for p in parts) == 1400
    assert all(len(p.rows) <= 256 for p in parts)
    run = running(profile, claim.release)
    maximum = replace(claim, run=run, profile=profile)
    queue = MemoryQueue()
    queue.budget = 120.0
    assert v12_worker.run_attempt(queue, maximum, model, guard=lambda full: None) == "succeeded", (
        queue.failed
    )
    assert sum(len(part.predictions) for part in queue.completed[0].parts) == 1400


def test_attempt_budget_expiring_during_prediction_never_completes(
    batch, worker_clock, monkeypatch
):
    claim, model = batch
    queue = MemoryQueue()
    calls = []

    def expired(self, inputs, **kwargs):
        calls.append(inputs)
        worker_clock.now += queue.budget
        kwargs["tick"]()
        pytest.fail("prediction continued after worker budget expired")

    monkeypatch.setattr(type(model), "predict", expired)
    assert v12_worker.run_attempt(queue, claim, model, guard=lambda full: None) == "lease_lost"
    assert len(calls) == 1
    assert not queue.completed and not queue.failed


def test_elapsed_heartbeat_interval_renews_fence_between_parts(batch, worker_clock, monkeypatch):
    claim, model = batch
    queue = MemoryQueue()
    original = type(model).predict
    guards = []

    def elapsed(self, inputs, **kwargs):
        worker_clock.now += claim.run.policy.heartbeat_seconds
        kwargs["tick"]()
        return original(self, inputs, **kwargs)

    monkeypatch.setattr(type(model), "predict", elapsed)
    assert (
        v12_worker.run_attempt(queue, claim, model, guard=lambda full: guards.append(full))
        == "succeeded"
    )
    assert len(queue.completed) == 1 and not queue.failed
    assert queue.heartbeats >= 2 + 2 * len(queue.completed[0].parts)
    assert guards.count(False) >= 1 + len(queue.completed[0].parts)
    assert guards.count(True) == 1


def test_single_series_over_byte_budget_is_refused(batch, monkeypatch):
    claim, _ = batch
    monkeypatch.setattr(v12_batch, "MAX_REQUEST_BYTES", 1)
    with pytest.raises(ValueError, match="single_series_byte_limit"):
        chunks(claim.profile, claim.profile.scope, 14, claim.release)


def test_byte_budget_splits_further_without_losing_rows(batch, monkeypatch):
    claim, _ = batch
    one_scope = BatchScope(
        product_ids=(claim.profile.scope.product_ids[0],),
        selling_location_ids=(claim.profile.scope.selling_location_ids[0],),
        channel="store",
    )
    one = chunks(claim.profile, one_scope, 14, claim.release)[0]
    from retailops_ai.forecast_jobs.v12_contracts import V12Execution

    document = {
        "root": "x" * 4096,
        "inference": claim.release.binding.approval.context().model_dump(mode="json"),
        **V12Execution(
            pin=claim.release.binding.approval.qualification.pin,
            inputs=one,
            limits=claim.release.binding.approval.qualification.limits,
        ).model_dump(mode="json"),
    }
    monkeypatch.setattr(v12_batch, "MAX_REQUEST_BYTES", len(canonical_bytes(document)) * 2)
    parts = chunks(claim.profile, claim.profile.scope, 14, claim.release)
    assert len(parts) > 4 and sum(len(p.rows) for p in parts) == 560


@pytest.mark.parametrize("failure", ["part", "guard", "lease"])
def test_failure_never_commits_partial_result(batch, monkeypatch, failure):
    claim, model = batch
    queue, count = MemoryQueue(), 0
    original = type(model).predict

    def predict(self, inputs, **kwargs):
        nonlocal count
        count += 1
        if count == 2 and failure == "part":
            raise RuntimeError("explicit_partial_compute_failure")
        if count == 2 and failure == "lease":
            raise LeaseLost("explicit_partial_lease_loss")
        return original(self, inputs, **kwargs)

    def guard(full):
        if full and failure == "guard":
            raise ValueError("explicit_registry_change")

    monkeypatch.setattr(type(model), "predict", predict)
    status = v12_worker.run_attempt(queue, claim, model, guard=guard)
    assert status == ("lease_lost" if failure == "lease" else "failed")
    assert not queue.completed
    assert bool(queue.failed) == (failure != "lease")


def test_lost_initial_fence_stops_before_prediction(batch, monkeypatch):
    claim, model = batch
    queue = MemoryQueue()
    queue.lost_at = 1

    def forbidden(*_, **kwargs):
        pytest.fail("prediction after lost lease")

    monkeypatch.setattr(type(model), "predict", forbidden)
    assert v12_worker.run_attempt(queue, claim, model, guard=lambda full: None) == "lease_lost"
    assert not queue.completed and not queue.failed


def test_image_mismatch_never_predicts(batch):
    claim, model = batch
    queue = MemoryQueue()
    assert (
        v12_worker.run_attempt(
            queue, claim, replace(model, image_digest="sha256:" + "d" * 64), guard=lambda full: None
        )
        == "failed"
    )
    assert not queue.completed


def test_exhausted_attempt_budget_does_not_start_prediction(batch, monkeypatch):
    claim, model = batch
    queue = MemoryQueue()
    queue.budget = 0.0

    def forbidden(*args, **kwargs):
        pytest.fail("prediction after exhausted attempt budget")

    monkeypatch.setattr(type(model), "predict", forbidden)
    assert v12_worker.run_attempt(queue, claim, model, guard=lambda full: None) == "lease_lost"
    assert not queue.completed and not queue.failed


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_part",
        "wrong_profile",
        "wrong_key",
        "rss",
        "time",
        "scope",
        "duplicate_part",
        "prediction_hash",
    ],
)
def test_receipt_tamper_refused(batch, mutation):
    claim, model = batch
    parts = chunks(claim.profile, claim.profile.scope, 14, claim.release)
    results = tuple(inference_result(model.export, p, model.release.context()) for p in parts)
    output = computation_receipt(claim.run, claim.profile, claim.release, results)
    raw = output.model_dump(mode="json")
    if mutation == "missing_part":
        raw["parts"].pop()
    elif mutation == "wrong_profile":
        raw["parts"][0]["profile_id"] = "batch-profile-sha256-" + "d" * 64
    elif mutation == "wrong_key":
        raw["parts"][0]["predictions"][0]["key"] = "unrelated"
        raw["parts"][0]["predictions_sha256"] = canonical_sha256(raw["parts"][0]["predictions"])
    elif mutation == "rss":
        raw["parts"][0]["peak_rss_bytes"] = 2**32
    elif mutation == "time":
        raw["parts"][0]["generated_at"] = (claim.run.requested_at - timedelta(days=1)).isoformat()
    elif mutation == "scope":
        raw["scope"]["product_ids"] = ["p-00"]
    elif mutation == "duplicate_part":
        raw["parts"].append(raw["parts"][0])
    else:
        raw["predictions_sha256"] = "d" * 64
    raw["artifact_id"] = "v12-computation-sha256-" + canonical_sha256(
        {k: v for k, v in raw.items() if k != "artifact_id"}
    )
    with pytest.raises(ValueError):
        changed = V12BatchReceipt.model_validate_json(canonical_bytes(raw))
        verify_receipt(claim.run, claim.profile, claim.release, changed)


def test_private_pipeline_identity_does_not_gain_promoter_role(tmp_path, monkeypatch, batch):
    claim, _ = batch
    principal = actor(claim.profile)

    class Authority:
        def authenticate(self, value):
            assert value == "Bearer " + "a" * 43
            return principal

    monkeypatch.setattr(
        "retailops_ai.security.model_operator.load_authority", lambda path: Authority()
    )
    credentials = tmp_path / "credentials.json"
    credentials.write_text(
        '{"schema_version":"1.0","credentials":[{"principal_id":"unit-pipeline","bearer_token":"'
        + "a" * 43
        + '"}]}'
    )
    credentials.chmod(0o600)
    assert private_principal(Path("unused"), credentials) == principal
    with pytest.raises(ValueError, match="promoter"):
        model_operator(Path("unused"), credentials)


def test_scope_and_requester_read_boundary(batch):
    claim, _ = batch
    authorize_read(claim.run, actor(claim.profile))
    with pytest.raises(BatchError):
        authorize_read(claim.run, replace(actor(claim.profile), principal_id="someone-else"))
    with pytest.raises(BatchError):
        authorize_read(claim.run, replace(actor(claim.profile), product_ids=frozenset({"outside"})))


def test_real_namespace_and_test_environment_do_not_mix():
    with pytest.raises(ValueError):
        PostgresV12Queue(None, "local", mechanics=True)


def test_transition_cannot_change_pinned_release(batch):
    claim, _ = batch
    changed = claim.run.model_copy(
        update=dict(release_id="v12-model-release-sha256-" + "c" * 64, status="failed")
    )
    with pytest.raises(ValueError, match="changed_pin"):
        transition(claim.run, changed)
