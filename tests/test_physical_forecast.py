"""Bounded physical indexing and role binding; no project/final data or fits."""

import json
import sqlite3
from contextlib import closing
from datetime import timedelta

import pytest
from pydantic import ValidationError
from test_forecast_features import SERIES
from test_forecast_features import tables as tables
from test_forecast_manifests import timeline as timeline
from test_forecast_outcome_reader import candidate
from test_forecast_source_replay import physical_parents as physical_parents
from test_forecast_source_versions import history
from test_independent_forecast_partitions import policy
from test_independent_forecast_partitions import population as population
from test_tensorflow_challenger import development as development

from retailops_ai.curated.builder import iter_rows
from retailops_ai.data_contracts.common import ForecastKey, end_of_day
from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign import physical_versions, source_versions
from retailops_ai.evaluation_campaign.physical_contract import (
    PhysicalForecastRecipe,
    PhysicalSourceSpec,
)
from retailops_ai.evaluation_campaign.physical_forecast import _index
from retailops_ai.forecasting.contract import OriginWindow, Parent
from retailops_ai.source_snapshot.files import SnapshotError


def source():
    return PhysicalSourceSpec(
        schema_version="1.1.0",
        parent=Parent(
            source_dataset_id="source-sha256-" + "1" * 64,
            snapshot_id="snapshot-sha256-" + "2" * 64,
            curated_dataset_id="curated-sha256-" + "3" * 64,
            curated_descriptor_sha256="4" * 64,
            business_timezone="UTC",
            forecast_source_status="passed",
        ),
        source_parameters={
            "profile": "controlled_metadata_only",
            "start_date": "2025-01-01",
            "end_date": "2027-12-31",
        },
        snapshot_manifest_sha256="5" * 64,
        curated_manifest_sha256="6" * 64,
    )


def recipe():
    temporal = policy()
    return PhysicalForecastRecipe(
        source=source(),
        roles=temporal.roles,
        origins=OriginWindow(
            start=temporal.roles[0].origins.start, end=temporal.roles[-1].origins.end
        ),
    )


def test_new_wire_accepts_explicit_larger_budget_without_qualifying_resources():
    new = recipe().model_copy(update={"max_population_rows": 100001})
    parsed = PhysicalForecastRecipe.model_validate_json(new.model_dump_json())
    assert parsed.max_population_rows == 100001
    assert not parsed.resource_qualified and not parsed.final_test_access_authorized
    assert parsed.temporal_policy().max_population_rows == 100000


@pytest.mark.parametrize(
    "change",
    [
        "missing_role",
        "short_purge",
        "late_cutoff",
        "extra_origin",
        "grant_resource",
        "grant_test",
        "oversize_index",
    ],
)
def test_new_recipe_rejects_temporal_ambiguity_and_permission_claims(change):
    value = recipe().model_dump(mode="json")
    if change == "missing_role":
        value["roles"].pop()
    elif change == "short_purge":
        value["purge_days"] = 14
    elif change == "late_cutoff":
        value["roles"][0]["label_knowledge_cutoff"] = value["roles"][1]["label_knowledge_cutoff"]
    elif change == "extra_origin":
        value["origins"]["end"] = "2027-07-31"
    elif change == "grant_resource":
        value["resource_qualified"] = True
    elif change == "grant_test":
        value["final_test_access_authorized"] = True
    else:
        value["max_index_bytes"] = 8 * 1024**3 + 1
    with pytest.raises(ValidationError):
        PhysicalForecastRecipe.model_validate_json(canonical_bytes(value))


def test_bounded_raw_index_matches_complete_legacy_version_inventory(physical_parents, tmp_path):
    _, curated = physical_parents
    manifest = json.loads((curated / "curated_manifest.json").read_bytes())
    with (
        closing(_index(tmp_path / "new.sqlite", 128 * 1024**2)) as new,
        closing(sqlite3.connect(tmp_path / "old.sqlite")) as old,
    ):
        index = physical_versions.PhysicalVersionIndex(
            new, curated, manifest, maximum_rows=100000, maximum_bytes=128 * 1024**2
        )
        observations, versions, digest = source_versions._populate(old, curated, manifest)
        assert (index.observation_rows, index.version_rows, index.version_inventory_sha256) == (
            observations,
            versions,
            digest,
        )
        assert not new.execute("SELECT 1 FROM sqlite_master WHERE name='qualified'").fetchone()
        spec = next(t for t in manifest["tables"] if t["table"] == "daily_demand_observations")
        for row in iter_rows(curated, spec["files"], 256):
            key = ForecastKey(
                forecast_origin=end_of_day(row["business_date"] - timedelta(days=1)),
                business_timezone="UTC",
                cutoff_policy="end_of_day_second_v1",
                target_date=row["business_date"],
                horizon_days=1,
                **{k: row[k] for k in ("product_id", "selling_location_id", "channel")},
            )
            cutoff = row["curated_available_at"] + timedelta(days=31)
            candidates = index.candidates(key, cutoff)
            expected = [
                json.loads(body)
                for (body,) in old.execute("SELECT body FROM qualified ORDER BY key")
                if json.loads(body)["observation_id"] == row["id"]
            ]
            assert candidates == tuple(
                source_versions.ForecastSourceVersion.model_validate_json(
                    canonical_bytes(e)
                ).at_cutoff(cutoff)
                for e in expected
            )
            break
        index.clear_cache()


