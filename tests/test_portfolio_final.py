"""Prospective dates, exact seeds and complete calibration required before final access."""

from datetime import date, timedelta
from pathlib import Path

import pytest
from test_forecast_source_replay import physical_parents as physical_parents

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign.campaign_final_contract import CampaignFinalExportPlan
from retailops_ai.evaluation_campaign.portfolio_final_data import prepare_final
from retailops_ai.source_snapshot.files import SnapshotError
from scripts import run_ai09_portfolio_final as runner


@pytest.mark.parametrize("seed", [42, 137, 2026])
def test_final_source_and_mature_origins_are_fixed_before_generation(seed):
    assessment = runner.probe.read(runner.ASSESSMENT)
    source = runner.source_plan(seed)
    final = assessment["final"]
    assert source["generation"]["seed"] == seed
    assert source["generation"]["products"] == 8
    assert source["generation"]["stores"] == 3
    assert source["generation"]["max_daily_rows"] == 8760
    start, end = (
        date.fromisoformat(source["generation"][key]) for key in ("start_date", "end_date")
    )
    assert (end - start).days + 1 == source["generation"]["days"] == 365
    origins = final["origins"]
    assert date.fromisoformat(origins["start"]) > date(2026, 12, 31) + timedelta(days=15)
    plan = CampaignFinalExportPlan.model_validate_json(
        canonical_bytes(
            {
                "source_recipe_sha256": "a" * 64,
                "generation_operation_id": "test-final-seed",
                "origins": origins,
                "label_knowledge_cutoff": final["label_knowledge_cutoff"],
                "prior_exposure_end": final["prior_documented_parent_exposure_end"],
            }
        )
    )
    assert plan.label_knowledge_cutoff.date() >= plan.origins.end + timedelta(days=15)
    assert source["budgets"]["tree_rss_bytes"] == 12 * 1024**3
    assert source["final_test_opened"] is True


@pytest.mark.parametrize("seed", [0, 41, 43, 2027, True, "42"])
def test_unregistered_seed_cannot_create_a_new_final_attempt(seed):
    with pytest.raises(ValueError, match="unregistered_seed"):
        runner.source_plan(seed)


def test_final_data_refuses_access_before_whole_parent_reservation(tmp_path, monkeypatch):
    import retailops_ai.evaluation_campaign.portfolio_final_data as final_data

    def guarded(root, name):
        assert root == tmp_path and name == "intent.json", "must not inspect a final parent"
        return {"final_test_opened": False}

    monkeypatch.setattr(final_data, "read_json", guarded)
    with pytest.raises(SnapshotError, match="reservation_required"):
        prepare_final(tmp_path, {}, {})


def test_missing_frozen_calibration_cannot_start_final(tmp_path):
    with pytest.raises((FileNotFoundError, ValueError)):
        runner.verified_freeze(tmp_path, {"freeze_sha256": "a" * 64})


def test_source_worker_entrypoint_remains_stdlib_only_before_operation():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-I", "-S", str(Path(runner.__file__)), "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "--producer-python" in result.stdout


@pytest.mark.parametrize("relocated", [False, True])
def test_real_import_layout_reaches_wire_and_rejects_empty_smoke(
    physical_parents, tmp_path, relocated
):
    """Exposed public smoke verifies wiring, not project holdout quality or maturity."""
    import json
    import shutil

    from retailops_ai.data_contracts.identity import canonical_sha256
    from retailops_ai.evaluation_campaign.final_forecast import verify_final_forecast

    output = tmp_path / "assessment"
    snapshot, curated = output / "original/imported/snapshot", output / "original/curated"
    shutil.copytree(physical_parents[0], snapshot)
    shutil.copytree(physical_parents[1], curated)
    generation = json.loads((curated / "curated_manifest.json").read_bytes())["descriptor"][
        "source_parameters"
    ]
    assessment = {
        "final": {
            "origins": {"start": "2026-07-04", "end": "2026-07-05"},
            "label_knowledge_cutoff": "2026-07-31T23:59:59Z",
            "prior_documented_parent_exposure_end": "2026-06-18",
            "label_delay_days": 1,
            "maximum_rows_per_seed": 50000,
            # This short, already public fixture cannot prove project eligibility.
            "minimum_eligible_rows_per_seed": 0,
        }
    }
    documents = {
        "intent.json": {
            "final_test_opened": True,
            "whole_parent_reserved": True,
            "assessment_sha256": canonical_sha256(assessment),
            "source_plan": {"generation": generation},
        },
        "import.json": {"destination": str(snapshot.parent)},
        "curation.json": {"destination": str(curated)},
    }
    for name, value in documents.items():
        (output / name).write_bytes(canonical_bytes(value))
    with pytest.raises(SnapshotError, match="portfolio_empty_eligible_population"):
        prepare_final(
            output, assessment, generation, parent_paths=(snapshot, curated) if relocated else None
        )
    dataset = next((output / "final-data").glob("ai09-final-forecast-sha256-*"))
    manifest = verify_final_forecast(dataset)
    assert manifest.descriptor.population.row_count > 0
    assert manifest.descriptor.population.eligible_rows == 0
    assert (output / "inputs.jsonl").is_file()
    assert (output / "actuals.jsonl").is_file()
    assert not manifest.stage_ready
