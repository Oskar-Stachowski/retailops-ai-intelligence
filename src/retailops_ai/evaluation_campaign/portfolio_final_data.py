"""Reuse native final-only feature/outcome qualification under the portfolio reservation."""

import json
import sqlite3
import zlib
from pathlib import Path
from typing import Any

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_final_contract import (
    CampaignFinalExportPlan,
    FinalForecastExample,
)
from retailops_ai.evaluation_campaign.final_forecast import (
    _build_final_forecast,
    verify_final_forecast,
)
from retailops_ai.evaluation_campaign.partitions import membership_key
from retailops_ai.evaluation_campaign.physical_contract import PhysicalSourceSpec
from retailops_ai.evaluation_campaign.portfolio_data_io import documents, write_population
from retailops_ai.evaluation_campaign.preparation import runtime_pin
from retailops_ai.evaluation_campaign.source_replay import (
    _open_verified_source_parent,
    physical_limits,
)
from retailops_ai.forecasting.features_contract import InputRow
from retailops_ai.forecasting.manifests import feature_key, input_models
from retailops_ai.forecasting.splits import history_index
from retailops_ai.source_snapshot.files import SnapshotError, file_hash, read_json


def prepare_final(
    output: Path, assessment: dict[str, Any], generation: dict[str, Any]
) -> dict[str, Any]:
    """Call only after a durable whole-source final reservation; never export training roles."""
    intent = read_json(output, "intent.json")
    if (
        intent["final_test_opened"] is not True
        or intent["whole_parent_reserved"] is not True
        or intent["assessment_sha256"] != canonical_sha256(assessment)
        or intent["source_plan"]["generation"] != generation
    ):
        raise SnapshotError("portfolio_final_reservation_required")
    snapshot = Path(read_json(output, "import.json")["destination"])
    curated = Path(read_json(output, "curation.json")["destination"])
    if any(not path.resolve().is_relative_to(output.resolve()) for path in (snapshot, curated)):
        raise SnapshotError("portfolio_final_parent_outside_owned_output")
    actual = read_json(curated, "curated_manifest.json")
    descriptor = actual["descriptor"]
    if any(descriptor["source_parameters"].get(k) != v for k, v in generation.items()):
        raise SnapshotError("portfolio_final_source_recipe_changed")
    source = PhysicalSourceSpec.model_validate_json(
        canonical_bytes(
            {
                "schema_version": actual["schema_version"],
                "parent": {
                    "source_dataset_id": descriptor["parent_source_dataset_id"],
                    "curated_dataset_id": actual["curated_dataset_id"],
                    "snapshot_id": descriptor["parent_snapshot_id"],
                    "curated_descriptor_sha256": canonical_sha256(descriptor),
                    "business_timezone": descriptor["config"]["business_timezone"],
                    "forecast_source_status": actual["readiness"]["forecast_source"],
                },
                "source_parameters": descriptor["source_parameters"],
                "snapshot_manifest_sha256": file_hash(snapshot, "snapshot_manifest.json")[1],
                "curated_manifest_sha256": file_hash(curated, "curated_manifest.json")[1],
            }
        )
    )
    final = assessment["final"]
    plan = CampaignFinalExportPlan.model_validate_json(
        canonical_bytes(
            {
                "source_recipe_sha256": canonical_sha256(generation),
                "generation_operation_id": "portfolio-final-seed-" + str(generation["seed"]),
                "origins": final["origins"],
                "label_knowledge_cutoff": final["label_knowledge_cutoff"],
                "prior_exposure_end": final["prior_documented_parent_exposure_end"],
                "label_delay_days": final["label_delay_days"],
                "max_population_rows": final["maximum_rows_per_seed"],
                "max_artifact_bytes": 2 * 1024**3,
                "max_index_bytes": 1024**3,
            }
        )
    )
    # The shared primitive verifies original snapshot bytes, complete curated
    # transform replay, Source provenance and final-only label maturity.
    with _open_verified_source_parent(
        snapshot, curated, source, limits=physical_limits(source), runtime=runtime_pin()
    ) as replay:
        dataset = _build_final_forecast(replay, plan.bind(source), output / "final-data")
    manifest = verify_final_forecast(dataset)
    if manifest.descriptor.population.eligible_rows < final["minimum_eligible_rows_per_seed"]:
        raise SnapshotError("portfolio_final_insufficient_eligible_rows")
    with sqlite3.connect(output / "final-index.sqlite") as db:
        db.execute("PRAGMA cache_size=-4096")
        db.execute("PRAGMA temp_store=FILE")
        db.execute("PRAGMA max_page_count=262144")
        db.execute("CREATE TABLE final_actuals(key BLOB PRIMARY KEY, body BLOB)")
        db.execute("CREATE TABLE final_features(key BLOB PRIMARY KEY, body BLOB)")
        for document in documents(dataset / "final_evaluation.jsonl"):
            example = FinalForecastExample.model_validate_json(canonical_bytes(document))
            db.execute(
                "INSERT INTO final_actuals VALUES (?,?)",
                (membership_key(example.key), canonical_bytes(document)),
            )
        for row in input_models(dataset / "features", "features"):
            if not isinstance(row, InputRow):
                raise SnapshotError("portfolio_final_feature_schema")
            db.execute(
                "INSERT INTO final_features VALUES (?,?)",
                (feature_key(row), zlib.compress(canonical_bytes(row.model_dump(mode="json")))),
            )
        get_history = history_index(db, dataset / "features")

        def records() -> Any:
            for (body,) in db.execute("SELECT body FROM final_features ORDER BY key"):
                row = InputRow.model_validate_json(zlib.decompress(body))
                found = db.execute(
                    "SELECT body FROM final_actuals WHERE key=?", (membership_key(row),)
                ).fetchone()
                if found is None:
                    raise SnapshotError("portfolio_final_missing_outcome_key")
                example = FinalForecastExample.model_validate_json(found[0])
                if example.outcome.feature_row_sha256 != canonical_sha256(
                    row.model_dump(mode="json")
                ):
                    raise SnapshotError("portfolio_final_outcome_feature_binding")
                yield (
                    row,
                    get_history(row.history_context_sha256),
                    example.outcome.label.observed_sales_units,
                    example.outcome.reasons,
                )

        population = write_population(output, records(), role="final_test")
        if (
            population["rows"] != manifest.descriptor.population.row_count
            or population["eligible"] != manifest.descriptor.population.eligible_rows
        ):
            raise SnapshotError("portfolio_final_complete_population_mismatch")
    (output / "final-index.sqlite").unlink()
    result = {
        "dataset": str(dataset),
        "dataset_id": manifest.dataset_id,
        "population": population,
        "source": source.model_dump(mode="json"),
        "plan": plan.model_dump(mode="json"),
        "final_test_opened": True,
        "scope": "new_portfolio_final_native_data_not_legacy_campaign_authorization",
    }
    (output / "final-data.json").write_text(json.dumps(result, sort_keys=True) + "\n")
    return result
