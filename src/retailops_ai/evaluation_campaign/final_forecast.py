"""Build and check only final forecast keys inside one verified private replay."""

import hashlib
import os
import tempfile
import zlib
from collections import Counter
from contextlib import closing
from pathlib import Path

from retailops_ai.data_contracts.common import ForecastKey
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_final_contract import (
    FinalForecastDescriptor,
    FinalForecastExample,
    FinalForecastManifest,
    FinalForecastRecipe,
)
from retailops_ai.evaluation_campaign.label_contract import OutcomeEvidence
from retailops_ai.evaluation_campaign.labels import qualify_evidence
from retailops_ai.evaluation_campaign.partition_contract import PartitionMembership
from retailops_ai.evaluation_campaign.partitions import KEY_FIELDS, membership_key
from retailops_ai.evaluation_campaign.physical_forecast import _index, _physical_bytes, _role_file
from retailops_ai.evaluation_campaign.physical_versions import PhysicalVersionIndex, check_index
from retailops_ai.evaluation_campaign.source_replay import PrivateSourceReplay
from retailops_ai.forecasting.calendar import build_calendar
from retailops_ai.forecasting.features_contract import HistoryContext, InputRow
from retailops_ai.forecasting.manifests import build_feature_set, input_models, verify_feature_set
from retailops_ai.forecasting.splits import history_index
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    file_hash,
    inventory,
    read_bytes,
    regular_file,
)
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace


def _example(
    row: InputRow, history: HistoryContext, evidence: OutcomeEvidence, recipe: FinalForecastRecipe
) -> FinalForecastExample:
    plan = recipe.plan
    if not plan.origins.start <= row.forecast_origin.date() <= plan.origins.end:
        raise SnapshotError("final_forecast_origin_outside_frozen_window")
    membership = PartitionMembership(
        **row.model_dump(include=set(KEY_FIELDS)),
        role="development_evaluation",
        label_knowledge_cutoff=plan.label_knowledge_cutoff,
        feature_row_sha256=canonical_sha256(row.model_dump(mode="json")),
    )
    # This is a pure qualification-rule adapter, never a development artifact
    # or development read. The persisted wire has only final_evaluation.
    outcome = qualify_evidence(
        evidence, membership, row, history, plan.features, plan.label_delay_days
    ).model_dump(mode="json")
    outcome["label"]["role"] = "final_evaluation"
    return FinalForecastExample.model_validate_json(
        canonical_bytes(
            {"key": row.model_dump(mode="json", include=set(KEY_FIELDS)), "outcome": outcome}
        )
    )


def _counts(counts: Counter[str], example: FinalForecastExample) -> None:
    outcome = example.outcome
    counts["rows"] += 1
    counts["eligible"] += int(outcome.eligible)
    counts["censored"] += int(outcome.label.status == "censored")
    counts["zero"] += int(outcome.label.observed_sales_units == 0)
    counts.update({"reason:" + r: 1 for r in outcome.reasons})