@pytest.mark.parametrize(
    "malformation", ["orphan", "missing_history", "count", "budget", "late_quality"]
)
def test_complete_index_rejects_unscoped_bad_rows_and_keeps_late_quality_unknown(
    tmp_path, monkeypatch, malformation
):
    observation, versions = history()
    if malformation == "orphan":
        versions[0]["observation_id"] = "orphan"
    if malformation == "missing_history":
        versions = []
    manifests = {
        "schema_version": "1.0.0",
        "tables": [
            {
                "table": "daily_demand_observations",
                "row_count": 2 if malformation == "count" else 1,
                "files": [{"table": "o"}],
            },
            {
                "table": "daily_demand_versions",
                "row_count": len(versions),
                "files": [{"table": "v"}],
            },
        ],
    }
    # Source declarations only; the public source fixture above is the physical comparison.
    monkeypatch.setattr(
        physical_versions,
        "iter_rows",
        lambda root, refs, batch: iter([observation] if refs[0]["table"] == "o" else versions),
    )
    monkeypatch.setattr(physical_versions, "columns_for", lambda table, version: [])
    monkeypatch.setattr(
        physical_versions,
        "decoded",
        lambda raw, columns: (
            observation
            if json.loads(raw)["id"] == observation["id"]
            else next(r for r in versions if r["id"] == json.loads(raw)["id"])
        ),
    )
    with closing(_index(tmp_path / "data.sqlite", 128 * 1024**2)) as db:
        if malformation != "late_quality":
            with pytest.raises((SnapshotError, sqlite3.Error)):
                physical_versions.PhysicalVersionIndex(
                    db,
                    tmp_path,
                    manifests,
                    maximum_rows=100000,
                    maximum_bytes=4096 if malformation == "budget" else 128 * 1024**2,
                )
        else:
            index = physical_versions.PhysicalVersionIndex(
                db, tmp_path, manifests, maximum_rows=100000, maximum_bytes=128 * 1024**2
            )
            grain = canonical_bytes(
                [
                    observation[k].isoformat() if k == "business_date" else observation[k]
                    for k in source_versions.GRAIN
                ]
            )
            old, latest = index._for_grain(grain)
            assert old.at_cutoff(observation["curated_available_at"]).source_data_complete is False
            assert (
                latest.at_cutoff(
                    observation["curated_available_at"] - timedelta(microseconds=1)
                ).source_data_complete
                is False
            )
            assert (
                latest.at_cutoff(observation["curated_available_at"]).source_data_complete is True
            )
            index.clear_cache()


@pytest.mark.parametrize(
    "field,value", [("start_date", "2027-01-01"), ("end_date", "2025-01-02"), ("start_date", None)]
)
def test_source_history_cannot_be_missing_or_crossed_by_roles_or_maturity(field, value):
    item = recipe().model_dump(mode="json")
    item["source"]["source_parameters"][field] = value
    with pytest.raises(ValidationError, match="source_history"):
        PhysicalForecastRecipe.model_validate_json(canonical_bytes(item))


