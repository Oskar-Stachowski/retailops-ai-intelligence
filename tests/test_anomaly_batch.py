"""Actual saved-model batch output preserves pins, context and replay identities."""

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from test_anomaly_detectors import point, scope
from test_anomaly_portfolio_model import saved_model  # noqa: F401, F811 - pytest fixture

from retailops_ai.anomaly_detectors.protocol import Window
from retailops_ai.anomaly_evaluation.contract import Decision
from retailops_ai.anomaly_evaluation.verification import verify_scores
from retailops_ai.anomaly_portfolio.batch import batch
from retailops_ai.anomaly_portfolio.lifecycle_contract import Binding, Qualification, release_for
from retailops_ai.anomaly_portfolio.model import row, score
from retailops_ai.anomaly_portfolio.result_store import logical
from retailops_ai.model_lifecycle.contracts import GATES, Gate, Receipt
from retailops_ai.source_snapshot.files import canonical_json


def test_batch_context_and_publication_retry_keep_content_identity(tmp_path, saved_model):  # noqa: F811
    raw = canonical_json(saved_model.model_dump(mode="json")) + b"\n"
    model_path = tmp_path / "model.json"
    model_path.write_bytes(raw)
    placeholder = Receipt(sha256="0" * 64, size_bytes=1)
    # Synthetic qualification is used only for local batch contract mechanics.
    # Registry acceptance separately requires recomputed six-case quality.
    qualification = Qualification(
        evidence_id="mechanics-only",
        evaluation_id="anomaly-quality-sha256-" + "0" * 64,
        reference_id="mechanics-only",
        source_dataset_id=saved_model.descriptor.source_dataset_id,
        qualified_anomaly_input_id=saved_model.descriptor.qualified_anomaly_input_id,
        original_started_at=datetime(2026, 8, 1, tzinfo=UTC),
        original_completed_at=datetime(2026, 8, 2, tzinfo=UTC),
        source_code_commit="1" * 40,
        ai_code_commit="2" * 40,
        dependency_lock_sha256=saved_model.descriptor.dependency_lock_sha256,
        model_family="seasonal_residual",
        model_seed=saved_model.descriptor.policy.model_seed,
        model=Receipt(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw)),
        config=placeholder,
        signature=placeholder,
        input_example=placeholder,
        expected_output_sha256="0" * 64,
        gates={k: Gate(status="passed", report=placeholder) for k in GATES},
    )
    binding = Binding(
        model_version="1",
        mlflow_run_id="3" * 32,
        source_uri="mlflow-artifacts:/mechanics/anomaly",
        qualification_sha256="4" * 64,
        qualification=qualification,
    )
    release = release_for(
        decision_id="decision-anomaly-mechanics-promotion",
        binding=binding.model_dump(mode="json"),
        image_digest="sha256:" + "5" * 64,
        previous_release_id=None,
        previous_version=None,
        restored_from_release_id=None,
    )
    day = date(2026, 8, 12)
    p = point(day, 90)
    frame = SimpleNamespace(
        points=lambda: [p],
        manifest_sha256="6" * 64,
        manifest=SimpleNamespace(
            qualified_anomaly_input_id=saved_model.descriptor.qualified_anomaly_input_id,
            descriptor=SimpleNamespace(
                coverage=SimpleNamespace(
                    descriptor=SimpleNamespace(
                        source_dataset_id=saved_model.descriptor.source_dataset_id
                    )
                ),
                full_dq_replay_id="full-dq-replay-sha256-" + "7" * 64,
            ),
        ),
        replay_manifest=SimpleNamespace(
            descriptor=SimpleNamespace(
                parent=SimpleNamespace(curated_dataset_id="curated-sha256-" + "8" * 64)
            )
        ),
    )
    generated = datetime(2026, 9, 1, tzinfo=UTC)
    args = (
        release,
        model_path,
        frame,
        (scope(),),
        Window(start=day, end=day),
        datetime(2026, 8, 16, tzinfo=UTC),
    )
    first, items = batch(*args, generated)
    second, retry = batch(*args, generated + timedelta(days=1))
    assert first["batch_id"] == second["batch_id"]
    assert items[0].anomaly_id == retry[0].anomaly_id
    assert items[0].signal_episode_id == retry[0].signal_episode_id
    assert logical(items) == logical(retry)
    assert items[0].detected_at == generated
    assert items[0].observed_window.start == day
    assert items[0].inventory_context.on_hand == p.context.on_hand
    assert items[0].promotion_context.planned_price == p.context.planned_price
    assert items[0].status == "scored" and items[0].role == "batch"


def test_promotion_replays_saved_model_scores_and_rejects_resealed_predictions(saved_model):  # noqa: F811
    day = date(2026, 8, 12)
    p = point(day, 90)
    window = Window(start=day, end=day)
    inputs = [row(p).model_dump(mode="json")]
    for family in ("seasonal_residual", "isolation_forest"):
        decisions = score(
            saved_model, [p], (scope(),), window, family, "batch", datetime(2026, 8, 16, tzinfo=UTC)
        )
        verify_scores(saved_model, decisions, inputs)
        changed = decisions[0].model_dump(mode="json")
        changed["score"] += 0.0001
        changed["alert"] = changed["score"] > changed["threshold"]
        changed["severity"] = "medium" if changed["alert"] else "none"
        altered = Decision.model_validate_json(json.dumps(changed))
        with pytest.raises(ValueError, match="prediction_mismatch"):
            verify_scores(saved_model, [altered], inputs)
        with pytest.raises(ValueError, match="abstention"):
            verify_scores(saved_model, decisions, [None])