def _build_final_forecast(
    replay: PrivateSourceReplay, recipe: FinalForecastRecipe, output_root: Path
) -> Path:
    recipe = FinalForecastRecipe.model_validate_json(
        canonical_bytes(recipe.model_dump(mode="json"))
    )
    replay.check_parents()
    if replay.specification_sha256 != canonical_sha256(recipe.source.model_dump(mode="json")):
        raise SnapshotError("final_forecast_source_specification_mismatch")
    output_root = output_root.absolute()
    if output_root.is_relative_to(replay.curated.parent) or replay.curated.parent.is_relative_to(
        output_root
    ):
        raise SnapshotError("final_forecast_distinct_output_required")
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    checked_directory(output_root)
    plan = recipe.plan
    calendar = build_calendar(replay.curated, plan.origins)
    if calendar.descriptor.parent != recipe.source.parent:
        raise SnapshotError("final_forecast_calendar_parent_mismatch")
    with tempfile.TemporaryDirectory(prefix=".final-forecast-", dir=output_root) as temporary:
        staging = Path(temporary)
        features = build_feature_set(
            replay.curated, calendar, staging / "feature-build", plan.features
        )
        features.rename(staging / "features")
        (staging / "feature-build").rmdir()
        feature = verify_feature_set(staging / "features")
        if not 1 <= feature.descriptor.row_count <= plan.max_population_rows:
            raise SnapshotError("final_forecast_population_budget")
        with tempfile.TemporaryDirectory(prefix="ai09-final-index-") as scratch:
            with closing(_index(Path(scratch) / "data.sqlite", plan.max_index_bytes)) as db:
                versions = PhysicalVersionIndex(
                    db,
                    replay.curated,
                    replay.manifest,
                    maximum_rows=recipe.source.max_rows_per_parent,
                    maximum_bytes=plan.max_index_bytes,
                )
                try:
                    get_history = history_index(db, staging / "features")
                    db.execute("CREATE TABLE examples(key BLOB PRIMARY KEY, body BLOB)")
                    count = 0
                    for row in input_models(staging / "features", "features"):
                        if not isinstance(row, InputRow):
                            raise SnapshotError("final_forecast_feature_schema")
                        evidence = OutcomeEvidence(
                            key=ForecastKey.model_validate(row.model_dump(include=set(KEY_FIELDS))),
                            candidates=versions.candidates(row, plan.label_knowledge_cutoff),
                        )
                        example = _example(
                            row, get_history(row.history_context_sha256), evidence, recipe
                        )
                        raw = canonical_bytes(example.model_dump(mode="json"))
                        if len(raw) + 1 > plan.max_record_bytes:
                            raise SnapshotError("final_forecast_record_budget")
                        db.execute(
                            "INSERT INTO examples VALUES (?,?)",
                            (membership_key(row), zlib.compress(raw, level=1)),
                        )
                        count += 1
                        if count % 256 == 0:
                            check_index(db, plan.max_index_bytes)
                    if count != feature.descriptor.row_count:
                        raise SnapshotError("final_forecast_complete_population_mismatch")
                    check_index(db, plan.max_index_bytes)
                    total = _physical_bytes(staging, plan.max_artifact_bytes)
                    digest, keys, size = hashlib.sha256(), hashlib.sha256(), 0
                    counts: Counter[str] = Counter()
                    with (staging / "final_evaluation.jsonl").open("xb") as stream:
                        os.chmod(stream.name, 0o600)
                        for key, body in db.execute("SELECT key,body FROM examples ORDER BY key"):
                            raw = zlib.decompress(body)
                            example = FinalForecastExample.model_validate_json(raw)
                            if membership_key(example.key) != key:
                                raise SnapshotError("final_forecast_private_key_mismatch")
                            line = raw + b"\n"
                            size += len(line)
                            if total + size > plan.max_artifact_bytes:
                                raise SnapshotError("final_forecast_artifact_budget")
                            stream.write(line)
                            digest.update(line)
                            keys.update(key + b"\n")
                            _counts(counts, example)
                    descriptor = FinalForecastDescriptor(
                        recipe=recipe,
                        runtime=replay.runtime,
                        feature_set_id=feature.feature_set_id,
                        feature_descriptor=feature.descriptor,
                        population=_role_file(counts, size, digest.hexdigest(), keys.hexdigest()),
                        snapshot_inventory_sha256=replay.snapshot_inventory_sha256,
                        curated_inventory_sha256=replay.curated_inventory_sha256,
                        logical_curated_sha256=replay.logical_curated_sha256,
                        observation_rows=versions.observation_rows,
                        version_rows=versions.version_rows,
                        version_inventory_sha256=versions.version_inventory_sha256,
                    )
                finally:
                    versions.clear_cache()
        manifest = FinalForecastManifest(
            dataset_id="ai09-final-forecast-sha256-"
            + canonical_sha256(descriptor.model_dump(mode="json")),
            descriptor=descriptor,
        )
        (staging / "manifest.json").write_bytes(
            canonical_bytes(manifest.model_dump(mode="json")) + b"\n"
        )
        (staging / "manifest.json").chmod(0o600)
        verify_final_forecast(staging)
        replay.check_parents()
        fsync_tree(staging)
        destination = output_root / manifest.dataset_id
        try:
            publish_noreplace(staging, destination)
        except FileExistsError:
            if verify_final_forecast(destination) != manifest:
                raise SnapshotError("final_forecast_publication_conflict") from None
        replay.check_parents()
        return destination


