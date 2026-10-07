"""Calendar membership remains enforced for every physical table."""

import hashlib
import json
import shutil

import pytest
from test_forecast_features import real_inputs as real_inputs

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecasting.calendar import load_calendar
from retailops_ai.forecasting.features_store import verify_inputs
from retailops_ai.source_snapshot.files import SnapshotError


@pytest.mark.parametrize("first_table", ["features", "history"])
def test_resealed_shorter_calendar_rejects_existing_outside_origin(
    real_inputs, tmp_path, first_table
):
    _, _, original = real_inputs
    directory = tmp_path / "shortened-calendar"
    shutil.copytree(original, directory)
    calendar_path = directory / "calendar_manifest.json"
    calendar = json.loads(calendar_path.read_bytes())
    calendar["origins"] = calendar["origins"][1:]
    descriptor = calendar["descriptor"]
    descriptor["origin_window"]["start"] = calendar["origins"][0]["origin_date"]
    descriptor["calendar_content_sha256"] = canonical_sha256(calendar["origins"])
    calendar["calendar_id"] = "forecast-calendar-sha256-" + canonical_sha256(descriptor)
    calendar_path.write_text(json.dumps(calendar))
    # This is a valid, self-consistent calendar. Rejection must come from
    # checking the stored rows against it, not from a stale checksum or ID.
    assert len(load_calendar(calendar_path).origins) == 1

    manifest_path = directory / "inputs_manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["calendar_file"].update(
        size_bytes=calendar_path.stat().st_size,
        sha256=hashlib.sha256(calendar_path.read_bytes()).hexdigest(),
    )
    manifest["descriptor"]["calendar_id"] = calendar["calendar_id"]
    manifest["inputs_id"] = "forecast-inputs-sha256-" + canonical_sha256(manifest["descriptor"])
    other_table = "history" if first_table == "features" else "features"
    manifest["tables"] = {name: manifest["tables"][name] for name in (first_table, other_table)}
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(SnapshotError, match="forecast_inputs_origin_outside_calendar"):
        verify_inputs(directory)
