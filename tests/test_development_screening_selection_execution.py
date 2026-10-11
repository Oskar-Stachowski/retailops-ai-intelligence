"""Controlled journals and real paired Tune files; no Project training evidence."""

import shutil
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_campaign_tune_data import paired_control as paired_control
from test_campaign_tune_worker import request_control as request_control
from test_development_screening import export, native_double, setup
from test_forecast_features import tables as tables
from test_forecast_manifests import timeline as timeline
from test_independent_forecast_partitions import population as population
from test_physical_forecast import stored_control as stored_control

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign import campaign_tune_worker as worker
from retailops_ai.evaluation_campaign.campaign_fit import _bundle_inventory
from retailops_ai.evaluation_campaign.development_screening_selection import ScreeningFamilyRanking
from retailops_ai.evaluation_campaign.development_screening_selection_contract import (
    PreparedDevelopmentSelectionJournal,
    PreparedDevelopmentSelectionProtocol,
    freeze_screening_selection,
)
from retailops_ai.source_snapshot.files import SnapshotError


def test_selection_is_frozen_before_initialization_and_preserves_original_exports(
    tmp_path, monkeypatch
):
    _, prefix, output = setup(tmp_path, monkeypatch)
    with pytest.raises(SnapshotError, match="before_journal_initialization"):
        freeze_screening_selection(prefix)
    root = tmp_path / "with-selection"
    prefix = prefix.model_copy(update={"journal_path": str(root)})
    protocol = freeze_screening_selection(prefix)
    journal.initialize(root, protocol)
    assert isinstance(journal.inspect(root), PreparedDevelopmentSelectionJournal)
    assert protocol.maximum_new_attempts == 12 and protocol.maximum_new_fit_attempts == 6
    assert protocol.screening.maximum_multiscale_fit_attempts == 12
    # The original exporter consumes the prefix but binds the actual12-operation journal.
    case = root, protocol.screening, output
    native_double(monkeypatch, case)
    _, receipt = export(case)
    assert receipt.protocol_sha256 == protocol.content_sha256()
    with pytest.raises(ValidationError, match="prerequisites_not_completed"):
        journal.reserve(root, protocol.operations[-1].operation_id)
    assert journal.summary(root)["charged_new_attempts"] == 1
    assert not protocol.advancement_authorized
    # Even a resealed operation cannot loosen the original selection resource/row policy.
    value = protocol.model_dump(mode="json")
    value["selection_plan"]["max_rows"] -= 1
    with pytest.raises(ValidationError, match="original_policy_budget"):
        PreparedDevelopmentSelectionProtocol.model_validate_json(canonical_bytes(value))


def two_trial_request(request_control, tmp_path):
    request, first = request_control
    request = deepcopy(request)
    second_id = "controlled-score-second"
    second = first.model_dump(mode="json")
    second["operation_id"] = second_id
    second["reservation_id"] = "campaign-operation-" + "2" * 32
    second["plan"]["fit_operation_ids"] = {
        f: second_id + "-fit-" + f for f in ("rf", "hgb", "tensorflow")
    }
    second_bundle = tmp_path / "second-predictions"
    shutil.copytree(Path(request["bundles"][first.operation_id]), second_bundle)
    (second_bundle / "plan.json").write_bytes(canonical_bytes(second["plan"]) + b"\n")
    files, size = _bundle_inventory(second_bundle, first.plan.max_output_bytes)
    second.update(
        artifact_files=files, artifact_sha256=canonical_sha256(files), artifact_bytes=size
    )
    request["scores"][second_id] = second
    request["bundles"][second_id] = str(second_bundle)
    request["plan"]["score_operation_ids"] = [first.operation_id, second_id]
    request["family_screening"] = True
    return request


def test_native_worker_ranks_all_six_trials_on_only_two_complete_tune_passes(
    request_control, tmp_path, monkeypatch
):
    from retailops_ai.evaluation_campaign import campaign_tune_data

    request = two_trial_request(request_control, tmp_path)
    opened = []
    original = campaign_tune_data.regular_file

    def capture(root, name):
        opened.append(name)
        return original(root, name)

    monkeypatch.setattr(campaign_tune_data, "regular_file", capture)
    root = tmp_path / "screening-worker"
    root.mkdir(mode=0o700)
    result = worker.select(root, request)
    rank = ScreeningFamilyRanking.model_validate_json(canonical_bytes(result["family_screening"]))
    assert opened == ["tune.jsonl", "predictions.jsonl"] * 2
    assert result["trial_count"] == result["full_tune_label_passes"] == 2
    assert result["calibration_label_passes"] == result["independent_or_final_label_passes"] == 0
    assert rank.status == "not_ready" and len(rank.trials) == 6  # Small fixture has too few rows.
    assert rank.rows == result["rows"] and rank.eligible_rows == result["eligible_rows"]
    assert not rank.advancement_authorized


@pytest.mark.parametrize("flag", [True, 1, "true"])
def test_single_trio_or_invalid_mode_is_refused_before_label_reads(
    request_control, tmp_path, monkeypatch, flag
):
    request, _ = request_control
    request["family_screening"] = flag

    def forbidden(*args):
        raise AssertionError("invalid scope must precede any label read")

    monkeypatch.setattr(worker, "paired_metrics", forbidden)
    with pytest.raises(SnapshotError, match="two_frozen_tune_trios"):
        worker.select(tmp_path / "never-created", request)