def verify_final_forecast(root: Path) -> FinalForecastManifest:
    """Verify the stored complete population; source freshness still needs audit."""
    checked_directory(root)
    raw = read_bytes(root, "manifest.json", 512 * 1024)
    manifest = FinalForecastManifest.model_validate_json(raw)
    if raw != canonical_bytes(manifest.model_dump(mode="json")) + b"\n":
        raise SnapshotError("final_forecast_noncanonical_manifest")
    descriptor, recipe = manifest.descriptor, manifest.descriptor.recipe
    plan = recipe.plan
    feature = verify_feature_set(root / "features")
    if (
        feature.feature_set_id != descriptor.feature_set_id
        or feature.descriptor != descriptor.feature_descriptor
    ):
        raise SnapshotError("final_forecast_feature_parent_mismatch")
    names = {"manifest.json", "final_evaluation.jsonl"}
    names.update(
        p.relative_to(root).as_posix() for p in (root / "features").rglob("*") if p.is_file()
    )
    inventory(root, names)
    _physical_bytes(root, plan.max_artifact_bytes)
    if file_hash(root, "final_evaluation.jsonl") != (
        descriptor.population.size_bytes,
        descriptor.population.sha256,
    ):
        raise SnapshotError("final_forecast_population_checksum")
    with tempfile.TemporaryDirectory(prefix="ai09-final-verify-") as scratch:
        with closing(_index(Path(scratch) / "keys.sqlite", plan.max_index_bytes)) as db:
            db.execute(
                "CREATE TABLE features(key BLOB PRIMARY KEY,body BLOB,seen INTEGER DEFAULT 0)"
            )
            count = 0
            for row in input_models(root / "features", "features"):
                if not isinstance(row, InputRow):
                    raise SnapshotError("final_forecast_feature_schema")
                db.execute(
                    "INSERT INTO features(key,body) VALUES (?,?)",
                    (
                        membership_key(row),
                        zlib.compress(canonical_bytes(row.model_dump(mode="json")), level=1),
                    ),
                )
                count += 1
                if count % 256 == 0:
                    check_index(db, plan.max_index_bytes)
            get_history = history_index(db, root / "features")
            check_index(db, plan.max_index_bytes)
            digest, keys, size = hashlib.sha256(), hashlib.sha256(), 0
            counts: Counter[str] = Counter()
            previous: bytes | None = None
            with regular_file(root, "final_evaluation.jsonl") as stream:
                while line := stream.readline(plan.max_record_bytes + 1):
                    if len(line) > plan.max_record_bytes or not line.endswith(b"\n"):
                        raise SnapshotError("final_forecast_record_budget")
                    example = FinalForecastExample.model_validate_json(line)
                    if line != canonical_bytes(example.model_dump(mode="json")) + b"\n":
                        raise SnapshotError("final_forecast_noncanonical_example")
                    key = membership_key(example.key)
                    if previous is not None and key <= previous:
                        raise SnapshotError("final_forecast_key_order_or_duplicate")
                    previous = key
                    found = db.execute(
                        "SELECT body,seen FROM features WHERE key=?", (key,)
                    ).fetchone()
                    if found is None or found[1]:
                        raise SnapshotError("final_forecast_missing_or_duplicate_feature_key")
                    row = InputRow.model_validate_json(zlib.decompress(found[0]))
                    selected = example.outcome.label.selected_version
                    evidence = OutcomeEvidence(
                        key=example.key, candidates=(selected,) if selected else ()
                    )
                    if (
                        _example(row, get_history(row.history_context_sha256), evidence, recipe)
                        != example
                    ):
                        raise SnapshotError("final_forecast_feature_or_eligibility_mismatch")
                    db.execute("UPDATE features SET seen=1 WHERE key=?", (key,))
                    digest.update(line)
                    keys.update(key + b"\n")
                    size += len(line)
                    _counts(counts, example)
                    if counts["rows"] % 256 == 0:
                        check_index(db, plan.max_index_bytes)
            if (
                _role_file(counts, size, digest.hexdigest(), keys.hexdigest())
                != descriptor.population
            ):
                raise SnapshotError("final_forecast_coverage_mismatch")
            if (
                db.execute("SELECT 1 FROM features WHERE seen!=1 LIMIT 1").fetchone()
                or count != descriptor.feature_descriptor.row_count
            ):
                raise SnapshotError("final_forecast_incomplete_feature_population")
    return manifest
