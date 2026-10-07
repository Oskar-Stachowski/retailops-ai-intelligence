"""Planned anomaly metadata and independently frozen exporter dependency pins."""

import json
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from test_ai09_campaign_journal import protocol_document

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign.campaign_contract import CampaignSourceRecipe
from retailops_ai.evaluation_campaign.campaign_export import _producer
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.source_snapshot.inventory_protocol import check_lineage, validate_schema

ARCHIVE = Path(__file__).resolve().parents[1] / "data/fixtures/anomaly-v1_2.zip"


def planned_manifest(case):
    with ZipFile(ARCHIVE) as archive:
        value = json.loads(archive.read(case + "/public/snapshot_manifest.json"))
    source = value["source"]
    descriptor = source["descriptor"]
    for parameters in (source["requested_parameters"], descriptor["resolved_parameters"]):
        parameters.update(forecast_plan_days=14, forecast_plan_version="known-forecast-plans-1.0.0")
    descriptor["forecast_watermarks"] = {
        "daily_demand_observations": {
            "as_of_time": descriptor["context"]["evaluated_at"],
            "complete_through": descriptor["resolved_parameters"]["end_date"],
            "completeness_status": "complete",
            "meaning": "synthetic_sales_day_close_without_return_guarantee",
            "policy_version": "daily-demand-1.0.0",
        }
    }
    return value


@pytest.mark.parametrize("case", ["demand", "physical"])
def test_reviewed_planned_anomaly_metadata_is_typed_without_changing_generator_version(case):
    value = planned_manifest(case)
    assert value["source"]["descriptor"]["generator_version"] == "1.0.0"
    validate_schema(value, "snapshot_manifest.schema.json", "1.2.0")
    # This is schema acceptance only. Unchanged IDs do not qualify these altered
    # declarations as a generated parent or a verified immutable snapshot.
    with pytest.raises(SnapshotError, match="identity_or_lineage"):
        check_lineage(value, {}, False)


@pytest.mark.parametrize("case", ["demand", "physical"])
@pytest.mark.parametrize("value", [True, 0, 15, "14"])
def test_planned_anomaly_rejects_untyped_or_out_of_range_horizon(case, value):
    manifest = planned_manifest(case)
    manifest["source"]["requested_parameters"]["forecast_plan_days"] = value
    with pytest.raises(SnapshotError, match="invalid_inventory_snapshot_schema"):
        validate_schema(manifest, "snapshot_manifest.schema.json", "1.2.0")


def test_anomaly_plan_version_disagreement_is_not_silently_treated_as_unplanned():
    manifest = planned_manifest("physical")
    manifest["source"]["requested_parameters"]["forecast_plan_version"] = "unknown-version"
    with pytest.raises(SnapshotError, match="forecast_plan_source_lineage"):
        check_lineage(manifest, {}, False)


def test_inventory_source_does_not_need_to_claim_exporter_dependency_as_its_own(tmp_path):
    value = protocol_document(tmp_path.resolve())["sources"][0]
    value["exporter_lock_sha256"] = canonical_sha256("independently-frozen-exporter-lock")
    source = CampaignSourceRecipe.model_validate_json(json.dumps(value))
    replay = SimpleNamespace(
        producer_commit=source.producer_commit,
        producer_code_state="clean",
        producer_lock_sha256=source.producer_lock_sha256,
        exporter_commit=source.producer_commit,
        exporter_lock_sha256=source.exporter_lock_sha256,
        declared_exporter_lock_sha256=None,
    )
    _producer(replay, source)
    replay.exporter_lock_sha256 = canonical_sha256("different-exporter-lock")
    with pytest.raises(SnapshotError, match="producer_or_lock"):
        _producer(replay, source)