@pytest.fixture
def stored_control(population, timeline, tmp_path, monkeypatch):
    """Real role files and SQL; declared feature/label unit control, no source proof."""
    import hashlib
    from collections import Counter

    from retailops_ai.data_contracts.identity import canonical_sha256
    from retailops_ai.evaluation_campaign import partitions, physical_forecast
    from retailops_ai.evaluation_campaign.label_contract import OutcomeEvidence
    from retailops_ai.evaluation_campaign.labels import qualify_evidence
    from retailops_ai.evaluation_campaign.physical_contract import (
        PhysicalForecastDescriptor,
        PhysicalForecastExample,
        PhysicalForecastManifest,
    )
    from retailops_ai.forecasting.contract import make_origin
    from retailops_ai.forecasting.features import OriginFeatures
    from retailops_ai.forecasting.manifest_contract import FeatureManifest

    path, rows, _ = population
    old = partitions.verify_feature_set(path)
    parameters = {
        **old.descriptor.source_parameters,
        "start_date": "2025-01-01",
        "end_date": "2027-12-31",
    }
    feature_descriptor = old.descriptor.model_copy(update={"source_parameters": parameters})
    feature = FeatureManifest(
        feature_set_id="features-sha256-"
        + canonical_sha256(feature_descriptor.model_dump(mode="json")),
        descriptor=feature_descriptor,
        generated_at=old.generated_at,
    )
    histories = {
        r.history_context_sha256: OriginFeatures(
            timeline, make_origin(r.forecast_origin.date())
        ).history(SERIES)
        for r in rows
    }
    monkeypatch.setattr(physical_forecast, "verify_feature_set", lambda root: feature)
    monkeypatch.setattr(physical_forecast, "input_models", lambda root, name: iter(rows))
    monkeypatch.setattr(
        "retailops_ai.forecasting.splits.input_models", lambda root, name: iter(histories.values())
    )
    spec = source().model_copy(
        update={"parent": feature_descriptor.parent, "source_parameters": parameters}
    )
    plan = recipe().model_copy(
        update={"source": spec, "features": feature_descriptor.resolved_policy}
    )
    plan = PhysicalForecastRecipe.model_validate_json(plan.model_dump_json())
    root = tmp_path / "stored-control"
    root.mkdir()
    (root / "features").mkdir()
    (root / "features/feature_manifest.json").write_bytes(
        canonical_bytes(feature.model_dump(mode="json")) + b"\n"
    )
    populations = {}
    for role in physical_forecast.ALL_ROLES:
        examples = []
        for row in rows:
            membership = physical_forecast._membership(row, plan)
            if membership.role != role:
                continue
            outcome = None
            if role != "purged":
                evidence = OutcomeEvidence(
                    key=ForecastKey.model_validate(
                        row.model_dump(include=set(partitions.KEY_FIELDS))
                    ),
                    candidates=(candidate(row),),
                )
                outcome = qualify_evidence(
                    evidence,
                    membership,
                    row,
                    histories[row.history_context_sha256],
                    plan.features,
                    plan.label_delay_days,
                )
            examples.append(PhysicalForecastExample(membership=membership, outcome=outcome))
        examples.sort(key=lambda e: partitions.membership_key(e.membership))
        raw = b"".join(canonical_bytes(e.model_dump(mode="json")) + b"\n" for e in examples)
        (root / (role + ".jsonl")).write_bytes(raw)
        counter = Counter()
        for example in examples:
            physical_forecast._counts(counter, example)
        keys = b"".join(partitions.membership_key(e.membership) + b"\n" for e in examples)
        populations[role] = physical_forecast._role_file(
            counter, len(raw), hashlib.sha256(raw).hexdigest(), hashlib.sha256(keys).hexdigest()
        )
    descriptor = PhysicalForecastDescriptor(
        recipe=plan,
        runtime=partitions.runtime_pin(),
        feature_set_id=feature.feature_set_id,
        feature_descriptor=feature_descriptor,
        populations=populations,
        snapshot_inventory_sha256="1" * 64,
        curated_inventory_sha256="2" * 64,
        logical_curated_sha256="3" * 64,
        observation_rows=75,
        version_rows=75,
        version_inventory_sha256="4" * 64,
    )
    manifest = PhysicalForecastManifest(
        dataset_id="ai09-physical-forecast-sha256-"
        + canonical_sha256(descriptor.model_dump(mode="json")),
        descriptor=descriptor,
    )
    (root / "manifest.json").write_bytes(canonical_bytes(manifest.model_dump(mode="json")) + b"\n")
    assert physical_forecast.verify_physical_forecast(root) == manifest
    return root, manifest


@pytest.mark.parametrize(
    "mutation", ["feature_hash", "duplicate", "eligibility", "role", "checksum"]
)
def test_resealed_role_corruption_is_rejected_against_complete_feature_keys(
    stored_control, mutation
):
    import hashlib
    from collections import Counter

    from retailops_ai.data_contracts.identity import canonical_sha256
    from retailops_ai.evaluation_campaign import partitions, physical_forecast
    from retailops_ai.evaluation_campaign.physical_contract import PhysicalForecastExample

    root, manifest = stored_control
    records = [json.loads(line) for line in (root / "train.jsonl").read_bytes().splitlines()]
    if mutation == "feature_hash":
        records[0]["membership"]["feature_row_sha256"] = "f" * 64
        records[0]["outcome"]["feature_row_sha256"] = "f" * 64
    elif mutation == "duplicate":
        records[1] = records[0]
    elif mutation == "eligibility":
        records[0]["outcome"]["eligible"] = False
        if "stale_history" not in records[0]["outcome"]["reasons"]:
            records[0]["outcome"]["reasons"].append("stale_history")
    elif mutation == "role":
        records[0]["membership"]["role"] = "tune"
        records[0]["outcome"]["label"]["role"] = "tune"
    raw = b"".join(canonical_bytes(r) + b"\n" for r in records)
    (root / "train.jsonl").write_bytes(raw)
    if mutation != "checksum":
        items = [PhysicalForecastExample.model_validate_json(canonical_bytes(r)) for r in records]
        counter = Counter()
        for item in items:
            physical_forecast._counts(counter, item)
        keys = b"".join(partitions.membership_key(e.membership) + b"\n" for e in items)
        value = manifest.model_dump(mode="json")
        value["descriptor"]["populations"]["train"] = physical_forecast._role_file(
            counter, len(raw), hashlib.sha256(raw).hexdigest(), hashlib.sha256(keys).hexdigest()
        ).model_dump(mode="json")
        value["dataset_id"] = "ai09-physical-forecast-sha256-" + canonical_sha256(
            value["descriptor"]
        )
        (root / "manifest.json").write_bytes(canonical_bytes(value) + b"\n")
    else:
        (root / "train.jsonl").write_bytes(raw + b" ")
    with pytest.raises((SnapshotError, ValidationError)):
        physical_forecast.verify_physical_forecast(root)
